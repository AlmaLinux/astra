import datetime
import enum
import hashlib
import json
import logging
import secrets
import uuid
from io import BytesIO
from typing import override

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from PIL import Image

from core.membership_targets import MembershipTargetIdentity, MembershipTargetKind
from core.tokens import make_signed_token

logger = logging.getLogger(__name__)


def organization_logo_upload_to(instance: Organization, filename: str) -> str:
    # Always store organizations' logos with a deterministic name.
    # Access control (bucket policy / auth) must be the security boundary.
    return f"organizations/logos/{instance.pk}.png"


class IPAUser(models.Model):
    # NOTE: Keep this model unmanaged; it mirrors FreeIPA users.
    username = models.CharField(max_length=255, primary_key=True)
    first_name = models.CharField(max_length=255, blank=True, default="")
    last_name = models.CharField(max_length=255, blank=True, default="")
    displayname = models.CharField(max_length=255, blank=True, default="", verbose_name="Display name")
    email = models.EmailField(blank=True, default="")
    fasstatusnote = models.TextField(blank=True, default="", verbose_name="Note")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    class Meta:
        managed = False
        # Make it appear where Django's default User model is listed.
        app_label = "auth"
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self) -> str:
        return self.username

    @classmethod
    def from_freeipa(cls, user) -> IPAUser:
        # `user` is a core.backends.FreeIPAUser
        return cls(
            username=user.username,
            first_name=user.first_name or "",
            last_name=user.last_name or "",
            displayname=user.displayname or "",
            email=user.email or "",
            fasstatusnote=user.fasstatusnote or "",
            is_active=bool(getattr(user, "is_active", True)),
            is_staff=bool(getattr(user, "is_staff", False)),
        )


class IPAGroup(models.Model):
    # NOTE: Keep this model unmanaged; it mirrors FreeIPA groups.
    cn = models.CharField(max_length=255, primary_key=True)
    description = models.TextField(blank=True, default="")
    fas_url = models.URLField(blank=True, default="", verbose_name="FAS URL")
    fas_mailing_list = models.EmailField(blank=True, default="", verbose_name="FAS Mailing List")
    fas_discussion_url = models.URLField(blank=True, default="", verbose_name="FAS Discussion URL")
    fas_group = models.BooleanField(default=False, verbose_name="FAS Group")

    class Meta:
        managed = False
        app_label = "auth"
        verbose_name = "group"
        verbose_name_plural = "groups"

    def __str__(self) -> str:
        return self.cn

    @classmethod
    def from_freeipa(cls, group) -> IPAGroup:
        # `group` is a core.backends.FreeIPAGroup
        return cls(
            cn=group.cn,
            description=getattr(group, "description", "") or "",
            fas_url=getattr(group, "fas_url", "") or "",
            fas_mailing_list=getattr(group, "fas_mailing_list", "") or "",
            fas_discussion_url=getattr(group, "fas_discussion_url", "") or "",
            fas_group=getattr(group, "fas_group", False),
        )


class IPAFASAgreement(models.Model):
    # NOTE: Keep this model unmanaged; it mirrors FreeIPA fasagreement entries.
    cn = models.CharField(max_length=255, primary_key=True, verbose_name="Agreement name")
    description = models.TextField(blank=True, default="")
    enabled = models.BooleanField(default=True)

    class Meta:
        managed = False
        # Keep these in the same admin section as other FreeIPA-backed objects.
        app_label = "auth"
        verbose_name = "Agreement"
        verbose_name_plural = "Agreements"

    def __str__(self) -> str:
        return self.cn

    @classmethod
    def from_freeipa(cls, agreement) -> IPAFASAgreement:
        # `agreement` is a core.backends.FreeIPAFASAgreement
        # Coerce to concrete types so the Django admin list display doesn't
        # receive MagicMock values from tests or partial stubs.
        return cls(
            cn=agreement.cn,
            description=str(getattr(agreement, "description", "") or ""),
            enabled=bool(getattr(agreement, "enabled", True)),
        )


class MembershipTypeCategory(models.Model):
    """Broad category of membership types (individual, mirror, sponsorship).

    Controls which target types (users and/or organizations) are valid for
    membership types in this category, and enforces the one-per-category
    invariant for organizations via a denormalized FK on Membership.
    """

    name = models.CharField(max_length=64, primary_key=True)
    is_individual = models.BooleanField(
        default=False,
        help_text="Whether types in this category can be held by individual users.",
    )
    is_organization = models.BooleanField(
        default=False,
        help_text="Whether types in this category can be held by organizations.",
    )
    sort_order = models.IntegerField(
        default=0,
        help_text="Lower values appear first in membership type selections.",
    )

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = "Membership type category"
        verbose_name_plural = "Membership type categories"

    def __str__(self) -> str:
        return self.name


class MembershipTypeQuerySet(models.QuerySet["MembershipType"]):
    def enabled(self) -> MembershipTypeQuerySet:
        return self.filter(enabled=True)

    def ordered_for_display(self) -> MembershipTypeQuerySet:
        return self.select_related("category").order_by(
            "category__sort_order",
            "category__name",
            "sort_order",
            "code",
            "pk",
        )


class MembershipType(models.Model):
    code = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    votes = models.PositiveIntegerField(blank=True, default=0)
    group_cn = models.CharField(max_length=255, blank=True, default="", verbose_name="Group")
    acceptance_template = models.ForeignKey(
        "post_office.EmailTemplate",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="+",
        help_text="Email template used when a membership request is approved.",
    )
    category = models.ForeignKey(
        MembershipTypeCategory,
        on_delete=models.PROTECT,
        related_name="membership_types",
        help_text="Broad category: individual, mirror, or sponsorship.",
    )
    sort_order = models.IntegerField(default=0)
    enabled = models.BooleanField(default=True)

    objects = MembershipTypeQuerySet.as_manager()

    class Meta:
        ordering = ("sort_order", "code")

    def __str__(self) -> str:
        return f"{self.name}"


