from __future__ import annotations

import base64
import datetime
import io
import logging
import os
from base64 import b32encode
from typing import Any, Final
from urllib.parse import quote

import post_office.mail
from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.module_loading import import_string
from python_freeipa import ClientMeta, exceptions

from core.agreements import has_enabled_agreements, list_agreements_for_user
from core.backends import FreeIPAFASAgreement, FreeIPAUser
from core.email_context import user_email_context
from core.forms_selfservice import (
    EmailsForm,
    KeysForm,
    OTPAddForm,
    OTPConfirmForm,
    OTPTokenActionForm,
    OTPTokenRenameForm,
    PasswordChangeFreeIPAForm,
    ProfileForm,
    normalize_locale_tag,
)
from core.membership_notes import CUSTOS, add_note
from core.models import MembershipRequest
from core.tokens import make_signed_token, read_signed_token
from core.views_utils import (
    _add_change,
    _add_change_list_setattr,
    _add_change_setattr,
    _bool_from_ipa,
    _bool_to_ipa,
    _data_get,
    _first,
    _form_label_for_attr,
    _get_full_user,
    _normalize_str,
    _split_lines,
    _split_list_field,
    _update_user_attrs,
    _value_to_csv,
    _value_to_text,
    block_action_without_country_code,
    settings_context,
)

logger = logging.getLogger(__name__)


_SETTINGS_TABS: Final[tuple[str, ...]] = (
    "profile",
    "emails",
    "keys",
    "security",
    "agreements",
)

# Must be the same as KEY_LENGTH in ipaserver/plugins/otptoken.py.
# For maximum compatibility, must be a multiple of 5.
OTP_KEY_LENGTH: Final[int] = 35


def _settings_fragment(tab: str) -> str:
    if tab not in _SETTINGS_TABS:
        tab = "profile"
    return f"#{tab}"


def _settings_url(tab: str, *, agreement_cn: str | None = None) -> str:
    base = reverse("settings")
    if agreement_cn is None:
        return f"{base}{_settings_fragment(tab)}"
    return f"{base}?agreement={quote(agreement_cn)}{_settings_fragment(tab)}"


def _block_settings_change_without_country_code(request: HttpRequest, *, user_data: dict | None) -> HttpResponse | None:
    return block_action_without_country_code(request, user_data=user_data, action_label="change settings")




def _send_email_validation_email(
    request: HttpRequest,
    *,
    username: str,
    name: str,
    attr: str,
    email_to_validate: str,
) -> None:
    base_ctx = user_email_context(username=username)
    token = make_signed_token({"u": username, "a": attr, "v": email_to_validate})
    validate_url = request.build_absolute_uri(reverse("settings-email-validate")) + f"?token={quote(token)}"
    ttl_seconds = settings.EMAIL_VALIDATION_TOKEN_TTL_SECONDS
    ttl_minutes = max(1, int((ttl_seconds + 59) / 60))
    valid_until = timezone.now() + datetime.timedelta(seconds=ttl_seconds)
    valid_until_utc = valid_until.astimezone(datetime.UTC).strftime("%H:%M")

    post_office.mail.send(
        recipients=[email_to_validate],
        sender=settings.DEFAULT_FROM_EMAIL,
        template=settings.EMAIL_VALIDATION_EMAIL_TEMPLATE_NAME,
        context={
            **base_ctx,
            "name": name or base_ctx["full_name"],
            "attr": attr,
            "email_to_validate": email_to_validate,
            "validate_url": validate_url,
            "ttl_minutes": ttl_minutes,
            "valid_until_utc": valid_until_utc,
        },
    )


def _detect_avatar_provider(user: object, *, size: int = 140) -> tuple[str | None, str | None]:
    """Return (provider_path, avatar_url) for the first provider that yields a URL."""

    for provider_path in settings.AVATAR_PROVIDERS:
        try:
            provider_cls = import_string(provider_path)
        except Exception:
            continue

        get_url = getattr(provider_cls, "get_avatar_url", None)
        if not callable(get_url):
            continue

        try:
            url = str(get_url(user, size, size)).strip()
        except Exception:
            continue

        if url:
            return provider_path, url

    return None, None


def _avatar_manage_url_for_provider(provider_path: str | None) -> str | None:
    if not provider_path:
        return None

    if provider_path.endswith("LibRAvatarProvider"):
        return "https://www.libravatar.org/"
    if provider_path.endswith("GravatarAvatarProvider"):
        return "https://gravatar.com/"

    return None


def avatar_manage(request: HttpRequest) -> HttpResponse:
    """Redirect the user to the appropriate place to manage their avatar."""

    provider_path, _ = _detect_avatar_provider(request.user)
    manage_url = _avatar_manage_url_for_provider(provider_path)
    if manage_url:
        return redirect(manage_url)

    messages.info(request, "Your current avatar provider does not support direct avatar updates here.")
    return redirect(_settings_url("profile"))


type TokenDict = dict[str, Any]


