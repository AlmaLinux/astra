import csv
import io
import logging
from collections.abc import Callable
from typing import Any, override
from urllib.parse import urlparse

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.utils import model_ngettext
from django.contrib.auth.models import Group as DjangoGroup
from django.contrib.auth.models import User as DjangoUser
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from import_export.admin import ImportMixin
from import_export.formats import base_formats
from python_freeipa import exceptions
from tablib import Dataset

from core.agreements import missing_required_agreements_for_user_in_group
from core.elections_services import (
    ElectionError,
    close_election,
    issue_voting_credentials_from_memberships,
    send_voting_credential_email,
    tally_election,
)
from core.form_validators import (
    clean_fas_discussion_url_value,
    clean_fas_irc_channels_value,
    clean_fas_mailing_list_value,
    clean_fas_url_value,
)
from core.ipa_user_attrs import _split_lines
from core.ipa_utils import sync_set_membership
from core.membership_csv_import import (
    _COLUMN_FIELDS,
    MembershipCSVConfirmImportForm,
    MembershipCSVImportForm,
    MembershipCSVImportResource,
)
from core.protected_resources import protected_freeipa_group_cns
from core.user_labels import user_choice, user_choice_with_fallback, user_choices_from_users
from core.views_utils import _normalize_str

from .backends import (
    FreeIPAFASAgreement,
    FreeIPAGroup,
    FreeIPAOperationFailed,
    FreeIPAUser,
    _invalidate_agreement_cache,
    _invalidate_agreements_list_cache,
    clear_current_viewer_username,
    set_current_viewer_username,
)
from .listbacked_queryset import _ListBackedQuerySet
from .models import (
    AuditLogEntry,
    Ballot,
    Candidate,
    Election,
    ExclusionGroup,
    ExclusionGroupCandidate,
    FreeIPAPermissionGrant,
    IPAFASAgreement,
    IPAGroup,
    IPAUser,
    Membership,
    MembershipCSVImportLink,
    MembershipType,
    MembershipTypeCategory,
    Organization,
    VotingCredential,
)

logger = logging.getLogger(__name__)




class FreeIPAModelAdmin(admin.ModelAdmin):
    """Common admin UX for FreeIPA-backed (unmanaged) models.

    Django's default admin flows assume DB-backed objects and a bulk delete
    action that can reliably report success. FreeIPA operations can fail per
    object (permissions, server-side constraints), so we provide:

    - Consistent error surfacing for change forms and deletes.
    - A custom bulk delete action that reports per-object failures.
    """

    # Keep a dedicated name for our implementation, but we map it to the
    # standard 'delete_selected' action key in get_actions() so users get the
    # usual Django confirmation flow and wording.
    actions = ("delete_selected_freeipa_objects",)

    # Subclasses should set this to the matching FreeIPA backend class, e.g.
    # FreeIPAUser / FreeIPAGroup / FreeIPAFASAgreement.
    freeipa_backend: Any | None = None

    class Media:
        js = (
            "core/js/admin_duallistbox_init.js",
        )

    @override
    def get_queryset(self, request) -> Any:
        backend = getattr(self, "freeipa_backend", None)
        if backend is None:
            return super().get_queryset(request)

        from_freeipa: Callable[[Any], Any] | None = getattr(self.model, "from_freeipa", None)
        if not callable(from_freeipa):
            raise RuntimeError(f"{self.model.__name__} must define a classmethod from_freeipa()")

        items = [from_freeipa(obj) for obj in backend.all()]
        return _ListBackedQuerySet(self.model, items)

    @override
    def get_object(self, request, object_id, from_field=None) -> Any:
        backend = getattr(self, "freeipa_backend", None)
        if backend is None:
            return super().get_object(request, object_id, from_field=from_field)

        # Prefer the list-backed queryset when available. This preserves
        # Django's normal admin semantics (and test fixtures that only stub
        # `.all()`), while still allowing a fallback for cases where the
        # object isn't present in the listing.
        obj = super().get_object(request, object_id, from_field=from_field)
        if obj is not None:
            return obj

        from_freeipa: Callable[[Any], Any] | None = getattr(self.model, "from_freeipa", None)
        if not callable(from_freeipa):
            raise RuntimeError(f"{self.model.__name__} must define a classmethod from_freeipa()")

        freeipa_obj = backend.get(object_id)
        if not freeipa_obj:
            return None
        return from_freeipa(freeipa_obj)

    @override
    def get_search_results(self, request, queryset, search_term) -> tuple[Any, bool]:
        # For DB-backed models, defer to Django.
        if not isinstance(queryset, _ListBackedQuerySet):
            return super().get_search_results(request, queryset, search_term)

        if not search_term or not getattr(self, "search_fields", None):
            return queryset, False

        term = search_term.lower()

        def matches(obj: Any) -> bool:
            for raw_field in self.search_fields:
                if not raw_field:
                    continue

                lookup = raw_field[0]
                if lookup in {"^", "=", "@"}:
                    field = raw_field[1:]
                else:
                    lookup = ""
                    field = raw_field

                try:
                    value = getattr(obj, field, "")
                except Exception:
                    continue
                if value is None:
                    continue

                text = str(value).lower()
                if lookup == "=":
                    if text == term:
                        return True
                elif lookup == "^":
                    if text.startswith(term):
                        return True
                else:
                    # Default to case-insensitive substring match.
                    if term in text:
                        return True
            return False

        return _ListBackedQuerySet(self.model, [o for o in queryset if matches(o)]), False

    @override
    def delete_model(self, request, obj) -> None:
        backend = getattr(self, "freeipa_backend", None)
        if backend is None:
            return super().delete_model(request, obj)

        object_id = self._object_key(obj)
        freeipa_obj = backend.get(object_id)
        if freeipa_obj:
            freeipa_obj.delete()

    @override
    def delete_queryset(self, request, queryset) -> None:
        backend = getattr(self, "freeipa_backend", None)
        if backend is None:
            return super().delete_queryset(request, queryset)

        # For our list-backed querysets, loop and call delete_model so we don't
        # rely on `_ListBackedQuerySet.delete()` (which is intentionally minimal
        # and not model-generic).
        for obj in list(queryset):
            self.delete_model(request, obj)

    @override
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None) -> Any:
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except FreeIPAOperationFailed as e:
            self.message_user(request, str(e), level=messages.ERROR)
            return HttpResponseRedirect(request.path)
        except Exception as e:
            logger.exception(
                "Unhandled exception in admin changeform_view model=%s object_id=%s",
                getattr(self.model, "__name__", "?"),
                object_id,
            )
            self.message_user(request, str(e), level=messages.ERROR)
            return HttpResponseRedirect(request.path)

    @override
    def delete_view(self, request, object_id, extra_context=None) -> Any:
        try:
            return super().delete_view(request, object_id, extra_context)
        except FreeIPAOperationFailed as e:
            self.message_user(request, str(e), level=messages.ERROR)
            return HttpResponseRedirect(self._changelist_url())
        except Exception as e:
            logger.exception(
                "Unhandled exception in admin delete_view model=%s object_id=%s",
                getattr(self.model, "__name__", "?"),
                object_id,
            )
            self.message_user(request, str(e), level=messages.ERROR)
            return HttpResponseRedirect(self._changelist_url())

    def _changelist_url(self) -> str:
        return reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")

    @override
    def get_actions(self, request):
        actions = super().get_actions(request)
        # Replace Django's built-in bulk delete action with our FreeIPA-aware
        # implementation, but keep the standard action key so Django renders the
        # usual confirmation page and UI label.
        actions.pop("delete_selected", None)

        # Move our action (generated by super().get_actions) to the standard
        # key. The action tuple contains the unbound method which Django can
        # call as func(modeladmin, request, queryset).
        freeipa_action = actions.pop("delete_selected_freeipa_objects", None)
        func = freeipa_action[0] if freeipa_action else type(self).delete_selected_freeipa_objects

        description = _("Delete selected %(verbose_name_plural)s") % {
            "verbose_name_plural": self.model._meta.verbose_name_plural,
        }
        actions["delete_selected"] = (func, "delete_selected", description)
        return actions

    def _object_key(self, obj) -> str:
        for attr in ("pk", "username", "cn"):
            value = getattr(obj, attr, None)
            if value:
                return str(value)
        return str(obj)

    def delete_selected_freeipa_objects(self, request, queryset):
        """Delete action with Django's confirmation flow + per-object failures.

        Mirrors Django's built-in delete action UX (confirmation page), but when
        confirmed it deletes objects one-by-one so we can surface partial failures
        instead of reporting a misleading blanket success.
        """

        opts = self.model._meta
        app_label = opts.app_label

        (
            deletable_objects,
            model_count,
            perms_needed,
            protected,
        ) = self.get_deleted_objects(queryset, request)

        if request.POST.get("post") and not protected:
            if perms_needed:
                raise PermissionDenied

            deleted_keys: list[str] = []
            deleted_objects: list[object] = []
            failed: dict[str, str] = {}

            for obj in list(queryset):
                key = self._object_key(obj)
                try:
                    self.delete_model(request, obj)
                    deleted_keys.append(key)
                    deleted_objects.append(obj)
                except Exception as e:
                    failed[key] = str(e)
                    logger.exception(
                        "Failed to delete FreeIPA-backed object model=%s key=%s",
                        getattr(self.model, "__name__", "?"),
                        key,
                    )

            # Django's log_deletions() is batch-oriented and is the supported API
            # in this Django version (there is no per-object log_deletion).
            # This should not convert successful deletes into failures.
            if deleted_objects:
                try:
                    # ContentType caches can survive across test DB setup and
                    # unmanaged models (especially with custom app_label) may
                    # not have ContentType rows until first use.
                    ContentType.objects.clear_cache()
                    ContentType.objects.get_for_model(self.model, for_concrete_model=False)
                    log_deletions = getattr(self, "log_deletions", None)
                    if callable(log_deletions):
                        log_deletions(request, _ListBackedQuerySet(self.model, deleted_objects))
                except Exception:
                    logger.exception(
                        "Failed to write admin deletion LogEntry rows model=%s count=%s",
                        getattr(self.model, "__name__", "?"),
                        len(deleted_objects),
                    )

            if deleted_keys:
                n = len(deleted_keys)
                self.message_user(
                    request,
                    _("Successfully deleted %(count)d %(items)s.")
                    % {"count": n, "items": model_ngettext(self.opts, n)},
                    messages.SUCCESS,
                )

            if failed:
                details = "; ".join(f"{k}: {v}" for k, v in sorted(failed.items()))
                self.message_user(
                    request,
                    f"Failed to delete {len(failed)} object(s): {details}",
                    messages.ERROR,
                )

            # Return None to display the change list page again.
            return None

        objects_name = model_ngettext(queryset)
        if perms_needed or protected:
            title = _("Cannot delete %(name)s") % {"name": objects_name}
        else:
            title = _("Delete multiple objects")

        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "subtitle": None,
            "objects_name": str(objects_name),
            "deletable_objects": [deletable_objects],
            "model_count": dict(model_count).items(),
            "queryset": queryset,
            "perms_lacking": perms_needed,
            "protected": protected,
            "opts": opts,
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "media": self.media,
        }

        request.current_app = self.admin_site.name

        return TemplateResponse(
            request,
            self.delete_selected_confirmation_template
            or [
                f"admin/{app_label}/{opts.model_name}/delete_selected_confirmation.html",
                f"admin/{app_label}/delete_selected_confirmation.html",
                "admin/delete_selected_confirmation.html",
            ],
            context,
        )