class Organization(models.Model):
    class Status(models.TextChoices):
        unclaimed = "unclaimed", "Unclaimed"
        active = "active", "Active"

    name = models.CharField(max_length=255)

    business_contact_name = models.CharField(max_length=255, blank=True, default="")
    business_contact_email = models.EmailField(blank=True, default="")
    business_contact_phone = models.CharField(max_length=64, blank=True, default="")

    pr_marketing_contact_name = models.CharField(max_length=255, blank=True, default="")
    pr_marketing_contact_email = models.EmailField(blank=True, default="")
    pr_marketing_contact_phone = models.CharField(max_length=64, blank=True, default="")

    technical_contact_name = models.CharField(max_length=255, blank=True, default="")
    technical_contact_email = models.EmailField(blank=True, default="")
    technical_contact_phone = models.CharField(max_length=64, blank=True, default="")

    website_logo = models.URLField(blank=True, default="", max_length=2048)

    website = models.URLField(blank=True, default="")
    logo = models.ImageField(
        upload_to=organization_logo_upload_to,
        blank=True,
        null=True,
    )

    representative = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.unclaimed, db_index=True)
    claim_secret = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["representative"],
                condition=~models.Q(representative=""),
                name="core_organization_unique_representative",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(status="unclaimed") & models.Q(representative=""))
                    | (models.Q(status="active") & ~models.Q(representative=""))
                ),
                name="core_organization_status_matches_representative",
            ),
            models.CheckConstraint(
                condition=~(models.Q(status="unclaimed") & models.Q(claim_secret="")),
                name="core_organization_unclaimed_requires_claim_secret",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name}"

    def primary_contact_email(self) -> str:
        return (
            self.business_contact_email
            or self.pr_marketing_contact_email
            or self.technical_contact_email
            or ""
        )

    @override
    def save(self, *args, **kwargs) -> None:
        self.representative = str(self.representative or "").strip()
        self.claim_secret = str(self.claim_secret or "").strip()

        if self.representative:
            self.status = self.Status.active
        else:
            self.status = self.Status.unclaimed
            if not self.claim_secret:
                # Unclaimed organizations need a stable server-side secret so
                # claim links can be invalidated by rotating this value.
                self.claim_secret = secrets.token_urlsafe(32)

        if self.pk is None and self.logo:
            # The storage path is based on the autoincrement PK; ensure we have
            # one before writing the file.
            pending_logo = self.logo
            self.logo = None
            super().save(*args, **kwargs)
            self.logo = pending_logo

        self._convert_new_logo_upload_to_png()
        super().save(*args, **kwargs)

    def _convert_new_logo_upload_to_png(self) -> None:
        if not self.logo:
            return

        # Only convert when a new file is uploaded in this save.
        # For existing stored files, avoid implicitly downloading/re-uploading.
        if not hasattr(self.logo, "_file") or self.logo._file is None:
            return
        if not isinstance(self.logo._file, UploadedFile):
            return

        uploaded = self.logo._file
        uploaded.seek(0)
        img = Image.open(uploaded)
        img.load()

        # Normalize to PNG. Preserve alpha when possible.
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        content = ContentFile(buf.getvalue())

        # The upload_to callable ignores the provided filename and always
        # generates organizations/logos/{pk}.png.
        self.logo.save(f"{self.pk}.png", content, save=False)


class MembershipRequest(models.Model):
    class TargetKind(enum.StrEnum):
        user = "user"
        organization = "organization"

    class Status(models.TextChoices):
        pending = "pending", "Pending"
        on_hold = "on_hold", "On Hold"
        approved = "approved", "Approved"
        rejected = "rejected", "Rejected"
        ignored = "ignored", "Ignored"
        rescinded = "rescinded", "Rescinded"

    requested_username = models.CharField(max_length=255, blank=True, default="")
    requested_organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="membership_requests",
    )
    requested_organization_code = models.CharField(max_length=64, blank=True, default="")
    requested_organization_name = models.CharField(max_length=255, blank=True, default="")
    membership_type = models.ForeignKey(MembershipType, on_delete=models.PROTECT)
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.pending, db_index=True)
    on_hold_at = models.DateTimeField(blank=True, null=True)
    decided_at = models.DateTimeField(blank=True, null=True)
    decided_by_username = models.CharField(max_length=255, blank=True, default="")
    responses = models.JSONField(blank=True, default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["requested_username", "membership_type"],
                condition=Q(status__in=["pending", "on_hold"], requested_organization__isnull=True)
                & ~Q(requested_username=""),
                name="uniq_membershiprequest_open_user_type",
            ),
            models.UniqueConstraint(
                fields=["requested_organization", "membership_type"],
                condition=Q(status__in=["pending", "on_hold"], requested_organization__isnull=False),
                name="uniq_membershiprequest_open_org_type",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        Q(requested_organization__isnull=True)
                        & Q(requested_organization_code="")
                        & ~Q(requested_username="")
                    )
                    | (
                        Q(requested_username="")
                        & (Q(requested_organization__isnull=False) | ~Q(requested_organization_code=""))
                    )
                ),
                name="chk_membershiprequest_exactly_one_target",
            ),
        ]
        indexes = [
            models.Index(fields=["requested_at"], name="mr_req_at"),
            models.Index(fields=["status", "requested_at"], name="mr_status_at"),
            models.Index(fields=["requested_username", "status"], name="mr_user_status"),
            models.Index(fields=["requested_organization", "status"], name="mr_org_status"),
            models.Index(fields=["requested_organization_code", "status"], name="mr_org_code_status"),
        ]
        ordering = ("-requested_at",)

    @override
    def save(self, *args, **kwargs) -> None:
        if self.requested_organization_id is not None and not self.requested_organization_code:
            self.requested_organization_code = str(self.requested_organization_id)
            self.requested_organization_name = self.requested_organization.name
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        if self.target_kind == self.TargetKind.user:
            return f"{self.requested_username} → {self.membership_type_id}"
        return f"org:{self.target_identifier} → {self.membership_type_id}"

    @property
    def target_identity(self) -> MembershipTargetIdentity:
        return MembershipTargetIdentity.from_target_fields(
            username=self.requested_username,
            organization_id=self.requested_organization_id,
            organization_code=self.requested_organization_code,
            organization_name=self.requested_organization_name,
            organization_fk_name=self.requested_organization.name if self.requested_organization is not None else "",
        )

    @property
    def target_kind(self) -> TargetKind:
        return self.TargetKind(self.target_identity.kind.value)

    @property
    def is_user_target(self) -> bool:
        return self.target_kind == self.TargetKind.user

    @property
    def is_organization_target(self) -> bool:
        return self.target_kind == self.TargetKind.organization

    @property
    def organization_identifier(self) -> str:
        return self.target_identity.organization_identifier

    @property
    def organization_display_name(self) -> str:
        return self.target_identity.organization_display_name

    @property
    def target_identifier(self) -> str:
        if self.target_kind == self.TargetKind.user:
            return self.target_identity.identifier
        return self.target_identity.organization_identifier or "?"