def settings_root(request: HttpRequest) -> HttpResponse:
    """Unified settings page.

    Tabs are selected by fragment (e.g. /settings/#emails). For form submissions, the
    active tab is posted as a `tab` field.
    """

    username = request.user.get_username()
    requested_tab = _normalize_str(request.POST.get("tab") or request.GET.get("tab")) or "profile"
    if requested_tab not in _SETTINGS_TABS:
        requested_tab = "profile"

    is_save_all = request.method == "POST" and _normalize_str(request.POST.get("save_all")) == "1"

    fu = _get_full_user(username)
    if not fu:
        messages.error(request, "Unable to load your FreeIPA profile.")
        return redirect("home")

    data = fu._user_data

    country_attr = str(settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR).strip() or "c"

    # --- Profile ---
    # Use the FreeIPA attribute data as the source of truth. This avoids relying on
    # extra convenience properties that may not exist on mocked/user objects.
    profile_initial = {
        "givenname": _first(data, "givenname", "") or "",
        "sn": _first(data, "sn", "") or "",
        "country_code": _first(data, country_attr, "") or "",
        "fasPronoun": _value_to_csv(_data_get(data, "fasPronoun", "")),
        "fasLocale": normalize_locale_tag(_first(data, "fasLocale", "") or ""),
        "fasTimezone": _first(data, "fasTimezone", "") or "",
        "fasWebsiteUrl": _value_to_text(_data_get(data, "fasWebsiteUrl", "")),
        "fasRssUrl": _value_to_text(_data_get(data, "fasRssUrl", "")),
        "fasIRCNick": _value_to_text(_data_get(data, "fasIRCNick", "")),
        "fasGitHubUsername": _first(data, "fasGitHubUsername", "") or "",
        "fasGitLabUsername": _first(data, "fasGitLabUsername", "") or "",
        "fasIsPrivate": _bool_from_ipa(_data_get(data, "fasIsPrivate", "FALSE"), default=False),
    }
    profile_form = ProfileForm(
        request.POST if request.method == "POST" and (is_save_all or requested_tab == "profile") else None,
        request.FILES if request.method == "POST" and (is_save_all or requested_tab == "profile") else None,
        initial=profile_initial,
    )

    # --- Emails ---
    emails_initial = {
        "mail": _first(data, "mail", "") or "",
        "fasRHBZEmail": _first(data, "fasRHBZEmail", "") or "",
    }
    emails_form = EmailsForm(
        request.POST if request.method == "POST" and (is_save_all or requested_tab == "emails") else None,
        initial=emails_initial,
    )

    # --- Keys ---
    gpg = _data_get(data, "fasGPGKeyId", [])
    ssh = _data_get(data, "ipasshpubkey", [])
    if isinstance(gpg, str):
        gpg = [gpg]
    if isinstance(ssh, str):
        ssh = [ssh]
    keys_initial = {
        "fasGPGKeyId": "\n".join(gpg or []),
        "ipasshpubkey": "\n".join(ssh or []),
    }
    keys_form = KeysForm(
        request.POST if request.method == "POST" and (is_save_all or requested_tab == "keys") else None,
        initial=keys_initial,
    )

    # --- Security (Password + OTP) ---
    password_form = PasswordChangeFreeIPAForm(
        request.POST if request.method == "POST" and requested_tab == "security" else None
    )

    # Fast-path: for password-change posts we should not touch OTP/agreement code.
    # Those code paths can trigger FreeIPA service-account network calls, and they
    # are unrelated to changing a user's password.
    if (
        request.method == "POST"
        and requested_tab == "security"
        and "add-submit" not in request.POST
        and "confirm-submit" not in request.POST
        and password_form.is_valid()
    ):
        blocked = _block_settings_change_without_country_code(request, user_data=data)
        if blocked is not None:
            return blocked

        current = password_form.cleaned_data["current_password"]
        otp = _normalize_str(password_form.cleaned_data.get("otp")) or None
        new = password_form.cleaned_data["new_password"]

        try:
            client = ClientMeta(host=settings.FREEIPA_HOST, verify_ssl=settings.FREEIPA_VERIFY_SSL)

            # Prefer the password-change endpoint: it works for self-service password changes
            # and supports OTP validation.
            change_password = getattr(client, "change_password", None)
            if callable(change_password):
                # python-freeipa signature: change_password(username, new_password, old_password, otp=None)
                change_password(username, new, current, otp=otp)
            else:
                # Fallback for very old python-freeipa versions.
                client.login(username, current)
                passwd = getattr(client, "passwd", None)
                if not callable(passwd):
                    raise RuntimeError("python-freeipa client does not support password changes")
                try:
                    passwd(username, current, new)
                except TypeError:
                    passwd(username, o_password=current, o_new_password=new)

            messages.success(request, "Password changed.")
            return redirect(_settings_url("security"))
        except exceptions.PWChangePolicyError as e:
            logger.info("Password change rejected by policy username=%s error=%s", username, e)
            messages.error(request, "Password change rejected by policy. Please choose a stronger password.")
            return redirect(_settings_url("security"))
        except exceptions.PWChangeInvalidPassword:
            # Most commonly: wrong/missing OTP for OTP-enabled accounts.
            logger.info("Password change rejected (invalid current password/OTP) username=%s", username)
            messages.error(request, "Incorrect current password or OTP.")
            return redirect(_settings_url("security"))
        except exceptions.PasswordExpired:
            messages.error(request, "Password is expired; please change it below.")
            return redirect(_settings_url("security"))
        except (exceptions.InvalidSessionPassword, exceptions.Unauthorized):
            # Treat auth failures as a normal user error, not a crash.
            logger.info("Password change rejected (bad credentials) username=%s", username)
            messages.error(request, "Incorrect current password or OTP.")
            return redirect(_settings_url("security"))
        except exceptions.FreeIPAError as e:
            logger.warning("Password change failed (FreeIPA error) username=%s error=%s", username, e)
            messages.error(request, "Unable to change password due to a FreeIPA error.")
            return redirect(_settings_url("security"))
        except Exception:
            logger.exception("Failed to change password username=%s", username)
            messages.error(request, "Failed to change password due to an internal error.")
            return redirect(_settings_url("security"))

    using_otp = False
    try:
        res = FreeIPAUser.get_client().otptoken_find(o_ipatokenowner=username, o_all=True)
        using_otp = bool((res or {}).get("result"))
    except Exception:
        using_otp = False

    is_add = requested_tab == "security" and request.method == "POST" and "add-submit" in request.POST
    is_confirm = requested_tab == "security" and request.method == "POST" and "confirm-submit" in request.POST
    otp_add_form = OTPAddForm(request.POST if is_add else None, prefix="add")
    otp_confirm_form = OTPConfirmForm(request.POST if is_confirm else None, prefix="confirm")

    tokens: list[TokenDict] = []
    otp_uri: str | None = None
    otp_qr_png_b64: str | None = None

    def _service_client() -> ClientMeta:
        c = ClientMeta(host=settings.FREEIPA_HOST, verify_ssl=settings.FREEIPA_VERIFY_SSL)
        c.login(settings.FREEIPA_SERVICE_USER, settings.FREEIPA_SERVICE_PASSWORD)
        return c

    def _user_can_reauth(password: str) -> bool:
        c = ClientMeta(host=settings.FREEIPA_HOST, verify_ssl=settings.FREEIPA_VERIFY_SSL)
        c.login(username, password)
        return True

    try:
        svc = _service_client()
        res = svc.otptoken_find(o_ipatokenowner=username, o_all=True)
        tokens = res.get("result", []) if isinstance(res, dict) else []
    except Exception:
        tokens = []

    normalized_tokens: list[TokenDict] = []
    for raw in tokens:
        if not isinstance(raw, dict):
            continue
        token_dict: TokenDict = dict(raw)

        description = token_dict.get("description")
        if isinstance(description, list):
            description = description[0] if description else ""
        token_dict["description"] = str(description).strip() if description else ""

        token_id = token_dict.get("ipatokenuniqueid")
        if isinstance(token_id, list):
            out: list[str] = []
            for v in token_id:
                s = str(v).strip()
                if s:
                    out.append(s)
            token_dict["ipatokenuniqueid"] = out
        elif token_id:
            token_dict["ipatokenuniqueid"] = [str(token_id).strip()]
        else:
            token_dict["ipatokenuniqueid"] = []

        normalized_tokens.append(token_dict)
    tokens = normalized_tokens
    tokens.sort(key=lambda t: str(t.get("description") or "").casefold())

    secret: str | None = None
    if is_add and otp_add_form.is_valid():
        description = _normalize_str(otp_add_form.cleaned_data.get("description"))
        password = otp_add_form.cleaned_data.get("password") or ""
        otp = _normalize_str(otp_add_form.cleaned_data.get("otp"))
        if otp:
            password = f"{password}{otp}"

        try:
            _user_can_reauth(password)
        except exceptions.InvalidSessionPassword:
            otp_add_form.add_error("password", "Incorrect password")
        except exceptions.Unauthorized:
            otp_add_form.add_error("password", "Incorrect password")
        except Exception as e:
            if settings.DEBUG:
                otp_add_form.add_error(None, f"Unable to reauthenticate (debug): {e}")
            else:
                otp_add_form.add_error(None, "Unable to reauthenticate due to an internal error.")
        else:
            # Must match KEY_LENGTH in ipaserver/plugins/otptoken.py (multiple of 5).
            secret = b32encode(os.urandom(OTP_KEY_LENGTH)).decode("ascii")
            otp_confirm_form = OTPConfirmForm(
                initial={
                    "secret": secret,
                    "description": description,
                },
                prefix="confirm",
            )

    if is_confirm:
        secret = _normalize_str(request.POST.get("confirm-secret")) or None

        if otp_confirm_form.is_valid():
            blocked = _block_settings_change_without_country_code(request, user_data=data)
            if blocked is not None:
                return blocked

            description = _normalize_str(otp_confirm_form.cleaned_data.get("description"))
            try:
                svc = _service_client()
                svc.otptoken_add(
                    o_ipatokenowner=username,
                    o_description=description,
                    o_type="totp",
                    o_ipatokenotpkey=otp_confirm_form.cleaned_data["secret"],
                )
            except exceptions.FreeIPAError:
                otp_confirm_form.add_error(None, "Cannot create the token.")
            except Exception as e:
                if settings.DEBUG:
                    otp_confirm_form.add_error(None, f"Cannot create the token (debug): {e}")
                else:
                    otp_confirm_form.add_error(None, "Cannot create the token.")
            else:
                messages.success(request, "The token has been created.")
                return redirect(_settings_url("security"))

    if secret:
        try:
            import pyotp
            import qrcode
        except Exception:
            pyotp = None
            qrcode = None

        if pyotp and qrcode:
            host = settings.FREEIPA_HOST
            parts = host.split(".")
            realm = ".".join(parts[1:]).upper() if len(parts) > 1 else host.upper()
            issuer = f"{username}@{realm}" if realm else username

            if is_confirm:
                description = _normalize_str(request.POST.get("confirm-description"))
            elif is_add:
                description = _normalize_str(otp_add_form.cleaned_data.get("description"))
            else:
                description = (otp_confirm_form.initial or {}).get("description") or ""
                description = _normalize_str(description)

            totp = pyotp.TOTP(secret)
            otp_uri = str(totp.provisioning_uri(name=description or "(no name)", issuer_name=issuer))

            qr = qrcode.QRCode(box_size=6, border=2)
            qr.add_data(otp_uri)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            otp_qr_png_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    # --- Agreements ---
    agreement_cn = _normalize_str(request.GET.get("agreement")) or None
    agreement: FreeIPAFASAgreement | None = None
    agreement_signed = False
    agreements = []

    try:
        show_agreements_tab = has_enabled_agreements()
    except Exception:
        # If FreeIPA is unreachable, degrade gracefully (e.g. tests, dev).
        show_agreements_tab = False

    if show_agreements_tab:
        # In unit tests `_get_full_user` is sometimes patched with a lightweight
        # object; group membership is optional for agreement applicability.
        user_groups = fu.groups_list if hasattr(fu, "groups_list") else []
        try:
            agreements = list_agreements_for_user(
                username,
                user_groups=user_groups,
                include_disabled=False,
                applicable_only=False,
            )
            if agreement_cn:
                agreement = FreeIPAFASAgreement.get(agreement_cn)
                if not agreement or not agreement.enabled:
                    raise Http404("Agreement not found")
                agreement_signed = username in set(agreement.users)
        except Http404:
            raise
        except Exception:
            # Same rationale as above: failing to fetch agreements should not
            # prevent users from managing other settings.
            show_agreements_tab = False
            agreements = []
            agreement = None
            agreement_signed = False

    if not show_agreements_tab and (requested_tab == "agreements" or agreement_cn):
        # If agreements are not available (disabled or FreeIPA unreachable),
        # do not render a broken/empty tab. Keep the user on a safe tab.
        return redirect(_settings_url("profile"))

    # Context
    context = {
        "tabs": list(_SETTINGS_TABS),
        "active_tab": requested_tab,
        "profile_form": profile_form,
        "emails_form": emails_form,
        "keys_form": keys_form,
        "password_form": password_form,
        "using_otp": using_otp,
        "otp_add_form": otp_add_form,
        "otp_confirm_form": otp_confirm_form,
        "otp_tokens": tokens,
        "otp_uri": otp_uri,
        "otp_qr_png_b64": otp_qr_png_b64,
        "agreements": agreements,
        "agreement": agreement,
        "agreement_signed": agreement_signed,
        "agreement_cn": agreement_cn,
        "rename_form": OTPTokenRenameForm(prefix="rename"),
        "show_agreements_tab": show_agreements_tab,
    }

    # Compatibility: some tests and older code expect `form` to be the active tab's primary form.
    context["form"] = {
        "profile": profile_form,
        "emails": emails_form,
        "keys": keys_form,
        "security": password_form,
        "agreements": None,
    }.get(requested_tab)

    if request.method != "POST":
        return render(request, "core/settings.html", context)

    if is_save_all:
        # Validate only the forms that have actually changed. Otherwise, a user
        # could be blocked from saving a country code just because an unrelated
        # tab has missing/invalid required fields.
        profile_changed = profile_form.has_changed()
        emails_changed = emails_form.has_changed()
        keys_changed = keys_form.has_changed()

        invalid_tab: str | None = None
        if profile_changed and not profile_form.is_valid():
            invalid_tab = "profile"
        elif emails_changed and not emails_form.is_valid():
            invalid_tab = "emails"
        elif keys_changed and not keys_form.is_valid():
            invalid_tab = "keys"

        if invalid_tab is not None:
            context["active_tab"] = invalid_tab
            # On POST error renders, the current URL fragment (e.g. #profile) can
            # override the server-selected tab via client-side JS. Force the UI to
            # show the tab that contains the validation errors.
            context["force_tab"] = invalid_tab
            context["form"] = {
                "profile": profile_form,
                "emails": emails_form,
                "keys": keys_form,
                "security": password_form,
                "agreements": None,
            }.get(invalid_tab)
            return render(request, "core/settings.html", context)

        # --- Build changes for each tab ---
        old_country = str(profile_initial.get("country_code") or "").strip().upper()
        new_country = ""
        profile_direct_updates: dict[str, object] = {}
        profile_addattrs: list[str] = []
        profile_setattrs: list[str] = []
        profile_delattrs: list[str] = []
        if profile_changed:
            new_country = str(profile_form.cleaned_data.get("country_code") or "").strip().upper()
            _add_change(
                updates=profile_direct_updates,
                delattrs=profile_delattrs,
                attr="givenname",
                current_value=profile_initial.get("givenname"),
                new_value=profile_form.cleaned_data["givenname"],
            )
            _add_change(
                updates=profile_direct_updates,
                delattrs=profile_delattrs,
                attr="sn",
                current_value=profile_initial.get("sn"),
                new_value=profile_form.cleaned_data["sn"],
            )
            new_cn = (
                f"{profile_form.cleaned_data['givenname']} {profile_form.cleaned_data['sn']}".strip() or username
            )
            current_cn = _first(data, "cn", "")
            _add_change(
                updates=profile_direct_updates,
                delattrs=profile_delattrs,
                attr="cn",
                current_value=current_cn,
                new_value=new_cn,
            )

            _add_change_list_setattr(
                addattrs=profile_addattrs,
                setattrs=profile_setattrs,
                delattrs=profile_delattrs,
                attr="fasPronoun",
                current_values=_data_get(data, "fasPronoun", []),
                new_values=_split_list_field(profile_form.cleaned_data["fasPronoun"]),
            )
            _add_change_setattr(
                setattrs=profile_setattrs,
                delattrs=profile_delattrs,
                attr="fasLocale",
                current_value=normalize_locale_tag(profile_initial.get("fasLocale")),
                new_value=normalize_locale_tag(profile_form.cleaned_data["fasLocale"]),
            )
            _add_change_setattr(
                setattrs=profile_setattrs,
                delattrs=profile_delattrs,
                attr="fasTimezone",
                current_value=profile_initial.get("fasTimezone"),
                new_value=profile_form.cleaned_data["fasTimezone"],
            )

            _add_change_list_setattr(
                addattrs=profile_addattrs,
                setattrs=profile_setattrs,
                delattrs=profile_delattrs,
                attr="fasWebsiteUrl",
                current_values=_data_get(data, "fasWebsiteUrl", []),
                new_values=_split_list_field(profile_form.cleaned_data["fasWebsiteUrl"]),
            )
            _add_change_list_setattr(
                addattrs=profile_addattrs,
                setattrs=profile_setattrs,
                delattrs=profile_delattrs,
                attr="fasRssUrl",
                current_values=_data_get(data, "fasRssUrl", []),
                new_values=_split_list_field(profile_form.cleaned_data["fasRssUrl"]),
            )
            _add_change_list_setattr(
                addattrs=profile_addattrs,
                setattrs=profile_setattrs,
                delattrs=profile_delattrs,
                attr="fasIRCNick",
                current_values=_data_get(data, "fasIRCNick", []),
                new_values=_split_list_field(profile_form.cleaned_data["fasIRCNick"]),
            )
            _add_change_setattr(
                setattrs=profile_setattrs,
                delattrs=profile_delattrs,
                attr="fasGitHubUsername",
                current_value=profile_initial.get("fasGitHubUsername"),
                new_value=profile_form.cleaned_data["fasGitHubUsername"],
            )
            _add_change_setattr(
                setattrs=profile_setattrs,
                delattrs=profile_delattrs,
                attr="fasGitLabUsername",
                current_value=profile_initial.get("fasGitLabUsername"),
                new_value=profile_form.cleaned_data["fasGitLabUsername"],
            )
            _add_change_setattr(
                setattrs=profile_setattrs,
                delattrs=profile_delattrs,
                attr=country_attr,
                current_value=profile_initial.get("country_code"),
                new_value=profile_form.cleaned_data["country_code"],
                transform=str.upper,
            )

            current_private = profile_initial["fasIsPrivate"]
            new_private = profile_form.cleaned_data["fasIsPrivate"]
            if current_private != new_private:
                profile_setattrs.append(f"fasIsPrivate={_bool_to_ipa(new_private)}")

        emails_direct_updates: dict[str, object] = {}
        emails_setattrs: list[str] = []
        emails_delattrs: list[str] = []
        pending_validations: list[tuple[str, str]] = []
        if emails_changed:
            current_mail = _normalize_str(emails_initial.get("mail")).lower()
            new_mail = _normalize_str(emails_form.cleaned_data["mail"]).lower()
            current_rhbz = _normalize_str(emails_initial.get("fasRHBZEmail")).lower()
            new_rhbz = _normalize_str(emails_form.cleaned_data["fasRHBZEmail"]).lower()

            if current_mail != new_mail and new_mail:
                if current_rhbz == new_mail and current_rhbz:
                    emails_direct_updates["o_mail"] = new_mail
                else:
                    pending_validations.append(("mail", new_mail))

            if current_rhbz != new_rhbz:
                if new_rhbz:
                    if current_mail == new_rhbz and current_mail:
                        _add_change_setattr(
                            setattrs=emails_setattrs,
                            delattrs=emails_delattrs,
                            attr="fasRHBZEmail",
                            current_value=current_rhbz,
                            new_value=new_rhbz,
                        )
                    else:
                        pending_validations.append(("fasRHBZEmail", new_rhbz))
                else:
                    _add_change_setattr(
                        setattrs=emails_setattrs,
                        delattrs=emails_delattrs,
                        attr="fasRHBZEmail",
                        current_value=current_rhbz,
                        new_value=new_rhbz,
                    )

        keys_addattrs: list[str] = []
        keys_setattrs: list[str] = []
        keys_delattrs: list[str] = []
        if keys_changed:
            _add_change_list_setattr(
                addattrs=keys_addattrs,
                setattrs=keys_setattrs,
                delattrs=keys_delattrs,
                attr="fasGPGKeyId",
                current_values=_data_get(data, "fasGPGKeyId", []),
                new_values=_split_lines(keys_form.cleaned_data["fasGPGKeyId"]),
            )
            _add_change_list_setattr(
                addattrs=keys_addattrs,
                setattrs=keys_setattrs,
                delattrs=keys_delattrs,
                attr="ipasshpubkey",
                current_values=_data_get(data, "ipasshpubkey", []),
                new_values=_split_lines(keys_form.cleaned_data["ipasshpubkey"]),
            )

        profile_has_changes = bool(profile_direct_updates or profile_addattrs or profile_setattrs or profile_delattrs)
        emails_has_changes = bool(pending_validations or emails_direct_updates or emails_setattrs or emails_delattrs)
        keys_has_changes = bool(keys_addattrs or keys_setattrs or keys_delattrs)

        if not (profile_has_changes or emails_has_changes or keys_has_changes):
            messages.info(request, "No changes to save.")
            return redirect(_settings_url(requested_tab))

        if profile_has_changes or emails_has_changes or keys_has_changes:
            # In save-all mode, allow the submitted profile country code to satisfy
            # the country requirement for other settings in the same request.
            effective_user_data = dict(data)
            if new_country:
                effective_user_data[country_attr] = [new_country]
                if country_attr.lower() != country_attr:
                    effective_user_data[country_attr.lower()] = [new_country]

            blocked = _block_settings_change_without_country_code(request, user_data=effective_user_data)
            if blocked is not None:
                return blocked

        try:
            if profile_has_changes:
                skipped, applied = _update_user_attrs(
                    username,
                    direct_updates=profile_direct_updates,
                    addattrs=profile_addattrs,
                    setattrs=profile_setattrs,
                    delattrs=profile_delattrs,
                )
                if skipped:
                    for attr in skipped:
                        label = (
                            profile_form.fields["country_code"].label
                            if attr == country_attr
                            else _form_label_for_attr(profile_form, attr)
                        )
                        messages.warning(
                            request,
                            f"Saved, but '{label or attr}' is not editable on this FreeIPA server.",
                        )
                if applied:
                    messages.success(request, "Profile updated in FreeIPA.")

                    if old_country and new_country and old_country != new_country and country_attr not in (skipped or []):
                        pending = list(
                            MembershipRequest.objects.filter(
                                requested_username=username,
                                status=MembershipRequest.Status.pending,
                            ).only("pk")
                        )
                        if pending:
                            for mr in pending:
                                try:
                                    add_note(
                                        membership_request=mr,
                                        username=CUSTOS,
                                        content=f"{username} updated their country from {old_country} to {new_country}.",
                                    )
                                except Exception:
                                    logger.exception(
                                        "Failed to record country-change system note request_id=%s username=%s",
                                        mr.pk,
                                        username,
                                    )
                else:
                    messages.info(request, "No profile changes were applied.")

            if emails_has_changes:
                if emails_direct_updates or emails_setattrs or emails_delattrs:
                    skipped, applied = _update_user_attrs(
                        username,
                        direct_updates=emails_direct_updates,
                        setattrs=emails_setattrs,
                        delattrs=emails_delattrs,
                    )
                    if skipped:
                        for attr in skipped:
                            label = _form_label_for_attr(emails_form, attr)
                            messages.warning(
                                request,
                                f"Saved, but '{label or attr}' is not editable on this FreeIPA server.",
                            )
                    if applied:
                        messages.success(request, "Email settings updated in FreeIPA.")
                    else:
                        messages.info(request, "No email settings changes were applied.")

                if pending_validations:
                    name = fu.full_name
                    for attr, address in pending_validations:
                        _send_email_validation_email(
                            request,
                            username=username,
                            name=name,
                            attr=attr,
                            email_to_validate=address,
                        )
                    messages.success(
                        request,
                        "We sent you an email to validate your new email address. Please check your inbox.",
                    )

            if keys_has_changes:
                skipped, applied = _update_user_attrs(
                    username,
                    addattrs=keys_addattrs,
                    setattrs=keys_setattrs,
                    delattrs=keys_delattrs,
                )
                if skipped:
                    for attr in skipped:
                        label = _form_label_for_attr(keys_form, attr)
                        messages.warning(
                            request,
                            f"Saved, but '{label or attr}' is not editable on this FreeIPA server.",
                        )
                if applied:
                    messages.success(request, "Keys updated in FreeIPA.")
                else:
                    messages.info(request, "No key changes were applied.")

        except Exception as e:
            logger.exception("Failed to update settings username=%s", username)
            if settings.DEBUG:
                messages.error(request, f"Failed to update settings (debug): {e}")
            else:
                messages.error(request, "Failed to update settings due to an internal error.")
            context["force_tab"] = requested_tab
            return render(request, "core/settings.html", context)

        return redirect(_settings_url(requested_tab))

    if requested_tab == "profile" and profile_form.is_valid():
        direct_updates: dict[str, object] = {}
        addattrs: list[str] = []
        setattrs: list[str] = []
        delattrs: list[str] = []

        old_country = str(profile_initial.get("country_code") or "").strip().upper()
        new_country = str(profile_form.cleaned_data.get("country_code") or "").strip().upper()

        _add_change(
            updates=direct_updates,
            delattrs=delattrs,
            attr="givenname",
            current_value=profile_initial.get("givenname"),
            new_value=profile_form.cleaned_data["givenname"],
        )
        _add_change(
            updates=direct_updates,
            delattrs=delattrs,
            attr="sn",
            current_value=profile_initial.get("sn"),
            new_value=profile_form.cleaned_data["sn"],
        )
        new_cn = f"{profile_form.cleaned_data['givenname']} {profile_form.cleaned_data['sn']}".strip() or username
        current_cn = _first(data, "cn", "")
        _add_change(
            updates=direct_updates,
            delattrs=delattrs,
            attr="cn",
            current_value=current_cn,
            new_value=new_cn,
        )

        _add_change_list_setattr(
            addattrs=addattrs,
            setattrs=setattrs,
            delattrs=delattrs,
            attr="fasPronoun",
            current_values=_data_get(data, "fasPronoun", []),
            new_values=_split_list_field(profile_form.cleaned_data["fasPronoun"]),
        )
        _add_change_setattr(
            setattrs=setattrs,
            delattrs=delattrs,
            attr="fasLocale",
            current_value=normalize_locale_tag(profile_initial.get("fasLocale")),
            new_value=normalize_locale_tag(profile_form.cleaned_data["fasLocale"]),
        )
        _add_change_setattr(
            setattrs=setattrs,
            delattrs=delattrs,
            attr="fasTimezone",
            current_value=profile_initial.get("fasTimezone"),
            new_value=profile_form.cleaned_data["fasTimezone"],
        )

        _add_change_list_setattr(
            addattrs=addattrs,
            setattrs=setattrs,
            delattrs=delattrs,
            attr="fasWebsiteUrl",
            current_values=_data_get(data, "fasWebsiteUrl", []),
            new_values=_split_list_field(profile_form.cleaned_data["fasWebsiteUrl"]),
        )
        _add_change_list_setattr(
            addattrs=addattrs,
            setattrs=setattrs,
            delattrs=delattrs,
            attr="fasRssUrl",
            current_values=_data_get(data, "fasRssUrl", []),
            new_values=_split_list_field(profile_form.cleaned_data["fasRssUrl"]),
        )
        _add_change_list_setattr(
            addattrs=addattrs,
            setattrs=setattrs,
            delattrs=delattrs,
            attr="fasIRCNick",
            current_values=_data_get(data, "fasIRCNick", []),
            new_values=_split_list_field(profile_form.cleaned_data["fasIRCNick"]),
        )
        _add_change_setattr(
            setattrs=setattrs,
            delattrs=delattrs,
            attr="fasGitHubUsername",
            current_value=profile_initial.get("fasGitHubUsername"),
            new_value=profile_form.cleaned_data["fasGitHubUsername"],
        )
        _add_change_setattr(
            setattrs=setattrs,
            delattrs=delattrs,
            attr="fasGitLabUsername",
            current_value=profile_initial.get("fasGitLabUsername"),
            new_value=profile_form.cleaned_data["fasGitLabUsername"],
        )
        _add_change_setattr(
            setattrs=setattrs,
            delattrs=delattrs,
            attr=country_attr,
            current_value=profile_initial.get("country_code"),
            new_value=profile_form.cleaned_data["country_code"],
            transform=str.upper,
        )

        current_private = profile_initial["fasIsPrivate"]
        new_private = profile_form.cleaned_data["fasIsPrivate"]
        if current_private != new_private:
            setattrs.append(f"fasIsPrivate={_bool_to_ipa(new_private)}")

        if not direct_updates and not addattrs and not setattrs and not delattrs:
            messages.info(request, "No changes to save.")
            return redirect(_settings_url("profile"))

        effective_user_data = dict(data)
        if new_country:
            effective_user_data[country_attr] = [new_country]
            if country_attr.lower() != country_attr:
                effective_user_data[country_attr.lower()] = [new_country]

        blocked = _block_settings_change_without_country_code(request, user_data=effective_user_data)
        if blocked is not None:
            return blocked

        try:
            skipped, applied = _update_user_attrs(
                username,
                direct_updates=direct_updates,
                addattrs=addattrs,
                setattrs=setattrs,
                delattrs=delattrs,
            )
        except Exception as e:
            logger.exception("Failed to update profile username=%s", username)
            if settings.DEBUG:
                messages.error(request, f"Failed to update profile (debug): {e}")
            else:
                messages.error(request, "Failed to update profile due to an internal error.")
            context["force_tab"] = "profile"
            return render(request, "core/settings.html", context)

        if skipped:
            for attr in skipped:
                label = (
                    profile_form.fields["country_code"].label
                    if attr == country_attr
                    else _form_label_for_attr(profile_form, attr)
                )
                messages.warning(request, f"Saved, but '{label or attr}' is not editable on this FreeIPA server.")
        if applied:
            messages.success(request, "Profile updated in FreeIPA.")

            if old_country and new_country and old_country != new_country and country_attr not in (skipped or []):
                pending = list(
                    MembershipRequest.objects.filter(
                        requested_username=username,
                        status=MembershipRequest.Status.pending,
                    ).only("pk")
                )
                if pending:
                    for mr in pending:
                        try:
                            add_note(
                                membership_request=mr,
                                username=CUSTOS,
                                content=f"{username} updated their country from {old_country} to {new_country}.",
                            )
                        except Exception:
                            logger.exception(
                                "Failed to record country-change system note request_id=%s username=%s",
                                mr.pk,
                                username,
                            )
        else:
            messages.info(request, "No changes were applied.")
        return redirect(_settings_url("profile"))

    if requested_tab == "emails" and emails_form.is_valid():
        direct_updates: dict[str, object] = {}
        setattrs: list[str] = []
        delattrs: list[str] = []
        pending_validations: list[tuple[str, str]] = []

        current_mail = _normalize_str(emails_initial.get("mail")).lower()
        new_mail = _normalize_str(emails_form.cleaned_data["mail"]).lower()
        current_rhbz = _normalize_str(emails_initial.get("fasRHBZEmail")).lower()
        new_rhbz = _normalize_str(emails_form.cleaned_data["fasRHBZEmail"]).lower()

        if current_mail != new_mail and new_mail:
            if _normalize_str(current_rhbz).lower() == new_mail and current_rhbz:
                direct_updates["o_mail"] = new_mail
            else:
                pending_validations.append(("mail", new_mail))

        if current_rhbz != new_rhbz:
            if new_rhbz:
                if _normalize_str(current_mail).lower() == new_rhbz and current_mail:
                    _add_change_setattr(
                        setattrs=setattrs,
                        delattrs=delattrs,
                        attr="fasRHBZEmail",
                        current_value=current_rhbz,
                        new_value=new_rhbz,
                    )
                else:
                    pending_validations.append(("fasRHBZEmail", new_rhbz))
            else:
                _add_change_setattr(
                    setattrs=setattrs,
                    delattrs=delattrs,
                    attr="fasRHBZEmail",
                    current_value=current_rhbz,
                    new_value=new_rhbz,
                )

        if not pending_validations and not direct_updates and not setattrs and not delattrs:
            messages.info(request, "No changes to save.")
            return redirect(_settings_url("emails"))

        blocked = _block_settings_change_without_country_code(request, user_data=data)
        if blocked is not None:
            return blocked

        try:
            if direct_updates or setattrs or delattrs:
                skipped, applied = _update_user_attrs(
                    username,
                    direct_updates=direct_updates,
                    setattrs=setattrs,
                    delattrs=delattrs,
                )
                if skipped:
                    for attr in skipped:
                        label = _form_label_for_attr(emails_form, attr)
                        messages.warning(request, f"Saved, but '{label or attr}' is not editable on this FreeIPA server.")
                if applied:
                    messages.success(request, "Email settings updated in FreeIPA.")
                else:
                    messages.info(request, "No changes were applied.")

            if pending_validations:
                name = fu.full_name
                for attr, address in pending_validations:
                    _send_email_validation_email(
                        request,
                        username=username,
                        name=name,
                        attr=attr,
                        email_to_validate=address,
                    )
                messages.success(
                    request,
                    "We sent you an email to validate your new email address. Please check your inbox.",
                )

        except Exception as e:
            logger.exception("Failed to update email settings username=%s", username)
            if settings.DEBUG:
                messages.error(request, f"Failed to update email settings (debug): {e}")
            else:
                messages.error(request, "Failed to update email settings due to an internal error.")
            context["force_tab"] = "emails"
            return render(request, "core/settings.html", context)

        return redirect(_settings_url("emails"))

    if requested_tab == "keys" and keys_form.is_valid():
        direct_updates: dict[str, object] = {}
        addattrs: list[str] = []
        setattrs: list[str] = []
        delattrs: list[str] = []

        _add_change_list_setattr(
            addattrs=addattrs,
            setattrs=setattrs,
            delattrs=delattrs,
            attr="fasGPGKeyId",
            current_values=_data_get(data, "fasGPGKeyId", []),
            new_values=_split_lines(keys_form.cleaned_data["fasGPGKeyId"]),
        )
        _add_change_list_setattr(
            addattrs=addattrs,
            setattrs=setattrs,
            delattrs=delattrs,
            attr="ipasshpubkey",
            current_values=_data_get(data, "ipasshpubkey", []),
            new_values=_split_lines(keys_form.cleaned_data["ipasshpubkey"]),
        )

        if not direct_updates and not addattrs and not setattrs and not delattrs:
            messages.info(request, "No changes to save.")
            return redirect(_settings_url("keys"))

        blocked = _block_settings_change_without_country_code(request, user_data=data)
        if blocked is not None:
            return blocked

        try:
            skipped, applied = _update_user_attrs(
                username,
                direct_updates=direct_updates,
                addattrs=addattrs,
                setattrs=setattrs,
                delattrs=delattrs,
            )
        except Exception as e:
            logger.exception("Failed to update keys username=%s", username)
            if settings.DEBUG:
                messages.error(request, f"Failed to update keys (debug): {e}")
            else:
                messages.error(request, "Failed to update keys due to an internal error.")
            context["force_tab"] = "keys"
            return render(request, "core/settings.html", context)

        if skipped:
            for attr in skipped:
                label = _form_label_for_attr(keys_form, attr)
                messages.warning(request, f"Saved, but '{label or attr}' is not editable on this FreeIPA server.")
        if applied:
            messages.success(request, "Keys updated in FreeIPA.")
        else:
            messages.info(request, "No changes were applied.")
        return redirect(_settings_url("keys"))

    if requested_tab == "security":
        if is_add or is_confirm:
            context["force_tab"] = "security"
            return render(request, "core/settings.html", context)

        context["force_tab"] = "security"
        return render(request, "core/settings.html", context)

    if requested_tab == "agreements":
        if not has_enabled_agreements():
            return redirect(_settings_url("profile"))

        action = _normalize_str(request.POST.get("action")).lower()
        cn = _normalize_str(request.POST.get("cn"))
        if action == "sign" and cn:
            agreement_obj = FreeIPAFASAgreement.get(cn)
            if not agreement_obj or not agreement_obj.enabled:
                raise Http404("Agreement not found")
            if username in set(agreement_obj.users):
                messages.info(request, "You have already signed this agreement.")
                return redirect(_settings_url("agreements"))

            try:
                agreement_obj.add_user(username)
                messages.success(request, "Agreement signed.")
            except Exception as e:
                logger.exception("Failed to sign agreement username=%s agreement=%s", username, cn)
                if settings.DEBUG:
                    messages.error(request, f"Failed to sign agreement (debug): {e}")
                else:
                    messages.error(request, "Failed to sign agreement due to an internal error.")

        return redirect(_settings_url("agreements"))

    context["force_tab"] = requested_tab
    return render(request, "core/settings.html", context)