def _safe_reregister(model: type, admin_cls: type[admin.ModelAdmin]) -> None:
    """Unregister a model from admin (if registered) and re-register with a new admin class."""
    from django.contrib.admin.sites import NotRegistered
    try:
        admin.site.unregister(model)
    except NotRegistered:
        pass
    admin.site.register(model, admin_cls)


class ReadOnlyModelAdmin(admin.ModelAdmin):
    """Mixin that disables add/change/delete permissions."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def _override_post_office_log_admin():
    """Disable manual creation of django-post-office Log rows in admin.

    This is a temporary local workaround until https://github.com/ui/django-post_office/pull/503 is merged and released.
    """

    try:
        from post_office.admin import LogAdmin as PostOfficeLogAdmin
        from post_office.models import Log
    except Exception:
        return

    class ReadOnlyAddLogAdmin(ReadOnlyModelAdmin, PostOfficeLogAdmin):
        pass

    _safe_reregister(Log, ReadOnlyAddLogAdmin)


_override_post_office_log_admin()


def _override_post_office_email_admin() -> None:
    """Make django-post-office Email change view resilient to SMTP issues.

    django-post-office's EmailAdmin.get_fieldsets() eagerly renders the email
    preview by calling obj.email_message(), which opens an SMTP connection.
    In dev we intentionally allow flaky SMTP (MailHog jim-*), so the admin
    page should not 500 if SMTP disconnects.
    """

    try:
        from post_office.admin import EmailAdmin as PostOfficeEmailAdmin
        from post_office.models import Email
    except Exception:
        return

    class SafeEmailAdmin(PostOfficeEmailAdmin):
        @override
        def get_fieldsets(self, request, obj=None):
            try:
                return super().get_fieldsets(request, obj=obj)
            except Exception as e:
                messages.warning(request, f"Unable to render email preview: {e}")

                fields: list[object] = [
                    "from_email",
                    "to",
                    "cc",
                    "bcc",
                    "priority",
                    ("status", "scheduled_time"),
                ]
                if obj is not None and obj.message_id:
                    fields.insert(0, "message_id")
                return [(None, {"fields": fields})]

    _safe_reregister(Email, SafeEmailAdmin)


_override_post_office_email_admin()


def _override_django_ses_admin():
    """Register django-ses models with sensible admin defaults.

    Note: Django admin LogEntry writes require a DB-backed user row.
    This project authenticates via FreeIPA, but the middleware
    `core.middleware_admin_log.AdminShadowUserLogEntryMiddleware` provides a
    minimal DB "shadow user" in /admin/ so auditing works normally.
    """

    try:
        from django_ses.models import BlacklistedEmail, SESStat
    except Exception:
        return

    class SESStatAdmin(ReadOnlyModelAdmin):
        list_display = ("date", "delivery_attempts", "bounces", "complaints", "rejects")
        ordering = ("-date",)

    class BlacklistedEmailAdmin(admin.ModelAdmin):
        list_display = ("email",)
        search_fields = ("email",)
        ordering = ("email",)

    _safe_reregister(SESStat, SESStatAdmin)
    _safe_reregister(BlacklistedEmail, BlacklistedEmailAdmin)


_override_django_ses_admin()


def _duallistbox_widget(*, size: int = 14) -> forms.SelectMultiple:
    """Reusable dual-list-box widget used across FreeIPA admin forms."""
    return forms.SelectMultiple(attrs={"class": "form-control alx-duallistbox", "size": size})


class FreeIPAFormMixin:
    """Mixin for FreeIPA-backed ModelForms that have no DB table.

    Suppresses Django's DB-backed uniqueness checks — FreeIPA enforces
    uniqueness on its own.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._validate_unique = False

    def validate_unique(self):
        return


def _merge_group_choices(
    field: forms.Field,
    current_values: list[str],
    base_names: list[str],
) -> None:
    """Ensure *current_values* are selectable in a simple (name, name) choice field."""
    existing = dict(field.choices)
    missing = [v for v in current_values if v not in existing]
    if missing:
        field.choices = [(g, g) for g in (base_names + missing)]


def _merge_user_choices(
    field: forms.Field,
    current_values: list[str],
    base_usernames: list[str],
    users_by_username: dict[str, FreeIPAUser],
) -> None:
    """Ensure *current_values* are selectable in a user choice field with display labels."""
    existing = dict(field.choices)
    missing = [u for u in current_values if u not in existing]
    if missing:
        merged = sorted(set([*base_usernames, *missing]), key=str.lower)
        field.choices = [
            user_choice_with_fallback(u, users_by_username)
            for u in merged
        ]


def _freeipa_user_data() -> tuple[list[str], dict[str, FreeIPAUser]]:
    """Return sorted usernames and username→FreeIPAUser mapping from FreeIPA."""
    users = FreeIPAUser.all()
    by_username: dict[str, FreeIPAUser] = {u.username: u for u in users if u.username}
    return sorted(by_username, key=str.lower), by_username


def _freeipa_group_names(*, fas_only: bool = False) -> list[str]:
    """Return sorted FreeIPA group CN list, optionally restricted to FAS groups."""
    groups = FreeIPAGroup.all()
    if fas_only:
        return sorted(
            {getattr(g, "cn", "") for g in groups if getattr(g, "cn", "") and bool(getattr(g, "fas_group", False))}
        )
    return sorted({getattr(g, "cn", "") for g in groups if getattr(g, "cn", "")})