class MembershipQuerySet(models.QuerySet["Membership"]):
    def active(
        self,
        *,
        at: datetime.datetime | None = None,
    ) -> MembershipQuerySet:
        reference = timezone.now() if at is None else at
        return self.filter(Q(expires_at__isnull=True) | Q(expires_at__gte=reference))

class AccountInvitation(models.Model):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True, default="")
    note = models.TextField(blank=True, default="")
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="account_invitations",
    )
    invitation_token = models.CharField(max_length=512, unique=True, editable=False)
    invited_by_username = models.CharField(max_length=255)
    invited_at = models.DateTimeField(auto_now_add=True)
    email_template_name = models.CharField(max_length=255, blank=True, default="")
    last_sent_at = models.DateTimeField(blank=True, null=True)
    send_count = models.PositiveIntegerField(default=0)
    dismissed_at = models.DateTimeField(blank=True, null=True)
    dismissed_by_username = models.CharField(max_length=255, blank=True, default="")
    accepted_at = models.DateTimeField(blank=True, null=True)
    freeipa_matched_usernames = models.JSONField(blank=True, default=list)
    freeipa_last_checked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-invited_at", "email")
        indexes = [
            models.Index(fields=["accepted_at"], name="acct_inv_accept_at"),
            models.Index(fields=["dismissed_at"], name="acct_inv_dismiss_at"),
        ]

    @override
    def save(self, *args, **kwargs) -> None:
        self.email = str(self.email or "").strip().lower()
        self.full_name = str(self.full_name or "").strip()
        self.note = str(self.note or "").strip()
        self.invited_by_username = str(self.invited_by_username or "").strip()
        self.email_template_name = str(self.email_template_name or "").strip()
        self.dismissed_by_username = str(self.dismissed_by_username or "").strip()
        if not isinstance(self.freeipa_matched_usernames, list):
            self.freeipa_matched_usernames = []
        else:
            self.freeipa_matched_usernames = [
                str(item or "").strip().lower()
                for item in self.freeipa_matched_usernames
                if str(item or "").strip()
            ]
        super().save(*args, **kwargs)

        if not self.invitation_token:
            token = make_signed_token({"invitation_id": self.pk})
            type(self).objects.filter(pk=self.pk).update(invitation_token=token)
            self.invitation_token = token

    def __str__(self) -> str:
        return f"account-invite:{self.email}"


class AccountInvitationSend(models.Model):
    class Result(models.TextChoices):
        queued = "queued", "Queued"
        failed = "failed", "Failed"

    invitation = models.ForeignKey(
        AccountInvitation,
        on_delete=models.CASCADE,
        related_name="sends",
    )
    sent_by_username = models.CharField(max_length=255)
    sent_at = models.DateTimeField(default=timezone.now)
    template_name = models.CharField(max_length=255)
    post_office_email_id = models.BigIntegerField(blank=True, null=True)
    result = models.CharField(max_length=16, choices=Result.choices)
    error_category = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["sent_at"], name="acct_inv_send_at"),
        ]

    @override
    def save(self, *args, **kwargs) -> None:
        self.sent_by_username = str(self.sent_by_username or "").strip()
        self.template_name = str(self.template_name or "").strip()
        self.error_category = str(self.error_category or "").strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"account-invite-send:{self.invitation_id}:{self.sent_at.isoformat()}"


class Note(models.Model):
    """Membership committee notes and actions tied to a specific membership request."""

    membership_request = models.ForeignKey(
        MembershipRequest,
        on_delete=models.CASCADE,
        related_name="notes",
    )
    username = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    content = models.TextField(blank=True, null=True)
    action = models.JSONField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["membership_request", "timestamp"], name="note_req_at"),
            models.Index(fields=["membership_request", "username", "timestamp"], name="note_req_user_at"),
        ]
        ordering = ("timestamp", "pk")

        constraints = [
            models.CheckConstraint(
                condition=Q(content__isnull=False) | Q(action__isnull=False),
                name="chk_note_content_or_action",
            ),
        ]

    @override
    def save(self, *args, **kwargs) -> None:
        # Treat empty/whitespace-only content as absent.
        if self.content is not None and str(self.content).strip() == "":
            self.content = None
        # Treat an empty dict/list as absent action.
        if self.action in ({}, []):
            self.action = None

        super().save(*args, **kwargs)

    @override
    def clean(self) -> None:
        super().clean()

        content_present = self.content is not None and str(self.content).strip() != ""
        action_present = self.action is not None
        if not content_present and not action_present:
            raise ValidationError("A note must have content and/or an action.")