def security_otp_enable(request: HttpRequest) -> HttpResponse:
    fu = FreeIPAUser.get(request.user.get_username())
    blocked = _block_settings_change_without_country_code(request, user_data=fu._user_data if fu else None)
    if blocked is not None:
        return blocked
    form = OTPTokenActionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        token = form.cleaned_data["token"]
        try:
            client = FreeIPAUser.get_client()
            client.otptoken_mod(a_ipatokenuniqueid=token, o_ipatokendisabled=False)
        except exceptions.FreeIPAError as e:
            messages.error(request, f"Cannot enable the token. {e}")
        else:
            messages.success(request, "OTP token enabled.")
    else:
        messages.error(request, "Token must not be empty")
    return redirect(_settings_url("security"))


def security_otp_disable(request: HttpRequest) -> HttpResponse:
    fu = FreeIPAUser.get(request.user.get_username())
    blocked = _block_settings_change_without_country_code(request, user_data=fu._user_data if fu else None)
    if blocked is not None:
        return blocked
    form = OTPTokenActionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        token = form.cleaned_data["token"]
        try:
            client = FreeIPAUser.get_client()
            client.otptoken_mod(a_ipatokenuniqueid=token, o_ipatokendisabled=True)
        except exceptions.FreeIPAError as e:
            messages.error(request, f"Cannot disable the token. {e}")
        else:
            messages.success(request, "OTP token disabled.")
    else:
        messages.error(request, "Token must not be empty")
    return redirect(_settings_url("security"))