class IPAUserBaseForm(FreeIPAFormMixin, forms.ModelForm):
    groups = forms.MultipleChoiceField(
        required=False,
        widget=_duallistbox_widget(),
        help_text="Select the FreeIPA groups this user should be a member of.",
    )

    class Meta:
        model = IPAUser
        fields = ("username", "first_name", "last_name", "email", "is_active")

    @override
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        group_names = _freeipa_group_names()
        self.fields["groups"].choices = [(name, name) for name in group_names]

        # Username is immutable in FreeIPA.
        if self.instance and getattr(self.instance, "username", None):
            self.fields["username"].disabled = True

        username = getattr(self.instance, "username", None)
        if username:
            freeipa = FreeIPAUser.get(username)
            if freeipa:
                self.initial.setdefault("first_name", freeipa.first_name or "")
                self.initial.setdefault("last_name", freeipa.last_name or "")
                self.initial.setdefault("email", freeipa.email or "")
                self.initial.setdefault("is_active", freeipa.is_active)
                current = sorted(freeipa.direct_groups_list)
                # If the server returns groups outside our enumerated list,
                # keep them selectable so we don't drop memberships on save.
                _merge_group_choices(self.fields["groups"], current, group_names)
                self.initial.setdefault("groups", current)


class IPAUserAddForm(IPAUserBaseForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Set only when creating a user.",
    )


class IPAUserChangeForm(IPAUserBaseForm):
    pass


class IPAGroupForm(FreeIPAFormMixin, forms.ModelForm):
    members = forms.MultipleChoiceField(
        required=False,
        widget=_duallistbox_widget(),
        help_text="Select the users that should be members of this group.",
    )
    sponsors = forms.MultipleChoiceField(
        required=False,
        widget=_duallistbox_widget(),
        help_text="Select the users that should be sponsors (memberManager) of this group.",
    )
    member_groups = forms.MultipleChoiceField(
        required=False,
        widget=_duallistbox_widget(),
        help_text="Select the groups that should be nested members of this group.",
        label="Member groups",
    )
    sponsor_groups = forms.MultipleChoiceField(
        required=False,
        widget=_duallistbox_widget(),
        help_text="Select the groups that should be sponsors (memberManager) of this group.",
        label="Sponsor groups",
    )
    fas_url = forms.CharField(
        required=False,
        label="FAS URL",
        help_text="Fedora Account System URL for this group",
        widget=forms.URLInput(attrs={"placeholder": "https://…"}),
    )
    fas_mailing_list = forms.CharField(
        required=False,
        label="FAS Mailing List",
        help_text="Fedora Account System mailing list for this group",
        widget=forms.EmailInput(attrs={"placeholder": "group@lists.example.org"}),
    )
    fas_irc_channels = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "data-chat-channels-editor": "1"}),
        help_text="One per line (or comma-separated).",
        label="FAS IRC Channels",
    )
    fas_discussion_url = forms.CharField(
        required=False,
        label="FAS Discussion URL",
        help_text="Fedora Account System discussion URL for this group",
        widget=forms.URLInput(attrs={"placeholder": "https://…"}),
    )

    fas_group = forms.BooleanField(
        required=False,
        label="FAS Group",
        help_text="Enable or disable the fasGroup objectClass for this group (controls FAS attribute support)",
    )

    class Meta:
        model = IPAGroup
        fields = ("cn", "description", "fas_url", "fas_mailing_list", "fas_discussion_url", "fas_group")

    @override
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        usernames, users_by_username = _freeipa_user_data()
        self.fields["members"].choices = user_choices_from_users(usernames, users_by_username=users_by_username)
        self.fields["sponsors"].choices = user_choices_from_users(usernames, users_by_username=users_by_username)

        group_names = _freeipa_group_names()
        self.fields["member_groups"].choices = [(g, g) for g in group_names]
        self.fields["sponsor_groups"].choices = [(g, g) for g in group_names]

        # Group name is immutable in FreeIPA.
        if self.instance and getattr(self.instance, "cn", None):
            self.fields["cn"].disabled = True

        cn = getattr(self.instance, "cn", None)
        if cn:
            freeipa = FreeIPAGroup.get(cn)
            if freeipa:
                self.initial.setdefault("description", freeipa.description or "")
                current = sorted(freeipa.members)
                # Groups can contain entries that aren't part of the standard user listing.
                _merge_user_choices(self.fields["members"], current, usernames, users_by_username)
                self.initial.setdefault("members", current)

                current_sponsors = sorted(freeipa.sponsors)
                _merge_user_choices(self.fields["sponsors"], current_sponsors, usernames, users_by_username)
                self.initial.setdefault("sponsors", current_sponsors)

                current_member_groups = sorted(getattr(freeipa, "member_groups", []) or [])
                _merge_group_choices(self.fields["member_groups"], current_member_groups, group_names)
                self.initial.setdefault("member_groups", current_member_groups)

                current_sponsor_groups = sorted(getattr(freeipa, "sponsor_groups", []) or [])
                _merge_group_choices(self.fields["sponsor_groups"], current_sponsor_groups, group_names)
                self.initial.setdefault("sponsor_groups", current_sponsor_groups)
                self.initial.setdefault("fas_url", freeipa.fas_url or "")
                self.initial.setdefault("fas_mailing_list", freeipa.fas_mailing_list or "")
                self.initial.setdefault("fas_irc_channels", "\n".join(sorted(freeipa.fas_irc_channels)))
                self.initial.setdefault("fas_discussion_url", freeipa.fas_discussion_url or "")
                self.initial.setdefault("fas_group", freeipa.fas_group)
            # `fas_group` is a creation-time property; disallow toggling on edit.
            self.fields["fas_group"].disabled = True

    def clean_fas_url(self) -> str:
        return clean_fas_url_value(self.cleaned_data.get("fas_url", ""))

    def clean_fas_discussion_url(self) -> str:
        return clean_fas_discussion_url_value(self.cleaned_data.get("fas_discussion_url", ""))

    def clean_fas_mailing_list(self) -> str:
        return clean_fas_mailing_list_value(self.cleaned_data.get("fas_mailing_list"))

    def clean_fas_irc_channels(self) -> str:
        channels = clean_fas_irc_channels_value(self.cleaned_data.get("fas_irc_channels"))
        # Admin stores as newline-joined string; keep stable ordering for diffs.
        deduped = sorted(set(channels), key=str.lower)
        return "\n".join(deduped)


class IPAFASAgreementForm(FreeIPAFormMixin, forms.ModelForm):
    groups = forms.MultipleChoiceField(
        required=False,
        widget=_duallistbox_widget(),
        help_text="Select the FreeIPA groups this agreement applies to.",
    )
    users = forms.MultipleChoiceField(
        required=False,
        widget=_duallistbox_widget(),
        help_text="Select the users who have consented to this agreement.",
    )
    enabled = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Enable or disable this agreement.",
    )

    class Meta:
        model = IPAFASAgreement
        fields = ("cn", "description", "enabled")

    @override
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "description" in self.fields:
            self.fields["description"].label = "Text"

        group_names = _freeipa_group_names(fas_only=True)
        self.fields["groups"].choices = [(name, name) for name in group_names]

        usernames, users_by_username = _freeipa_user_data()
        self.fields["users"].choices = user_choices_from_users(usernames, users_by_username=users_by_username)

        # Agreement name is immutable in FreeIPA.
        if self.instance and getattr(self.instance, "cn", None):
            self.fields["cn"].disabled = True

        cn = getattr(self.instance, "cn", None)
        if cn:
            freeipa = FreeIPAFASAgreement.get(cn)
            if freeipa:
                self.initial.setdefault("description", freeipa.description)
                self.initial.setdefault("enabled", freeipa.enabled)

                current_groups = sorted(freeipa.groups)
                _merge_group_choices(self.fields["groups"], current_groups, group_names)
                self.initial.setdefault("groups", current_groups)

                current_users = sorted(freeipa.users)
                _merge_user_choices(self.fields["users"], current_users, usernames, users_by_username)
                self.initial.setdefault("users", current_users)