class Membership(models.Model):
    """Active membership row for a user OR an organization.

    Exactly one of (target_username, target_organization) must be set.
    The ``category`` FK is denormalized from ``membership_type.category`` on
    save, enabling the DB-level UniqueConstraint that enforces the
    one-membership-per-category invariant for organizations.
    """

    target_username = models.CharField(max_length=255, blank=True, default="")
    target_organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="memberships",
    )
    target_organization_code = models.CharField(max_length=64, blank=True, default="")
    target_organization_name = models.CharField(max_length=255, blank=True, default="")
    membership_type = models.ForeignKey(MembershipType, on_delete=models.PROTECT)
    category = models.ForeignKey(
        MembershipTypeCategory,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Denormalized from membership_type.category for UniqueConstraint.",
    )
    expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MembershipQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["target_username", "membership_type"],
                condition=~models.Q(target_username=""),
                name="uniq_membership_target_username_type",
            ),
            models.UniqueConstraint(
                fields=["target_organization", "membership_type"],
                condition=models.Q(target_organization__isnull=False),
                name="uniq_membership_org_type",
            ),
            # One-per-category for orgs: an org can hold at most one
            # membership type from each category (DD-01).
            models.UniqueConstraint(
                fields=["target_organization", "category"],
                condition=models.Q(target_organization__isnull=False),
                name="uniq_membership_org_category",
            ),
            # Exactly one target must be set, never both, never neither.
            models.CheckConstraint(
                condition=(
                    (
                        ~models.Q(target_username="")
                        & models.Q(target_organization__isnull=True)
                        & models.Q(target_organization_code="")
                    )
                    | (
                        models.Q(target_username="")
                        & models.Q(target_organization__isnull=False)
                    )
                ),
                name="chk_membership_exactly_one_target",
            ),
        ]
        indexes = [
            models.Index(fields=["target_username"], name="m_tgt"),
            models.Index(fields=["target_organization"], name="m_org_tgt"),
            models.Index(fields=["expires_at"], name="m_exp_at"),
        ]
        ordering = ("target_username", "membership_type_id")

    @override
    def save(self, *args, **kwargs) -> None:
        # Denormalize category from membership_type so the DB constraint works.
        self.category_id = self.membership_type.category_id

        # Denormalize org identifiers for display after FK deletion.
        if self.target_organization_id is not None:
            if not self.target_organization_code:
                self.target_organization_code = str(self.target_organization_id)
            if not self.target_organization_name and self.target_organization is not None:
                self.target_organization_name = self.target_organization.name

        super().save(*args, **kwargs)

    @property
    def target_identity(self) -> MembershipTargetIdentity:
        return MembershipTargetIdentity.from_target_fields(
            username=self.target_username,
            organization_id=self.target_organization_id,
            organization_code=self.target_organization_code,
            organization_name=self.target_organization_name,
            organization_fk_name=self.target_organization.name if self.target_organization is not None else "",
        )

    @property
    def target_kind(self) -> MembershipTargetKind:
        return self.target_identity.kind

    @property
    def organization_identifier(self) -> str:
        return self.target_identity.organization_identifier

    @property
    def organization_display_name(self) -> str:
        return self.target_identity.organization_display_name

    @property
    def target_identifier(self) -> str:
        return self.target_identity.identifier

    @classmethod
    def replace_within_category(
        cls,
        *,
        organization: Organization,
        new_membership_type: MembershipType,
        expires_at: datetime.datetime | None,
        created_at: datetime.datetime | None = None,
    ) -> tuple[Membership, Membership | None]:
        """Atomically replace an org's membership within the same category (DD-02).

        Returns (new_membership, old_membership_or_None).
        The old membership is deleted first so the UniqueConstraint on
        (target_organization, category) is satisfied.
        """
        with transaction.atomic():
            category_id = new_membership_type.category_id
            old = (
                cls.objects.filter(
                    target_organization=organization,
                    category_id=category_id,
                )
                .select_for_update()
                .first()
            )
            if old is not None:
                old_copy = Membership(
                    pk=old.pk,
                    target_organization_id=old.target_organization_id,
                    target_organization_code=old.target_organization_code,
                    target_organization_name=old.target_organization_name,
                    membership_type_id=old.membership_type_id,
                    category_id=old.category_id,
                    expires_at=old.expires_at,
                )
                # Manually copy created_at since auto_now_add would overwrite on
                # a new unsaved instance.
                old_copy.created_at = old.created_at
                old.delete()
            else:
                old_copy = None

            new = cls(
                target_organization=organization,
                membership_type=new_membership_type,
                expires_at=expires_at,
            )
            new.save()

            if created_at is not None and new.created_at != created_at:
                cls.objects.filter(pk=new.pk).update(created_at=created_at)
                new.created_at = created_at

            return new, old_copy

    def __str__(self) -> str:
        if self.target_kind == MembershipTargetKind.user:
            return f"{self.target_identifier} ({self.membership_type_id})"
        code = self.organization_identifier or "?"
        return f"org:{code} ({self.membership_type_id})"


class MembershipLogQuerySet(models.QuerySet["MembershipLog"]):
    def for_organization_identifier(self, organization_id: int) -> MembershipLogQuerySet:
        return self.filter(
            Q(target_organization_id=organization_id) | Q(target_organization_code=str(organization_id))
        )