def security_otp_delete(request: HttpRequest) -> HttpResponse:
    fu = FreeIPAUser.get(request.user.get_username())
    blocked = _block_settings_change_without_country_code(request, user_data=fu._user_data if fu else None)
    if blocked is not None:
        return blocked
    form = OTPTokenActionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        token = form.cleaned_data["token"]
        try:
            client = FreeIPAUser.get_client()
            client.otptoken_del(a_ipatokenuniqueid=token)
        except exceptions.BadRequest as e:
            if "can't delete last active token" in str(e).lower():
                messages.warning(request, "Sorry, you cannot delete your last active token.")
            else:
                messages.error(request, "Cannot delete the token.")
        except exceptions.FreeIPAError:
            messages.error(request, "Cannot delete the token.")
        else:
            messages.success(request, "OTP token deleted.")
    else:
        messages.error(request, "Token must not be empty")
    return redirect(_settings_url("security"))


def security_otp_rename(request: HttpRequest) -> HttpResponse:
    fu = FreeIPAUser.get(request.user.get_username())
    blocked = _block_settings_change_without_country_code(request, user_data=fu._user_data if fu else None)
    if blocked is not None:
        return blocked
    form = OTPTokenRenameForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        token = form.cleaned_data["token"]
        description = _normalize_str(form.cleaned_data.get("description"))
        try:
            client = FreeIPAUser.get_client()
            client.otptoken_mod(a_ipatokenuniqueid=token, o_description=description)
        except exceptions.BadRequest as e:
            if "no modifications" not in str(e).lower():
                messages.error(request, "Cannot rename the token.")
        except exceptions.FreeIPAError:
            messages.error(request, "Cannot rename the token.")
        else:
            messages.success(request, "OTP token renamed.")
    else:
        if form.errors:
            first_field_errors = next(iter(form.errors.values()))
            first_error = first_field_errors[0] if first_field_errors else "Invalid form"
            messages.error(request, str(first_error))
        else:
            messages.error(request, "Token must not be empty")
    return redirect(_settings_url("security"))