@admin.register(IPAUser)
class IPAUserAdmin(FreeIPAModelAdmin):
    form = IPAUserChangeForm
    freeipa_backend = FreeIPAUser
    list_display = ("username", "displayname", "email", "is_active", "is_staff")
    ordering = ("username",)
    search_fields = ("username", "displayname", "first_name", "last_name", "email")
    readonly_fields = ()
    change_form_template = "admin/core/ipauser/change_form.html"
    change_list_template = "admin/core/ipauser/change_list.html"

    @override
    def get_queryset(self, request):
        clear_current_viewer_username()
        return super().get_queryset(request)

    @override
    def get_object(self, request, object_id, from_field=None):
        clear_current_viewer_username()
        return super().get_object(request, object_id, from_field=from_field)

    @override
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None) -> Any:
        extra_context = dict(extra_context or {})

        has_otp_tokens = False
        has_email = False
        if object_id is not None:
            obj = self.get_object(request, object_id)
            if obj is not None:
                username = _normalize_str(obj.username)
                has_email = bool(_normalize_str(obj.email))
                if username:
                    try:
                        client = FreeIPAUser.get_client()
                        res = client.otptoken_find(o_ipatokenowner=username, o_all=True)
                        tokens = res.get("result", []) if isinstance(res, dict) else []
                        has_otp_tokens = bool(tokens)
                    except exceptions.NotFound:
                        has_otp_tokens = False
                    except AttributeError:
                        # Some client versions/environments may not expose the OTP API.
                        has_otp_tokens = False
                    except Exception:
                        logger.debug("Admin OTP token lookup failed username=%s", username, exc_info=True)
                        has_otp_tokens = False

        extra_context["has_otp_tokens"] = has_otp_tokens
        extra_context["has_email"] = has_email
        return super().changeform_view(request, object_id, form_url, extra_context)

    @override
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/send-password-reset/",
                self.admin_site.admin_view(self.send_password_reset_email_view),
                name="auth_ipauser_send_password_reset",
            ),
            path(
                "<path:object_id>/disable-otp-tokens/",
                self.admin_site.admin_view(self.disable_otp_tokens_view),
                name="auth_ipauser_disable_otp_tokens",
            ),
        ]
        return custom + urls

    def send_password_reset_email_view(self, request, object_id: str):
        obj = self.get_object(request, object_id)
        if obj is None:
            raise PermissionDenied
        if not self.has_change_permission(request, obj=obj):
            raise PermissionDenied

        email = _normalize_str(getattr(obj, "email", ""))
        username = _normalize_str(getattr(obj, "username", ""))

        if request.method != "POST" or not request.POST.get("post"):
            return HttpResponseRedirect(reverse("admin:auth_ipauser_change", args=[object_id]))

        if not email:
            self.message_user(
                request,
                "User has no email address; cannot send password reset.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:auth_ipauser_change", args=[object_id]))

        from core.password_reset import send_password_reset_email

        try:
            freeipa = FreeIPAUser.get(username)
            last_password_change = _normalize_str(freeipa.last_password_change) if freeipa else ""
            send_password_reset_email(
                request=request,
                username=username,
                email=email,
                last_password_change=last_password_change,
            )
        except Exception as e:
            logger.exception("Admin password reset email send failed username=%s", username)
            self.message_user(request, f"Failed to send password reset email: {e}", level=messages.ERROR)
        else:
            self.message_user(request, "Password reset email queued.", level=messages.SUCCESS)

        return HttpResponseRedirect(reverse("admin:auth_ipauser_change", args=[object_id]))

    def disable_otp_tokens_view(self, request, object_id: str):
        obj = self.get_object(request, object_id)
        if obj is None:
            raise PermissionDenied
        if not self.has_change_permission(request, obj=obj):
            raise PermissionDenied

        username = _normalize_str(obj.username)
        if not username:
            raise PermissionDenied

        def _token_id(token: object) -> str:
            if not isinstance(token, dict):
                return ""
            token_id = token.get("ipatokenuniqueid")
            if isinstance(token_id, list):
                return _normalize_str(token_id[0]) if token_id else ""
            return _normalize_str(token_id)

        def _token_disabled(token: object) -> bool:
            if not isinstance(token, dict):
                return False
            raw = token.get("ipatokendisabled")
            if isinstance(raw, list) and raw:
                return bool(raw[0])
            return bool(raw)

        if request.method != "POST" or not request.POST.get("post"):
            return HttpResponseRedirect(reverse("admin:auth_ipauser_change", args=[object_id]))

        try:
            client = FreeIPAUser.get_client()
            res = client.otptoken_find(o_ipatokenowner=username, o_all=True)
            raw_tokens = res.get("result", []) if isinstance(res, dict) else []
            tokens = [
                {
                    "id": _token_id(t),
                    "disabled": _token_disabled(t),
                }
                for t in raw_tokens
                if _token_id(t)
            ]
        except Exception as e:
            logger.exception("Admin OTP token lookup failed username=%s", username)
            self.message_user(request, f"Failed to look up OTP tokens: {e}", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:auth_ipauser_change", args=[object_id]))

        if not tokens:
            self.message_user(request, f"No OTP tokens found for {username}.", level=messages.INFO)
            return HttpResponseRedirect(reverse("admin:auth_ipauser_change", args=[object_id]))

        try:
            client = FreeIPAUser.get_client()
            for token in tokens:
                client.otptoken_mod(a_ipatokenuniqueid=token["id"], o_ipatokendisabled=True)
        except Exception as e:
            logger.exception("Admin OTP disable failed username=%s", username)
            self.message_user(request, f"Failed to disable OTP tokens: {e}", level=messages.ERROR)
        else:
            self.message_user(
                request,
                f"Disabled {len(tokens)} OTP token(s) for {username}.",
                level=messages.SUCCESS,
            )

        return HttpResponseRedirect(reverse("admin:auth_ipauser_change", args=[object_id]))

    @override
    def get_form(self, request, obj=None, change=False, **kwargs):
        defaults: dict[str, Any] = {"form": IPAUserChangeForm if obj else IPAUserAddForm}
        defaults.update(kwargs)
        return super().get_form(request, obj=obj, change=change, **defaults)

    @override
    def save_model(self, request, obj, form, change):
        clear_current_viewer_username()
        username = form.cleaned_data.get("username") or getattr(obj, "username", None)
        if not username:
            return

        desired_groups = set(form.cleaned_data.get("groups") or [])
        password = form.cleaned_data.get("password")

        if not change:
            freeipa = FreeIPAUser.create(
                username,
                first_name=form.cleaned_data.get("first_name") or "",
                last_name=form.cleaned_data.get("last_name") or "",
                email=form.cleaned_data.get("email") or "",
                password=password or None,
            )
        else:
            freeipa = FreeIPAUser.get(username)
            if not freeipa:
                return
            freeipa.first_name = form.cleaned_data.get("first_name") or ""
            freeipa.last_name = form.cleaned_data.get("last_name") or ""
            freeipa.email = form.cleaned_data.get("email") or ""
            freeipa.is_active = bool(form.cleaned_data.get("is_active"))
            freeipa.save()

        def _check_agreements(g: str) -> None:
            missing = missing_required_agreements_for_user_in_group(username, g)
            if missing:
                raise FreeIPAOperationFailed(
                    f"Cannot add user '{username}' to group '{g}' until they have signed: {', '.join(missing)}"
                )

        sync_set_membership(
            desired_groups,
            set(freeipa.direct_groups_list),
            freeipa.add_to_group,
            freeipa.remove_from_group,
            pre_add_check=_check_agreements,
        )

@admin.register(IPAGroup)
class IPAGroupAdmin(FreeIPAModelAdmin):
    form = IPAGroupForm
    freeipa_backend = FreeIPAGroup
    list_display = ("cn", "description", "fas_group", "fas_url", "fas_mailing_list")
    ordering = ("cn",)
    search_fields = ("cn", "description")
    change_form_template = "admin/core/ipagroup/change_form.html"

    @override
    def changeform_view(self, request: HttpRequest, object_id: str | None = None, form_url: str = "", extra_context: dict[str, object] | None = None):
        extra = extra_context or {}
        extra["chat_networks"] = settings.CHAT_NETWORKS
        return super().changeform_view(request, object_id=object_id, form_url=form_url, extra_context=extra)

    @override
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/download-emails/",
                self.admin_site.admin_view(self.download_emails_view),
                name="auth_ipagroup_download_emails",
            ),
        ]
        return custom + urls

    def download_emails_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        obj = self.get_object(request, object_id)
        if obj is None:
            raise Http404
        if not self.has_change_permission(request, obj=obj):
            raise PermissionDenied

        group_cn = _normalize_str(obj.pk)
        if not group_cn:
            raise Http404

        group = FreeIPAGroup.get(group_cn)
        if group is None:
            raise Http404

        usernames: set[str] = set()
        try:
            usernames |= set(group.member_usernames_recursive())
        except Exception:
            logger.exception("Failed to expand member usernames group=%s", group_cn)
            usernames |= set(group.members)

        usernames |= set(group.sponsors)

        for sponsor_group_cn in group.sponsor_groups:
            sponsor_group = FreeIPAGroup.get(sponsor_group_cn)
            if sponsor_group is None:
                continue
            try:
                usernames |= set(sponsor_group.member_usernames_recursive())
            except Exception:
                logger.exception(
                    "Failed to expand sponsor group member usernames group=%s sponsor_group=%s",
                    group_cn,
                    sponsor_group_cn,
                )
                usernames |= set(sponsor_group.members)

        request_username = _normalize_str(getattr(request.user, "username", ""))

        # This export is an admin-only operation. Admins must be able to export
        # contact data for operational reasons, even when a user opted into a
        # private profile.
        clear_current_viewer_username()
        try:
            emails: set[str] = set()
            for username in sorted(usernames, key=str.lower):
                user = FreeIPAUser.get(username)
                if user is None:
                    continue
                email = _normalize_str(user.email).lower()
                if email:
                    emails.add(email)
        finally:
            if request_username:
                set_current_viewer_username(request_username)
            else:
                clear_current_viewer_username()

        buf = io.StringIO(newline="")
        writer = csv.writer(buf)
        writer.writerow(["email"])
        for email in sorted(emails):
            writer.writerow([email])

        resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{group_cn}_emails.csv"'
        return resp

    @staticmethod
    def _sync_fas_group_attributes(
        freeipa: FreeIPAGroup,
        *,
        description: str = "",
        fas_url: str = "",
        fas_mailing_list: str = "",
        fas_irc_channels: set[str] | None = None,
        fas_discussion_url: str = "",
        sync_description: bool = False,
    ) -> None:
        """Dirty-check and persist FAS attributes (+ optionally description) on a FreeIPA group."""
        changed = False
        if sync_description and freeipa.description != description:
            freeipa.description = description
            changed = True
        if bool(getattr(freeipa, "fas_group", False)):
            irc_list = sorted(fas_irc_channels) if fas_irc_channels else []
            if (freeipa.fas_url or "") != (fas_url or ""):
                freeipa.fas_url = fas_url or None
                changed = True
            if (freeipa.fas_mailing_list or "") != (fas_mailing_list or ""):
                freeipa.fas_mailing_list = fas_mailing_list or None
                changed = True
            if sorted(freeipa.fas_irc_channels or []) != irc_list:
                freeipa.fas_irc_channels = irc_list
                changed = True
            if (freeipa.fas_discussion_url or "") != (fas_discussion_url or ""):
                freeipa.fas_discussion_url = fas_discussion_url or None
                changed = True
        if changed:
            freeipa.save()

    @override
    def save_model(self, request, obj, form, change):
        cn = form.cleaned_data.get("cn") or getattr(obj, "cn", None)
        if not cn:
            return

        desired_members = set(form.cleaned_data.get("members") or [])
        desired_sponsors = set(form.cleaned_data.get("sponsors") or [])
        desired_member_groups = set(form.cleaned_data.get("member_groups") or [])
        desired_sponsor_groups = set(form.cleaned_data.get("sponsor_groups") or [])
        description = form.cleaned_data.get("description") or ""
        fas_url = form.cleaned_data.get("fas_url") or ""
        fas_mailing_list = form.cleaned_data.get("fas_mailing_list") or ""
        fas_irc_channels = set(_split_lines(form.cleaned_data.get("fas_irc_channels", "")))
        fas_discussion_url = form.cleaned_data.get("fas_discussion_url") or ""
        fas_group = form.cleaned_data.get("fas_group", False)

        if not change:
            freeipa = FreeIPAGroup.create(cn, description=description or None, fas_group=fas_group)
            # Enforce creation-time setting: if caller asked for a FAS-enabled
            # group but the FreeIPA server did not expose it immediately, fail
            # because toggling via group_mod is unsupported on some deployments.
            if fas_group:
                # Re-fetch authoritative state and verify.
                freeipa = FreeIPAGroup.get(cn)
                current_fas = bool(freeipa.fas_group)
                if not current_fas:
                    raise RuntimeError(
                        "FreeIPA server did not create a fasGroup at creation time; toggling is not supported"
                    )
            if freeipa is not None:
                self._sync_fas_group_attributes(
                    freeipa,
                    fas_url=fas_url,
                    fas_mailing_list=fas_mailing_list,
                    fas_irc_channels=fas_irc_channels,
                    fas_discussion_url=fas_discussion_url,
                )
        else:
            freeipa = FreeIPAGroup.get(cn)
            if not freeipa:
                return
            self._sync_fas_group_attributes(
                freeipa,
                description=description,
                fas_url=fas_url,
                fas_mailing_list=fas_mailing_list,
                fas_irc_channels=fas_irc_channels,
                fas_discussion_url=fas_discussion_url,
                sync_description=True,
            )

        # `fas_group` is a creation-time-only property. Do not attempt to
        # toggle it for existing groups via `group_mod` as many FreeIPA
        # deployments do not support that reliably. If an edit attempted to
        # change this value, ignore it and log at INFO level for visibility.
        if change:
            try:
                current_fas = bool(freeipa.fas_group)
                if fas_group != current_fas:
                    logger.info(
                        "Ignoring fas_group toggle for existing group %s: current=%s requested=%s",
                        cn,
                        current_fas,
                        fas_group,
                    )
            except Exception:
                pass

        def _check_group_agreements(u: str) -> None:
            missing = missing_required_agreements_for_user_in_group(u, cn)
            if missing:
                raise FreeIPAOperationFailed(
                    f"Cannot add user '{u}' to group '{cn}' until they have signed: {', '.join(missing)}"
                )

        sync_set_membership(
            desired_members, set(freeipa.members),
            freeipa.add_member, freeipa.remove_member,
            pre_add_check=_check_group_agreements,
        )
        sync_set_membership(
            desired_sponsors, set(freeipa.sponsors),
            freeipa.add_sponsor, freeipa.remove_sponsor,
        )
        sync_set_membership(
            desired_sponsor_groups, set(getattr(freeipa, "sponsor_groups", []) or []),
            freeipa.add_sponsor_group, freeipa.remove_sponsor_group,
        )
        sync_set_membership(
            desired_member_groups, set(getattr(freeipa, "member_groups", []) or []),
            freeipa.add_member_group, freeipa.remove_member_group,
        )

    @override
    def delete_model(self, request, obj) -> None:
        group_cn = str(obj.cn or obj.pk or "").strip()
        if group_cn and group_cn in protected_freeipa_group_cns():
            raise FreeIPAOperationFailed(f"Group {group_cn!r} is protected and cannot be deleted")
        return super().delete_model(request, obj)