class MembershipLog(models.Model):
    class Action(models.TextChoices):
        requested = "requested", "Requested"
        on_hold = "on_hold", "On Hold"
        resubmitted = "resubmitted", "Resubmitted"
        approved = "approved", "Approved"
        rejected = "rejected", "Rejected"
        ignored = "ignored", "Ignored"
        rescinded = "rescinded", "Rescinded"
        representative_changed = "representative_changed", "Representative changed"
        expiry_changed = "expiry_changed", "Expiry changed"
        terminated = "terminated", "Terminated"

    actor_username = models.CharField(max_length=255)
    target_username = models.CharField(max_length=255, blank=True, default="")
    target_organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="membership_logs",
    )
    target_organization_code = models.CharField(max_length=64, blank=True, default="")
    target_organization_name = models.CharField(max_length=255, blank=True, default="")
    membership_type = models.ForeignKey(MembershipType, on_delete=models.PROTECT)
    membership_request = models.ForeignKey(
        MembershipRequest,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="logs",
    )
    requested_group_cn = models.CharField(max_length=255, blank=True, default="")
    action = models.CharField(max_length=32, choices=Action.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    rejection_reason = models.TextField(blank=True, default="")
    expires_at = models.DateTimeField(blank=True, null=True)

    objects = MembershipLogQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        Q(target_username="")
                        & (Q(target_organization__isnull=False) | ~Q(target_organization_code=""))
                    )
                    | (
                        ~Q(target_username="")
                        & Q(target_organization__isnull=True)
                        & Q(target_organization_code="")
                    )
                ),
                name="chk_membershiplog_exactly_one_target",
            )
        ]
        indexes = [
            models.Index(fields=["target_username", "created_at"], name="ml_tgt_at"),
            models.Index(fields=["target_username", "action", "created_at"], name="ml_tgt_act_at"),
            models.Index(fields=["target_organization", "created_at"], name="ml_org_at"),
            models.Index(fields=["target_organization", "action", "created_at"], name="ml_org_act_at"),
            models.Index(fields=["target_organization_code", "created_at"], name="ml_org_code_at"),
            models.Index(fields=["target_organization_code", "action", "created_at"], name="ml_org_code_act_at"),
            models.Index(fields=["expires_at"], name="ml_exp_at"),
        ]
        ordering = ("-created_at",)

    @property
    def target_identity(self) -> MembershipTargetIdentity:
        return MembershipTargetIdentity.from_target_fields(
            username=self.target_username,
            organization_id=self.target_organization_id,
            organization_code=self.target_organization_code,
            organization_name=self.target_organization_name,
            organization_fk_name=self.target_organization.name if self.target_organization is not None else "",
        )

    @property
    def target_kind(self) -> MembershipTargetKind:
        return self.target_identity.kind

    @property
    def organization_identifier(self) -> str:
        return self.target_identity.organization_identifier

    @property
    def organization_display_name(self) -> str:
        return self.target_identity.organization_display_name

    @property
    def target_identifier(self) -> str:
        return self.target_identity.identifier

    @override
    def save(self, *args, **kwargs) -> None:
        if self.target_organization_id is not None and not self.target_organization_code:
            self.target_organization_code = str(self.target_organization_id)
            self.target_organization_name = self.target_organization.name
        super().save(*args, **kwargs)

        if self.target_organization_id is not None:
            self._apply_org_side_effects()
            return

        if self.target_organization_code:
            self._cleanup_orphaned_organization_memberships()
            return

        self._apply_user_side_effects()

    def _cleanup_orphaned_organization_memberships(self) -> None:
        if self.action not in {self.Action.approved, self.Action.expiry_changed, self.Action.terminated}:
            return

        try:
            organization_id = int(self.target_organization_code)
        except ValueError:
            return

        deleted_count, _ = Membership.objects.filter(target_organization_id=organization_id).delete()
        if deleted_count > 0:
            logger.info(
                "_cleanup_orphaned_organization_memberships: removed memberships for orphan org_code=%s",
                self.target_organization_code,
            )

    def _resolve_term_start_at(
        self,
        *,
        existing: Membership | None,
        log_filter: dict[str, object],
    ) -> datetime.datetime:
        """Compute the start of the current uninterrupted term from membership logs."""
        if existing is not None and existing.expires_at is not None and existing.expires_at > self.created_at:
            return existing.created_at

        start_at = self.created_at

        last_approved = (
            MembershipLog.objects.filter(
                **log_filter,
                action=self.Action.approved,
                created_at__lt=self.created_at,
            )
            .only("created_at", "expires_at")
            .order_by("-created_at")
            .first()
        )

        if last_approved is not None and last_approved.expires_at is not None and last_approved.expires_at > self.created_at:
            last_terminated = (
                MembershipLog.objects.filter(
                    **log_filter,
                    action=self.Action.terminated,
                    created_at__lt=self.created_at,
                )
                .only("created_at")
                .order_by("-created_at")
                .first()
            )

            approved_qs = MembershipLog.objects.filter(**log_filter, action=self.Action.approved)
            if last_terminated is not None:
                approved_qs = approved_qs.filter(created_at__gt=last_terminated.created_at)

            first_term_approved = approved_qs.only("created_at").order_by("created_at").first()
            if first_term_approved is not None:
                start_at = first_term_approved.created_at

        return start_at

    def _apply_org_side_effects(self) -> None:
        """Side-effects for organization membership changes."""
        if self.action not in {self.Action.approved, self.Action.expiry_changed, self.Action.terminated}:
            return

        if self.action == self.Action.terminated:
            Membership.objects.filter(
                target_organization_id=self.target_organization_id,
                membership_type=self.membership_type,
            ).delete()
            return

        existing = (
            Membership.objects.filter(
                target_organization_id=self.target_organization_id,
                membership_type=self.membership_type,
            )
            .only("created_at", "expires_at")
            .first()
        )

        log_filter = self.target_identity.for_membership_log_filter()
        log_filter["membership_type"] = self.membership_type

        start_at = self._resolve_term_start_at(existing=existing, log_filter=log_filter)

        org = Organization.objects.filter(pk=self.target_organization_id).first()
        if org is None:
            logger.warning(
                "_apply_org_side_effects: organization not found org_id=%s",
                self.target_organization_id,
            )
            return

        new_membership, old = Membership.replace_within_category(
            organization=org,
            new_membership_type=self.membership_type,
            expires_at=self.expires_at,
            created_at=start_at,
        )
        if old is not None and old.membership_type_id != self.membership_type_id:
            logger.info(
                "_apply_org_side_effects: replaced %s with %s for org_id=%s",
                old.membership_type_id,
                self.membership_type_id,
                self.target_organization_id,
            )

    def _apply_user_side_effects(self) -> None:
        if self.action not in {self.Action.approved, self.Action.expiry_changed, self.Action.terminated}:
            return

        category_id = self.membership_type.category_id
        membership_qs = Membership.objects.filter(
            target_username=self.target_username,
            category_id=category_id,
        )

        if self.action == self.Action.terminated:
            membership_qs.filter(membership_type=self.membership_type).delete()
            return

        existing_same_type = (
            membership_qs.filter(membership_type=self.membership_type)
            .only("created_at", "expires_at")
            .first()
        )
        log_filter = self.target_identity.for_membership_log_filter()
        log_filter["membership_type"] = self.membership_type
        start_at = self._resolve_term_start_at(existing=existing_same_type, log_filter=log_filter)

        with transaction.atomic():
            removed_count, _ = membership_qs.exclude(membership_type=self.membership_type).delete()
            row, _created = Membership.objects.update_or_create(
                target_username=self.target_username,
                membership_type=self.membership_type,
                defaults={
                    "category": self.membership_type.category,
                    "expires_at": self.expires_at,
                },
            )

            if row.created_at != start_at:
                Membership.objects.filter(pk=row.pk).update(created_at=start_at)

        if removed_count > 0:
            logger.info(
                "_apply_user_side_effects: enforced one-per-category target=%s category=%s removed=%s",
                self.target_username,
                category_id,
                removed_count,
            )

    def __str__(self) -> str:
        if self.target_kind == MembershipTargetKind.organization:
            code = self.organization_identifier
            return f"{self.action}: org:{code} ({self.membership_type_id})"
        return f"{self.action}: {self.target_identifier} ({self.membership_type_id})"

    @classmethod
    def expiry_for_approval_at(
        cls,
        *,
        approved_at: datetime.datetime,
        previous_expires_at: datetime.datetime | None = None,
    ) -> datetime.datetime:
        # If we're extending an existing membership, preserve the existing
        # expiration timestamp as the base so the new term starts when the
        # previous one ends.
        if previous_expires_at is not None and previous_expires_at > approved_at:
            base = previous_expires_at
        else:
            # For a new approval, treat the approval as granting the rest of the
            # day, so the initial expiration is end-of-day (UTC) on the
            # corresponding date.
            base = datetime.datetime.combine(
                approved_at.astimezone(datetime.UTC).date(),
                datetime.time(23, 59, 59),
                tzinfo=datetime.UTC,
            )

        return base + datetime.timedelta(days=settings.MEMBERSHIP_VALIDITY_DAYS)

    @classmethod
    def _create_log(
        cls,
        *,
        actor_username: str,
        action: str,
        membership_type: MembershipType,
        target_username: str = "",
        target_organization: Organization | None = None,
        target_organization_code: str = "",
        target_organization_name: str = "",
        membership_request: MembershipRequest | None = None,
        expires_at: datetime.datetime | None = None,
        rejection_reason: str = "",
    ) -> MembershipLog:
        """Internal factory: all public create_for_* methods delegate here."""
        kwargs: dict[str, object] = {
            "actor_username": actor_username,
            "target_username": target_username,
            "membership_type": membership_type,
            "membership_request": membership_request,
            "requested_group_cn": membership_type.group_cn,
            "action": action,
        }
        if target_organization is not None:
            kwargs["target_organization"] = target_organization
            kwargs["target_organization_code"] = str(target_organization.pk)
            kwargs["target_organization_name"] = target_organization.name
        elif target_organization_code or target_organization_name:
            # Org FK is gone but caller supplied identifiers (e.g. deleted org).
            kwargs["target_organization_code"] = target_organization_code
            kwargs["target_organization_name"] = target_organization_name
        if expires_at is not None:
            kwargs["expires_at"] = expires_at
        if rejection_reason:
            kwargs["rejection_reason"] = rejection_reason
        return cls.objects.create(**kwargs)

    # --- Factory methods (unified: pass target_username OR target_organization) ---

    @classmethod
    def create_for_request(
        cls,
        *,
        actor_username: str,
        membership_type: MembershipType,
        target_username: str = "",
        target_organization: Organization | None = None,
        membership_request: MembershipRequest | None = None,
    ) -> MembershipLog:
        return cls._create_log(
            actor_username=actor_username, target_username=target_username,
            target_organization=target_organization,
            membership_type=membership_type, membership_request=membership_request,
            action=cls.Action.requested,
        )

    @classmethod
    def create_for_approval_at(
        cls,
        *,
        actor_username: str,
        membership_type: MembershipType,
        approved_at: datetime.datetime,
        target_username: str = "",
        target_organization: Organization | None = None,
        previous_expires_at: datetime.datetime | None = None,
        membership_request: MembershipRequest | None = None,
    ) -> MembershipLog:
        return cls._create_log(
            actor_username=actor_username, target_username=target_username,
            target_organization=target_organization,
            membership_type=membership_type, membership_request=membership_request,
            action=cls.Action.approved,
            expires_at=cls.expiry_for_approval_at(
                approved_at=approved_at, previous_expires_at=previous_expires_at,
            ),
        )

    @classmethod
    def create_for_approval(
        cls,
        *,
        actor_username: str,
        membership_type: MembershipType,
        target_username: str = "",
        target_organization: Organization | None = None,
        previous_expires_at: datetime.datetime | None = None,
        membership_request: MembershipRequest | None = None,
    ) -> MembershipLog:
        return cls.create_for_approval_at(
            actor_username=actor_username, target_username=target_username,
            target_organization=target_organization,
            membership_type=membership_type, approved_at=timezone.now(),
            previous_expires_at=previous_expires_at, membership_request=membership_request,
        )

    @classmethod
    def create_for_expiry_change(
        cls,
        *,
        actor_username: str,
        membership_type: MembershipType,
        expires_at: datetime.datetime,
        target_username: str = "",
        target_organization: Organization | None = None,
        membership_request: MembershipRequest | None = None,
    ) -> MembershipLog:
        return cls._create_log(
            actor_username=actor_username, target_username=target_username,
            target_organization=target_organization,
            membership_type=membership_type, membership_request=membership_request,
            action=cls.Action.expiry_changed, expires_at=expires_at,
        )

    @classmethod
    def create_for_termination(
        cls,
        *,
        actor_username: str,
        membership_type: MembershipType,
        target_username: str = "",
        target_organization: Organization | None = None,
        membership_request: MembershipRequest | None = None,
    ) -> MembershipLog:
        return cls._create_log(
            actor_username=actor_username, target_username=target_username,
            target_organization=target_organization,
            membership_type=membership_type, membership_request=membership_request,
            action=cls.Action.terminated, expires_at=timezone.now(),
        )

    @classmethod
    def create_for_rejection(
        cls,
        *,
        actor_username: str,
        membership_type: MembershipType,
        rejection_reason: str,
        target_username: str = "",
        target_organization: Organization | None = None,
        membership_request: MembershipRequest | None = None,
    ) -> MembershipLog:
        return cls._create_log(
            actor_username=actor_username, target_username=target_username,
            target_organization=target_organization,
            membership_type=membership_type, membership_request=membership_request,
            action=cls.Action.rejected, rejection_reason=rejection_reason,
        )

    @classmethod
    def create_for_ignore(
        cls,
        *,
        actor_username: str,
        membership_type: MembershipType,
        target_username: str = "",
        target_organization: Organization | None = None,
        membership_request: MembershipRequest | None = None,
    ) -> MembershipLog:
        return cls._create_log(
            actor_username=actor_username, target_username=target_username,
            target_organization=target_organization,
            membership_type=membership_type, membership_request=membership_request,
            action=cls.Action.ignored,
        )