def settings_email_validate(request: HttpRequest) -> HttpResponse:
    username = request.user.get_username()
    token_string = _normalize_str(request.GET.get("token"))
    if not token_string:
        messages.warning(request, "No token provided, please check your email validation link.")
        return redirect(_settings_url("emails"))

    try:
        token = read_signed_token(token_string)
    except signing.SignatureExpired:
        messages.warning(request, "This token is no longer valid, please request a new validation email.")
        return redirect(_settings_url("emails"))
    except signing.BadSignature:
        messages.warning(request, "The token is invalid, please request a new validation email.")
        return redirect(_settings_url("emails"))

    token_user = _normalize_str(token.get("u"))
    attr = _normalize_str(token.get("a"))
    value = _normalize_str(token.get("v")).lower()

    if token_user != username:
        messages.warning(request, "This token does not belong to you.")
        return redirect(_settings_url("emails"))

    if attr not in {"mail", "fasRHBZEmail"}:
        messages.warning(request, "The token is invalid, please request a validation email.")
        return redirect(_settings_url("emails"))

    fu = _get_full_user(username)
    if not fu:
        messages.error(request, "Unable to load your FreeIPA profile.")
        return redirect("home")

    attr_label = "E-mail Address" if attr == "mail" else "Red Hat Bugzilla Email"

    if request.method == "POST":
        direct_updates: dict[str, object] = {}
        setattrs: list[str] = []
        delattrs: list[str] = []

        if attr == "mail":
            direct_updates["o_mail"] = value
        else:
            setattrs.append(f"fasRHBZEmail={value}")

        try:
            _update_user_attrs(username, direct_updates=direct_updates, setattrs=setattrs, delattrs=delattrs)
        except Exception as e:
            logger.exception("Email validation apply failed username=%s attr=%s", username, attr)
            if settings.DEBUG:
                messages.error(request, f"Failed to validate email (debug): {e}")
            else:
                messages.error(request, "Failed to validate email due to an internal error.")
            return redirect(_settings_url("emails"))

        messages.success(request, "Your email address has been validated.")
        return redirect(_settings_url("emails"))

    return render(
        request,
        "core/settings_email_validation.html",
        {"attr": attr, "attr_label": attr_label, "value": value, **settings_context("emails")},
    )