@admin.register(IPAFASAgreement)
class IPAFASAgreementAdmin(FreeIPAModelAdmin):
    form = IPAFASAgreementForm
    freeipa_backend = FreeIPAFASAgreement
    list_display = ("cn", "enabled")
    ordering = ("cn",)
    search_fields = ("cn", "description")

    @override
    def save_model(self, request, obj, form, change):
        try:
            cn: str = form.cleaned_data.get("cn") or obj.cn
            description: str = _normalize_str(form.cleaned_data.get("description"))
            enabled: bool = form.cleaned_data.get("enabled", False)
            selected_groups = set(form.cleaned_data.get("groups") or [])
            selected_users = set(form.cleaned_data.get("users") or [])

            # Treat disabled agreements as non-applicable for group membership.
            # The FreeIPA plugin may still enforce agreements linked to groups,
            # so we remove group links when the agreement is disabled.
            if not enabled:
                selected_groups = set()

            if not change:
                freeipa = FreeIPAFASAgreement.create(cn, description=description or None)
            else:
                freeipa = FreeIPAFASAgreement.get(cn)
                if freeipa is None:
                    raise FreeIPAOperationFailed(f"Agreement not found after edit: {cn}")
                if freeipa.description != description:
                    freeipa.set_description(description or None)

            sync_set_membership(
                selected_groups, set(freeipa.groups),
                freeipa.add_group, freeipa.remove_group,
            )
            sync_set_membership(
                selected_users, set(freeipa.users),
                freeipa.add_user, freeipa.remove_user,
            )

            if freeipa.enabled != enabled:
                freeipa.set_enabled(enabled)

            _invalidate_agreement_cache(cn)
            _invalidate_agreements_list_cache()
        except FreeIPAOperationFailed:
            raise
        except Exception as e:
            logger.exception("Failed to save FAS agreement cn=%s", getattr(obj, "cn", None) or form.cleaned_data.get("cn"))
            raise FreeIPAOperationFailed(str(e))


@admin.register(MembershipTypeCategory)
class MembershipTypeCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_individual", "is_organization")
    ordering = ("sort_order", "name")