def election_artifact_upload_to(election: Election, filename: str) -> str:
    if not election.pk:
        raise ValueError("Election must be saved before writing artifacts")
    return f"elections/{election.pk}/{filename}"


class ElectionQuerySet(models.QuerySet):
    """Custom queryset for Election with soft-delete awareness."""

    def active(self) -> ElectionQuerySet:
        """Exclude soft-deleted elections.

        Uses the raw string "deleted" because ElectionQuerySet must be defined
        before Election (Django's as_manager() requires it), so we cannot
        reference Election.Status.deleted here. The value is guaranteed to
        match by the Election.Status enum definition below.
        """
        return self.exclude(status="deleted")


class Election(models.Model):
    class Status(models.TextChoices):
        draft = "draft", "Draft"
        open = "open", "Open"
        closed = "closed", "Closed"
        tallied = "tallied", "Tallied"
        deleted = "deleted", "Deleted"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    url = models.URLField(blank=True, default="", max_length=2048)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    number_of_seats = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    quorum = models.PositiveSmallIntegerField(
        default=10,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Minimum turnout percentage required to conclude the election without extension.",
    )
    eligible_group_cn = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Optional FreeIPA group CN. When set, only members of this group will receive voting credentials."
        ),
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.draft)

    # Published machine-readable tally output.
    tally_result = models.JSONField(blank=True, default=dict)

    public_ballots_file = models.FileField(
        upload_to=election_artifact_upload_to,
        blank=True,
        default="",
    )
    public_audit_file = models.FileField(
        upload_to=election_artifact_upload_to,
        blank=True,
        default="",
    )
    artifacts_generated_at = models.DateTimeField(blank=True, null=True)

    # Per-election voting credential email configuration.
    # We snapshot the subject/body at election configuration time so the election can
    # send the exact content the admin reviewed, even if the underlying template changes.
    voting_email_template = models.ForeignKey(
        "post_office.EmailTemplate",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="+",
    )
    voting_email_subject = models.TextField(blank=True, default="")
    voting_email_html = models.TextField(blank=True, default="")
    voting_email_text = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ElectionQuerySet.as_manager()

    class Meta:
        ordering = ("-start_datetime", "id")

    def __str__(self) -> str:
        return self.name


class Candidate(models.Model):
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="candidates")
    freeipa_username = models.CharField(max_length=255)
    nominated_by = models.CharField(
        max_length=255,
        help_text="FreeIPA username of the person who nominated this candidate.",
    )
    description = models.TextField(blank=True, default="")
    url = models.URLField(blank=True, default="", max_length=2048)
    tiebreak_uuid = models.UUIDField(default=uuid.uuid4, editable=False)

    class Meta:
        ordering = ("freeipa_username", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["election", "freeipa_username"],
                name="uniq_candidate_election_username",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.freeipa_username} ({self.election_id})"


class ExclusionGroup(models.Model):
    """A constraint on how many candidates in a set may be elected."""

    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="exclusion_groups")
    name = models.CharField(max_length=255)
    max_elected = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Maximum number of candidates from this group that may be elected.",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    candidates = models.ManyToManyField(Candidate, through="ExclusionGroupCandidate", related_name="exclusion_groups")

    class Meta:
        ordering = ("election", "name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["election", "name"],
                name="uniq_exclusiongroup_election_name",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.election_id}:{self.name}"


class ExclusionGroupCandidate(models.Model):
    exclusion_group = models.ForeignKey(ExclusionGroup, on_delete=models.CASCADE, related_name="group_candidates")
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name="candidate_groups")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["exclusion_group", "candidate"],
                name="uniq_exclusiongroupcandidate_group_candidate",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.exclusion_group_id}:{self.candidate_id}"