@admin.register(MembershipType)
class MembershipTypeAdmin(admin.ModelAdmin):
    class MembershipTypeAdminForm(forms.ModelForm):
        group_cn = forms.ChoiceField(
            required=False,
            label="Group",
            help_text="Optional: associate this membership type with a FreeIPA group.",
        )

        class Meta:
            model = MembershipType
            fields = "__all__"

        @override
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            group_names = _freeipa_group_names()

            current = (self.initial.get("group_cn") or "").strip()
            if not current and self.instance and self.instance.group_cn:
                current = str(self.instance.group_cn or "").strip()
            if current and current not in group_names:
                group_names.append(current)

            self.fields["group_cn"].choices = [("", "---------"), *[(name, name) for name in group_names]]

    form = MembershipTypeAdminForm

    list_display = (
        "code",
        "name",
        "group_cn",
        "category",
        "sort_order",
        "enabled",
    )
    list_filter = ("enabled", "category")
    ordering = ("sort_order", "code")
    search_fields = ("code", "name")

    @override
    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj=obj))
        if obj is not None and "code" not in readonly:
            readonly.append("code")
        return tuple(readonly)


class CandidateInline(admin.TabularInline):
    model = Candidate
    extra = 0
    fields = ("freeipa_username", "nominated_by", "url", "description")
    ordering = ("freeipa_username", "id")


@admin.register(Election)
class ElectionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "status",
        "url",
        "start_datetime",
        "end_datetime",
        "number_of_seats",
    )
    list_filter = ("status",)
    search_fields = ("name",)
    inlines = (CandidateInline,)
    actions = (
        "issue_credentials_from_memberships_action",
        "issue_and_email_credentials_from_memberships_action",
        "close_elections_action",
        "tally_elections_action",
    )

    def issue_credentials_from_memberships_action(self, request: HttpRequest, queryset) -> None:
        for election in queryset:
            try:
                affected = len(issue_voting_credentials_from_memberships(election=election))
            except ElectionError as exc:
                self.message_user(request, f"{election}: {exc}", level=messages.ERROR)
                continue
            self.message_user(
                request,
                f"{election}: issued/updated {affected} credential(s) from memberships.",
                level=messages.SUCCESS,
            )

    issue_credentials_from_memberships_action.short_description = "Issue credentials from memberships"  # type: ignore[attr-defined]

    def issue_and_email_credentials_from_memberships_action(self, request: HttpRequest, queryset) -> None:
        for election in queryset:
            try:
                credentials = issue_voting_credentials_from_memberships(election=election)
            except ElectionError as exc:
                self.message_user(request, f"{election}: {exc}", level=messages.ERROR)
                continue

            emailed = 0
            skipped = 0
            failures = 0
            for cred in credentials:
                username = str(cred.freeipa_username or "").strip()
                if not username:
                    skipped += 1
                    continue

                try:
                    user = FreeIPAUser.get(username)
                except Exception:
                    failures += 1
                    continue

                if user is None or not user.email:
                    skipped += 1
                    continue

                try:
                    send_voting_credential_email(
                        request=request,
                        election=election,
                        username=username,
                        email=user.email,
                        credential_public_id=str(cred.public_id),
                    )
                except Exception:
                    failures += 1
                    continue
                emailed += 1

            if emailed:
                self.message_user(
                    request,
                    f"{election}: emailed {emailed} credential(s).",
                    level=messages.SUCCESS,
                )
            if skipped:
                self.message_user(
                    request,
                    f"{election}: skipped {skipped} credential(s) (missing username/email).",
                    level=messages.WARNING,
                )
            if failures:
                self.message_user(
                    request,
                    f"{election}: failed to email {failures} credential(s).",
                    level=messages.ERROR,
                )

    issue_and_email_credentials_from_memberships_action.short_description = (
        "Issue credentials from memberships and email voters"
    )  # type: ignore[attr-defined]

    def _run_election_action(self, request: HttpRequest, queryset, *, action_fn: Callable, success_verb: str) -> None:
        """Shared boilerplate for election admin actions that apply a function per election."""
        for election in queryset:
            try:
                action_fn(election=election)
            except ElectionError as exc:
                self.message_user(request, f"{election}: {exc}", level=messages.ERROR)
                continue
            self.message_user(request, f"{election}: {success_verb}.", level=messages.SUCCESS)

    def close_elections_action(self, request: HttpRequest, queryset) -> None:
        self._run_election_action(request, queryset, action_fn=close_election, success_verb="closed")

    close_elections_action.short_description = "Close election(s)"  # type: ignore[attr-defined]

    def tally_elections_action(self, request: HttpRequest, queryset) -> None:
        self._run_election_action(request, queryset, action_fn=tally_election, success_verb="tallied")

    tally_elections_action.short_description = "Tally election(s)"  # type: ignore[attr-defined]


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ("freeipa_username", "nominated_by", "election")
    list_filter = ("election",)
    search_fields = ("freeipa_username", "nominated_by", "election__name")
    ordering = ("election", "freeipa_username", "id")


class ExclusionGroupCandidateInline(admin.TabularInline):
    model = ExclusionGroupCandidate
    extra = 0
    autocomplete_fields = ("candidate",)


@admin.register(ExclusionGroup)
class ExclusionGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "election", "max_elected")
    list_filter = ("election",)
    search_fields = ("name", "election__name")
    ordering = ("election", "name", "id")
    inlines = (ExclusionGroupCandidateInline,)


@admin.register(VotingCredential)
class VotingCredentialAdmin(admin.ModelAdmin):
    list_display = ("election", "public_id", "freeipa_username", "weight", "created_at")
    list_filter = ("election",)
    search_fields = ("public_id", "freeipa_username")
    ordering = ("-created_at", "id")


@admin.register(Ballot)
class BallotAdmin(ReadOnlyModelAdmin):
    list_display = ("election", "credential_public_id", "weight", "ballot_hash", "created_at")
    list_filter = ("election",)
    search_fields = ("credential_public_id", "ballot_hash")
    ordering = ("-created_at", "id")
    readonly_fields = ("election", "credential_public_id", "ranking", "weight", "ballot_hash", "created_at", "chain_hash", "previous_chain_hash")


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(ReadOnlyModelAdmin):
    list_display = ("election", "timestamp", "event_type", "is_public")
    list_filter = ("election", "is_public", "event_type")
    search_fields = ("event_type",)
    ordering = ("-timestamp", "id")
    readonly_fields = ("election", "timestamp", "event_type", "payload", "is_public")


@admin.register(MembershipCSVImportLink)
class MembershipCSVImportLinkAdmin(ImportMixin, admin.ModelAdmin):
    """Admin entry for the membership CSV importer (django-import-export)."""

    import_form_class = MembershipCSVImportForm
    confirm_form_class = MembershipCSVConfirmImportForm
    import_template_name = "admin/core/membership_csv_import.html"
    resource_classes = [MembershipCSVImportResource]

    @override
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    @override
    def get_import_formats(self) -> list[type[base_formats.Format]]:
        return [base_formats.CSV]

    @override
    def has_delete_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        return False

    @override
    def has_change_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        return False

    @override
    def has_view_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        # Keep it simple: if the user can access /admin/ at all, let them see
        # this shortcut entry.
        return bool(request.user.is_active and request.user.is_staff)

    @override
    def get_model_perms(self, request: HttpRequest) -> dict[str, bool]:
        # Ensure the model appears in the app list/sidebar.
        if not self.has_view_permission(request):
            return {}
        return {"view": True}

    @override
    def get_confirm_form_initial(self, request: HttpRequest, import_form: forms.Form) -> dict[str, Any]:
        initial = super().get_confirm_form_initial(request, import_form)

        # During the initial preview request, ImportMixin passes the validated
        # upload form here so we can persist custom fields into the hidden
        # confirmation form. During process_import(), this argument is None.
        if import_form is None:
            return initial

        membership_type = import_form.cleaned_data.get("membership_type")
        if membership_type is not None:
            initial["membership_type"] = membership_type.pk

        for key in _COLUMN_FIELDS:
            value = import_form.cleaned_data.get(key, "")
            if value:
                initial[key] = value

        for key, value in import_form.cleaned_data.items():
            if (
                isinstance(key, str)
                and key.startswith("q_")
                and key.endswith("_column")
                and value
            ):
                initial[key] = value

        if import_form.cleaned_data.get("enable_name_matching"):
            initial["enable_name_matching"] = "on"
        return initial

    @override
    def get_import_resource_kwargs(self, request: HttpRequest, **kwargs: Any) -> dict[str, Any]:
        form = kwargs.get("form")
        if form is None:
            raise ValueError("Missing import form")

        # ImportMixin.import_action() calls this even for the initial GET /import/
        # page, where the form is unbound and has not been validated.
        membership_type = None
        cleaned_data = getattr(form, "cleaned_data", None)
        if isinstance(cleaned_data, dict):
            membership_type = cleaned_data.get("membership_type")
        if membership_type is None:
            membership_type = (
                MembershipType.objects.enabled()
                .ordered_for_display()
                .first()
            )

        extra: dict[str, Any] = {}
        if isinstance(cleaned_data, dict):
            for key in _COLUMN_FIELDS:
                value = cleaned_data.get(key, "")
                if value:
                    extra[key] = value

            raw_enable = cleaned_data.get("enable_name_matching")
            if isinstance(raw_enable, bool):
                extra["enable_name_matching"] = raw_enable
            elif isinstance(raw_enable, str):
                extra["enable_name_matching"] = raw_enable.strip().lower() in {"1", "y", "yes", "true", "t", "on"}

        question_column_overrides: dict[str, str] = {}
        if isinstance(cleaned_data, dict):
            for key, value in cleaned_data.items():
                if (
                    isinstance(key, str)
                    and key.startswith("q_")
                    and key.endswith("_column")
                    and isinstance(value, str)
                    and value
                ):
                    question_column_overrides[key] = value
        if question_column_overrides:
            extra["question_column_overrides"] = question_column_overrides

        return {
            "membership_type": membership_type,
            "actor_username": request.user.get_username(),
            **extra,
        }

    @override
    def process_dataset(self, dataset: Any, confirm_form: forms.Form, request: HttpRequest, **kwargs: Any) -> Any:
        cleaned_data = getattr(confirm_form, "cleaned_data", None)
        selected_raw = cleaned_data.get("selected_row_numbers") if isinstance(cleaned_data, dict) else ""
        selected_numbers: list[int] = []
        if isinstance(selected_raw, str):
            for part in selected_raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    selected_numbers.append(int(part))
                except ValueError:
                    continue

        # If selection is blank (or invalid), keep default behavior.
        if not selected_numbers:
            return super().process_dataset(dataset, confirm_form, request, **kwargs)

        # import-export row numbers are 1-based; Dataset rows are 0-based.
        try:
            headers = list(dataset.headers or [])
        except Exception:
            headers = []

        filtered = Dataset(headers=headers)
        for n in sorted(set(selected_numbers)):
            if n <= 0:
                continue
            idx = n - 1
            try:
                filtered.append(dataset[idx])
            except Exception:
                continue

        return super().process_dataset(filtered, confirm_form, request, **kwargs)

    @override
    def import_action(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        response = super().import_action(request, *args, **kwargs)
        if isinstance(response, TemplateResponse):
            result = response.context_data.get("result") if response.context_data else None
            unmatched_url = getattr(result, "unmatched_download_url", "") if result is not None else ""
            if unmatched_url:
                response.context_data["unmatched_download_url"] = unmatched_url
                parsed = urlparse(unmatched_url)
                token = parsed.path.rstrip("/").split("/")[-1] if parsed.path else ""
                if token:
                    cache_key = f"membership-import-unmatched:{token}"
                    content = cache.get(cache_key)
                    if content is None and result is not None:
                        # Optional attribute supplied by the import resource.
                        content = getattr(result, "unmatched_csv_content", None)
                    if content is not None:
                        request.session[cache_key] = content

            confirm_form = response.context_data.get("confirm_form") if response.context_data else None
            if result is not None and response.context_data is not None:
                response.context_data["csv_stats"] = {
                    "total_records": getattr(result, "csv_total_records", 0),
                    "matched_by_email": getattr(result, "matched_by_email", 0),
                    "matched_by_name": getattr(result, "matched_by_name", 0),
                    "matched_total": getattr(result, "matched_total", 0),
                    "matched_total_percent": getattr(result, "matched_total_percent", 0.0),
                }

            if result is not None and confirm_form is not None and response.context_data is not None:
                valid_rows_obj = getattr(result, "valid_rows", None)
                if callable(valid_rows_obj):
                    valid_rows = list(valid_rows_obj() or [])
                else:
                    valid_rows = list(valid_rows_obj or [])
                matches: list[Any] = []
                skipped: list[Any] = []
                match_row_numbers: list[int] = []

                for idx, row_result in enumerate(valid_rows, start=1):
                    instance = row_result.instance if hasattr(row_result, "instance") else None
                    import_type = row_result.import_type if hasattr(row_result, "import_type") else ""
                    if import_type:
                        is_match = import_type != "skip"
                    elif instance is not None and hasattr(instance, "_decision"):
                        is_match = instance._decision == "IMPORT"
                    else:
                        is_match = False

                    number = getattr(row_result, "number", None)
                    if number is None:
                        number = getattr(getattr(row_result, "row", None), "number", None)
                    if number is None:
                        number = getattr(getattr(row_result, "original", None), "number", None)
                    if number is None:
                        number = idx
                    try:
                        row_result.astra_row_number = int(number)
                    except (TypeError, ValueError):
                        row_result.astra_row_number = idx

                    if is_match:
                        matches.append(row_result)
                        match_row_numbers.append(row_result.astra_row_number)
                    else:
                        skipped.append(row_result)

                try:
                    per_page = int(request.GET.get("per_page", "50"))
                except ValueError:
                    per_page = 50
                per_page = max(per_page, 50)

                matches_paginator = Paginator(matches, per_page)
                skipped_paginator = Paginator(skipped, per_page)

                matches_page_number = request.GET.get("matches_page") or "1"
                skipped_page_number = request.GET.get("skipped_page") or "1"
                response.context_data["matches_page_obj"] = matches_paginator.get_page(matches_page_number)
                response.context_data["skipped_page_obj"] = skipped_paginator.get_page(skipped_page_number)

                if match_row_numbers:
                    selected_default = ",".join(str(n) for n in sorted(set(match_row_numbers)))
                    response.context_data["all_match_row_numbers_csv"] = selected_default

                    selected_from_query = request.GET.get("selected_row_numbers", "")
                    if selected_from_query:
                        confirm_form.initial["selected_row_numbers"] = selected_from_query
                    else:
                        confirm_form.initial.setdefault("selected_row_numbers", selected_default)
        return response

    @override
    def process_result(self, result: Any, request: HttpRequest) -> HttpResponse:
        unmatched_url = getattr(result, "unmatched_download_url", "")
        if unmatched_url:
            messages.warning(
                request,
                mark_safe(f'Unmatched users export: <a href="{unmatched_url}">download CSV</a>'),
            )
        return super().process_result(result, request)

    @override
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "download-unmatched/<str:token>/",
                self.admin_site.admin_view(self.download_unmatched_view),
                name="core_membershipcsvimportlink_download_unmatched",
            ),
        ]
        return custom + urls

    def download_unmatched_view(self, request: HttpRequest, token: str) -> HttpResponse:
        if not self.has_view_permission(request):
            raise PermissionDenied

        cache_key = f"membership-import-unmatched:{token}"
        content = cache.get(cache_key)
        if content is None:
            content = request.session.get(cache_key)
            if content is not None:
                request.session.pop(cache_key, None)

        if content is None:
            raise Http404("Export expired")

        resp = HttpResponse(content, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="unmatched_users.csv"'
        return resp

    @override
    def changelist_view(self, request: HttpRequest, extra_context: dict[str, Any] | None = None) -> HttpResponse:
        return redirect("admin:core_membershipcsvimportlink_import")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    class OrganizationAdminForm(forms.ModelForm):
        representative = forms.ChoiceField(
            required=True,
            widget=forms.Select(attrs={"class": "form-control", "size": 12}),
            help_text="Select the FreeIPA user who is the organization's representative.",
        )

        class Meta:
            model = Organization
            fields = "__all__"

        @override
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            # Set short labels for each contact group's Name/Email/Phone fields
            # and assign per-group email help text. The fieldset descriptions
            # already provide the full context, so this just keeps the form clean.
            _CONTACT_GROUPS = [
                ("business_contact", "All legal and financial notices from AlmaLinux OS Foundation to the member will be sent to this e-mail address unless the member directs otherwise"),
                ("pr_marketing_contact", "This person will be contacted for press release and marketing benefit reasons"),
                ("technical_contact", "All technical notices from AlmaLinux OS Foundation to the member will be sent to this e-mail address unless the member directs otherwise"),
            ]
            for prefix, email_help in _CONTACT_GROUPS:
                self.fields[f"{prefix}_name"].label = "Name"
                self.fields[f"{prefix}_email"].label = "Email"
                self.fields[f"{prefix}_phone"].label = "Phone"
                self.fields[f"{prefix}_email"].help_text = email_help

            self.fields["name"].label = "Legal/Official name of the sponsor to be listed"
            self.fields["website_logo"].label = "High-quality logo that you would like used on the website"
            self.fields["website_logo"].help_text = "Please provide a white logo, or a link to all of your logo options"
            self.fields["website"].label = "URL we should link to"
            self.fields["website"].help_text = (
                "Please provide the exact URL that you would like the logo to link to - this can be a dedicated page or just your primary URL"
            )
            self.fields["logo"].label = "Logo upload for AlmaLinux Accounts"
            self.fields["additional_information"].label = "Please provide any additional information the Membership Committee should take into account"

            usernames, users_by_username = _freeipa_user_data()

            taken_representatives = set(
                Organization.objects.exclude(representative="")
                .exclude(pk=self.instance.pk)
                .values_list("representative", flat=True)
            )
            usernames = [u for u in usernames if u not in taken_representatives]

            current = str(self.initial.get("representative") or "").strip()
            if not current and self.instance:
                current = str(self.instance.representative or "").strip()

            if current and current not in usernames:
                usernames.append(current)

            merged = sorted(set(usernames), key=str.lower)
            self.fields["representative"].choices = [
                ("", "—"),
                *[
                    user_choice_with_fallback(u, users_by_username)
                    for u in merged
                ],
            ]

        def clean_representative(self) -> str:
            username = str(self.cleaned_data.get("representative") or "").strip()

            if not username:
                raise forms.ValidationError("A representative is required.", code="required")

            if FreeIPAUser.get(username) is None:
                raise forms.ValidationError(
                    f"Unknown user: {username}",
                    code="unknown_representative",
                )

            conflict_exists = (
                Organization.objects.filter(representative=username)
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if conflict_exists:
                raise forms.ValidationError(
                    "That user is already the representative of another organization.",
                    code="representative_not_unique",
                )

            return username

    form = OrganizationAdminForm

    fieldsets = (
        (
            "Business Contact",
            {
                "description": (
                    "All legal and financial notices from AlmaLinux OS Foundation to the member will be sent to this e-mail address unless the member directs otherwise"
                ),
                "fields": ("business_contact_name", "business_contact_email", "business_contact_phone"),
            },
        ),
        (
            "PR and/or Marketing Contact",
            {
                "description": "This person will be contacted for press release and marketing benefit reasons",
                "fields": ("pr_marketing_contact_name", "pr_marketing_contact_email", "pr_marketing_contact_phone"),
            },
        ),
        (
            "Technical Contact",
            {
                "description": (
                    "All technical notices from AlmaLinux OS Foundation to the member will be sent to this e-mail address unless the member directs otherwise"
                ),
                "fields": ("technical_contact_name", "technical_contact_email", "technical_contact_phone"),
            },
        ),
        (
            "Branding",
            {
                "fields": ("name", "website_logo", "website", "logo"),
            },
        ),
        (
            "Additional Information",
            {
                "fields": ("additional_information",),
            },
        ),
        (
            "Access",
            {
                "fields": ("id", "representative"),
            },
        ),
    )

    class MembershipInline(admin.TabularInline):
        model = Membership
        fk_name = "target_organization"
        fields = ("membership_type", "category", "expires_at", "created_at")
        readonly_fields = ("category", "created_at")
        extra = 0

    inlines = [MembershipInline]

    list_display = ("id", "name", "business_contact_email", "website")
    search_fields = (
        "name",
        "business_contact_email",
        "technical_contact_email",
        "pr_marketing_contact_email",
        "website",
    )
    ordering = ("name", "id")

    @override
    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj=obj))
        if "id" not in readonly:
            readonly.append("id")
        return tuple(readonly)