class VotingCredential(models.Model):
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="credentials")
    public_id = models.CharField(max_length=128, unique=True, db_index=True)

    # Nullable so we can remove the link post-close to improve privacy.
    freeipa_username = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    weight = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["election", "freeipa_username"],
                name="uniq_credential_election_username",
                condition=Q(freeipa_username__isnull=False),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.election_id}:{self.public_id}"

    @classmethod
    def generate_public_id(cls) -> str:
        return secrets.token_urlsafe(32)


class BallotQuerySet(models.QuerySet["Ballot"]):
    def for_election(self, *, election: Election) -> BallotQuerySet:
        return self.filter(election=election)

    def final(self) -> BallotQuerySet:
        return self.filter(superseded_by__isnull=True)

    def latest_chain_head_hash_for_election(self, *, election: Election) -> str | None:
        return (
            self.for_election(election=election)
            .order_by("-created_at", "-id")
            .values_list("chain_hash", flat=True)
            .first()
        )


class Ballot(models.Model):
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="ballots")

    # Intentionally not a FK to avoid coupling ballots to any user-identifying row.
    credential_public_id = models.CharField(max_length=128, db_index=True)

    # Ordered list of Candidate PKs.
    ranking = models.JSONField(blank=True, default=list)
    weight = models.PositiveIntegerField(default=0)

    superseded_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supersedes",
    )
    is_counted = models.BooleanField(default=True)

    ballot_hash = models.CharField(max_length=64, db_index=True)

    previous_chain_hash = models.CharField(max_length=64)
    chain_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = BallotQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["election", "credential_public_id"],
                name="uniq_ballot_final_election_credential",
                condition=Q(superseded_by__isnull=True),
            ),
            models.UniqueConstraint(
                fields=["election", "credential_public_id"],
                name="uniq_ballot_counted_election_credential",
                condition=Q(is_counted=True),
            ),
        ]
        indexes = [
            models.Index(fields=["election", "created_at"], name="ballot_el_at"),
        ]

    def __str__(self) -> str:
        return f"ballot:{self.election_id}:{self.ballot_hash[:12]}"

    @classmethod
    def compute_hash(
        cls,
        *,
        election_id: int,
        credential_public_id: str,
        ranking: list[int],
        weight: int,
        nonce: str,
    ) -> str:
        payload: dict[str, object] = {
            "election_id": election_id,
            "credential_public_id": credential_public_id,
            "ranking": ranking,
            "weight": weight,
            "nonce": nonce,
        }

        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(data).hexdigest()


class AuditLogEntry(models.Model):
    election = models.ForeignKey(Election, on_delete=models.CASCADE, related_name="audit_log")
    timestamp = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(blank=True, default=dict)
    is_public = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Audit log entries"
        ordering = ("timestamp", "id")
        indexes = [
            models.Index(fields=["election", "timestamp"], name="audit_el_ts"),
            models.Index(fields=["election", "is_public"], name="audit_el_pub"),
        ]

    def __str__(self) -> str:
        return f"{self.election_id}:{self.event_type}"


class FreeIPAPermissionGrant(models.Model):
    """Grant an arbitrary Django permission string to a FreeIPA user or group.

    This intentionally does not use Django's auth.Permission model because:
    - Our users and groups are backed by FreeIPA, not Django DB rows.
    - We want grants like "astra.add_membership" without needing a model.
    """

    class PrincipalType(models.TextChoices):
        user = "user", "User"
        group = "group", "Group"

    permission = models.CharField(max_length=150, db_index=True)
    principal_type = models.CharField(max_length=10, choices=PrincipalType.choices, db_index=True)
    principal_name = models.CharField(max_length=255, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Permission Grant"
        verbose_name_plural = "Permission Grants"
        constraints = [
            models.UniqueConstraint(
                fields=["permission", "principal_type", "principal_name"],
                name="uniq_freeipa_permission_grant",
            )
        ]
        indexes = [
            models.Index(fields=["principal_type", "principal_name"], name="idx_perm_grant_principal"),
        ]

    def __str__(self) -> str:
        return f"{self.permission} -> {self.principal_type}:{self.principal_name}"

    @override
    def save(self, *args, **kwargs) -> None:
        # Normalize for stable matching (FreeIPA names are case-insensitive in practice).
        self.permission = str(self.permission or "").strip().lower()
        self.principal_name = str(self.principal_name or "").strip().lower()
        super().save(*args, **kwargs)


class MembershipCSVImportLink(MembershipType):
    """Admin sidebar link for the one-time membership CSV importer.

    Implemented as a proxy model so it shows up under the `core` app in the
    Django admin sidebar without creating any new DB tables.
    """

    class Meta:
        proxy = True
        verbose_name = "Membership import (CSV)"
        verbose_name_plural = "Membership import (CSV)"