@admin.register(FreeIPAPermissionGrant)
class FreeIPAPermissionGrantAdmin(admin.ModelAdmin):
    class FreeIPAPermissionGrantAdminForm(forms.ModelForm):
        principal_type = forms.ChoiceField(
            label="Principal Type",
            choices=FreeIPAPermissionGrant.PrincipalType.choices,
            help_text="Grant to either a FreeIPA User or FreeIPA Group.",
        )

        principal_name = forms.ChoiceField(
            label="Principal Name",
            help_text="Select the FreeIPA principal (user or group) to grant this permission to.",
        )

        class Meta:
            model = FreeIPAPermissionGrant
            fields = "__all__"

        @override
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            raw_selected_type = self.data.get("principal_type")
            selected_type = (
                str(raw_selected_type).strip()
                if raw_selected_type is not None
                else str(self.initial.get("principal_type") or self.instance.principal_type or "").strip()
            )
            if not selected_type:
                selected_type = FreeIPAPermissionGrant.PrincipalType.user

            current = str(self.initial.get("principal_name") or "").strip()
            if not current and self.instance and self.instance.principal_name:
                current = str(self.instance.principal_name or "").strip()

            if selected_type == FreeIPAPermissionGrant.PrincipalType.group:
                principal_names = _freeipa_group_names()
                if current and current not in principal_names:
                    principal_names = sorted(set([*principal_names, current]), key=str.lower)
                self.fields["principal_name"].choices = [("", "---------"), *[(n, n) for n in principal_names]]
                return

            usernames, users_by_username = _freeipa_user_data()
            if current and current not in users_by_username:
                usernames = sorted(set([*usernames, current]), key=str.lower)
            self.fields["principal_name"].choices = [
                ("", "---------"),
                *[
                    user_choice_with_fallback(u, users_by_username)
                    for u in usernames
                ],
            ]

    class Media:
        js = (
            "core/js/admin_permission_grants_principal_dropdown.js",
        )

    form = FreeIPAPermissionGrantAdminForm

    list_display = ("permission", "principal_type", "principal_name", "created_at")
    list_filter = ("principal_type",)
    search_fields = ("permission", "principal_name")
    ordering = ("permission", "principal_type", "principal_name")

    @override
    def get_urls(self):
        urls = super().get_urls()

        custom = [
            path(
                "principals/",
                self.admin_site.admin_view(self.principals_view),
                name="core_freeipapermissiongrant_principals",
            ),
        ]

        return custom + urls

    def principals_view(self, request: HttpRequest) -> JsonResponse:
        raw = request.GET.get("principal_type")
        principal_type = str(raw or "").strip()

        valid_types = {
            FreeIPAPermissionGrant.PrincipalType.user,
            FreeIPAPermissionGrant.PrincipalType.group,
        }
        if principal_type not in valid_types:
            principal_type = FreeIPAPermissionGrant.PrincipalType.user

        if principal_type == FreeIPAPermissionGrant.PrincipalType.group:
            names = _freeipa_group_names()
            principals = [{"id": n, "text": n} for n in names]
            return JsonResponse({"principal_type": principal_type, "principals": principals})

        usernames, users_by_username = _freeipa_user_data()
        principals = [
            {"id": u, "text": user_choice(u, user=users_by_username.get(u))[1]}
            for u in usernames
        ]
        return JsonResponse({"principal_type": principal_type, "principals": principals})

# Replace DB-backed auth models in admin with FreeIPA-backed listings.
try:
    admin.site.unregister(DjangoUser)
except admin.sites.NotRegistered:
    pass

try:
    admin.site.unregister(DjangoGroup)
except admin.sites.NotRegistered:
    pass

# django-avatar registers an Avatar admin that depends on Django's User admin
# being registered (it uses autocomplete_fields=['user']). This project
# intentionally unregisters the DB-backed User/Group admin in favor of FreeIPA
# listings, so we also remove the django-avatar admin integration.
try:
    from avatar.models import Avatar

    try:
        admin.site.unregister(Avatar)
    except admin.sites.NotRegistered:
        pass
except ImportError:
    pass


# Keep the traditional admin URLs working.
if not getattr(admin.site, "_freeipa_aliases_patched", False):
    _orig_get_urls = admin.site.get_urls

    def _get_urls_with_freeipa_aliases():
        def redirect_to_ipa_users(request):
            return HttpResponseRedirect(reverse("admin:auth_ipauser_changelist"))

        def redirect_to_ipa_groups(request):
            return HttpResponseRedirect(reverse("admin:auth_ipagroup_changelist"))

        custom = [
            path("auth/user/", admin.site.admin_view(redirect_to_ipa_users)),
            path("auth/group/", admin.site.admin_view(redirect_to_ipa_groups)),
        ]
        return custom + _orig_get_urls()

    admin.site.get_urls = _get_urls_with_freeipa_aliases
    admin.site._freeipa_aliases_patched = True
