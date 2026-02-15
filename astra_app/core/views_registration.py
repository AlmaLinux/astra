import datetime
import logging
from smtplib import SMTPRecipientsRefused
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from python_freeipa import exceptions

from core.account_invitation_reconcile import (
    load_account_invitation_from_token,
    reconcile_account_invitation_for_username,
)
from core.email_context import system_email_context
from core.templated_email import queue_templated_email
from core.views_utils import _normalize_str

from .backends import FreeIPAUser, _build_freeipa_client
from .forms_registration import PasswordSetForm, RegistrationForm, ResendRegistrationEmailForm
from .tokens import make_signed_token, read_signed_token

logger = logging.getLogger(__name__)
def _stageuser_add(client, username: str, **kwargs):
    # python-freeipa call signatures vary by version. Try a couple.
    try:
        return client.stageuser_add(username, **kwargs)
    except TypeError:
        return client.stageuser_add(a_uid=username, **kwargs)


def _stageuser_show(client, username: str):
    try:
        return client.stageuser_show(username)
    except TypeError:
        return client.stageuser_show(a_uid=username)


def _stageuser_activate(client, username: str):
    try:
        return client.stageuser_activate(username)
    except TypeError:
        return client.stageuser_activate(a_uid=username)


def _send_registration_email(
    request: HttpRequest,
    *,
    username: str,
    email: str,
    first_name: str,
    last_name: str,
    invitation_token: str | None = None,
) -> None:
    payload = {"u": username, "e": email}
    normalized_invitation_token = _normalize_str(invitation_token)
    if normalized_invitation_token:
        payload["i"] = normalized_invitation_token
    token = make_signed_token(payload)
    activate_url = request.build_absolute_uri(reverse("register-activate")) + f"?token={quote(token)}"
    confirm_url = request.build_absolute_uri(reverse("register-confirm")) + f"?username={quote(username)}"

    ttl_seconds = settings.EMAIL_VALIDATION_TOKEN_TTL_SECONDS
    ttl_minutes = max(1, int((ttl_seconds + 59) / 60))
    valid_until = timezone.now() + datetime.timedelta(seconds=ttl_seconds)
    # Use a stable UTC string for emails.
    valid_until_utc = valid_until.astimezone(datetime.UTC).strftime("%H:%M")

    full_name = f"{first_name} {last_name}".strip() or username

    queue_templated_email(
        recipients=[email],
        sender=settings.DEFAULT_FROM_EMAIL,
        template_name=settings.REGISTRATION_EMAIL_TEMPLATE_NAME,
        context={
            **system_email_context(),
            "username": username,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name,
            "activate_url": activate_url,
            "confirm_url": confirm_url,
            "ttl_minutes": ttl_minutes,
            "valid_until_utc": valid_until_utc,
        },
    )


def register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST" and not settings.REGISTRATION_OPEN:
        messages.warning(request, "Registration is closed at the moment.")
        return redirect("login")

    invite_token = _normalize_str(request.GET.get("invite"))
    invitation_token = ""
    if invite_token:
        invitation = load_account_invitation_from_token(invite_token)
        if invitation is not None:
            invitation_token = invite_token

    form = RegistrationForm(request.POST or None, initial={"invitation_token": invitation_token})

    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        first_name = form.cleaned_data["first_name"].strip()
        last_name = form.cleaned_data["last_name"].strip()
        email = form.cleaned_data["email"]
        invitation_token = _normalize_str(form.cleaned_data.get("invitation_token"))

        common_name = f"{first_name} {last_name}".strip() or username

        client = FreeIPAUser.get_client()
        try:
            result = _stageuser_add(
                client,
                username,
                o_givenname=first_name,
                o_sn=last_name,
                o_cn=common_name,
                o_mail=email,
                o_loginshell="/bin/bash",
                fasstatusnote="active",
            )
            _ = result
        except exceptions.DuplicateEntry:
            form.add_error(None, f"The username '{username}' or the email address '{email}' are already taken.")
            return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})
        except exceptions.ValidationError as e:
            # FreeIPA often encodes field name inside the message; keep it generic.
            logger.info("Registration validation error username=%s error=%s", username, e)
            form.add_error(None, str(e))
            return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})
        except exceptions.FreeIPAError as e:
            logger.warning("Registration FreeIPA error username=%s error=%s", username, e)
            if settings.DEBUG:
                form.add_error(None, f"An error occurred while creating the account (debug): {e}")
            else:
                form.add_error(None, "An error occurred while creating the account, please try again.")
            return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})
        except Exception as e:
            logger.exception("Registration unexpected error username=%s", username)
            if settings.DEBUG:
                form.add_error(None, f"Unable to create account (debug): {e}")
            else:
                form.add_error(None, "Unable to create account due to an internal error.")
            return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})

        try:
            _send_registration_email(
                request,
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name,
                invitation_token=invitation_token,
            )
        except (ConnectionRefusedError, SMTPRecipientsRefused) as e:
            logger.error("Registration email send failed username=%s email=%s error=%s", username, email, e)
            messages.error(request, "We could not send you the address validation email, please retry later")
        except Exception as e:
            logger.exception("Registration email send unexpected failure username=%s email=%s", username, email)
            if settings.DEBUG:
                messages.error(request, f"We could not send the validation email (debug): {e}")
            else:
                messages.error(request, "We could not send you the address validation email, please retry later")

        return redirect(f"{reverse('register-confirm')}?username={username}")

    return render(request, "core/register.html", {"form": form, "registration_open": settings.REGISTRATION_OPEN})


def confirm(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    username = _normalize_str(request.GET.get("username"))
    if not username:
        return HttpResponse("No username provided", status=400)

    client = FreeIPAUser.get_client()
    try:
        stage = _stageuser_show(client, username)
        stage_data = stage.get("result") if isinstance(stage, dict) else None
    except exceptions.NotFound:
        messages.warning(request, "The registration seems to have failed, please try again.")
        return redirect("register")
    except Exception:
        logger.exception("Registration confirm failed username=%s", username)
        messages.error(request, "Something went wrong")
        return redirect("register")

    email = None
    first_name = None
    last_name = None
    if isinstance(stage_data, dict):
        email = (stage_data.get("mail") or [None])[0] if isinstance(stage_data.get("mail"), list) else stage_data.get("mail")
        first_name = (stage_data.get("givenname") or [None])[0] if isinstance(stage_data.get("givenname"), list) else stage_data.get("givenname")
        last_name = (stage_data.get("sn") or [None])[0] if isinstance(stage_data.get("sn"), list) else stage_data.get("sn")

    form = ResendRegistrationEmailForm(request.POST or None, initial={"username": username})
    if request.method == "POST" and form.is_valid():
        try:
            _send_registration_email(
                request,
                username=username,
                email=(email or ""),
                first_name=(first_name or ""),
                last_name=(last_name or ""),
            )
        except Exception:
            logger.exception("Resend registration email failed username=%s", username)
            messages.error(request, "We could not send you the address validation email, please retry later")
        else:
            messages.success(
                request,
                "The address validation email has be sent again. Make sure it did not land in your spam folder",
            )
        return redirect(request.get_full_path())

    return render(
        request,
        "core/register_confirm.html",
        {"username": username, "email": email, "form": form},
    )


def activate(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    token_string = _normalize_str(request.GET.get("token"))
    if not token_string:
        messages.warning(request, "No token provided, please check your email validation link.")
        return redirect("register")

    try:
        token = read_signed_token(token_string)
    except signing.SignatureExpired:
        messages.warning(request, "This token is no longer valid, please register again.")
        return redirect("register")
    except signing.BadSignature:
        messages.warning(request, "The token is invalid, please register again.")
        return redirect("register")

    username = _normalize_str(token.get("u"))
    token_email = _normalize_str(token.get("e")).lower()
    invitation_token = _normalize_str(token.get("i"))

    client = FreeIPAUser.get_client()
    try:
        stage = _stageuser_show(client, username)
        stage_data = stage.get("result") if isinstance(stage, dict) else None
    except exceptions.NotFound:
        messages.warning(request, "This user cannot be found, please register again.")
        return redirect("register")

    user_email = None
    if isinstance(stage_data, dict):
        raw = stage_data.get("mail")
        if isinstance(raw, list):
            user_email = (raw[0] if raw else None)
        else:
            user_email = raw
    if _normalize_str(user_email).lower() != token_email:
        logger.error(
            "Registration token email mismatch username=%s token_email=%s user_email=%s",
            username,
            token_email,
            user_email,
        )
        messages.warning(request, "The username and the email address don't match the token you used, please register again.")
        return redirect("register")

    form = PasswordSetForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        password = form.cleaned_data["password"]
        try:
            _stageuser_activate(client, username)

            # Set password as a privileged client.
            try:
                client.user_mod(username, o_userpassword=password)
            except TypeError:
                client.user_mod(a_uid=username, o_userpassword=password)

            # Try to un-expire it by changing it "as the user".
            try:
                c = _build_freeipa_client()
                # python-freeipa signature: change_password(username, new_password, old_password, otp=None)
                c.change_password(username, password, password, otp=None)
            except exceptions.PWChangePolicyError as e:
                logger.info("Activation succeeded but password policy rejected username=%s error=%s", username, e)
                messages.warning(
                    request,
                    "Your account has been created, but the password you chose does not comply with policy and has been set as expired. Please log in and change it.",
                )
                return redirect("login")
            except Exception as e:
                logger.warning("Activation password unexpire step failed username=%s error=%s", username, e)
                messages.warning(
                    request,
                    "Your account has been created, but an error occurred while setting your password. You may need to change it after logging in.",
                )
                return redirect("login")

        except exceptions.FreeIPAError as e:
            logger.error("Activation failed username=%s error=%s", username, e)
            form.add_error(None, "Something went wrong while creating your account, please try again later.")
        except Exception as e:
            logger.exception("Activation failed (unexpected) username=%s", username)
            if settings.DEBUG:
                form.add_error(None, f"Something went wrong (debug): {e}")
            else:
                form.add_error(None, "Something went wrong while creating your account, please try again later.")
        else:
            if invitation_token:
                invitation = load_account_invitation_from_token(invitation_token)
                if invitation is not None:
                    reconcile_account_invitation_for_username(
                        invitation=invitation,
                        username=username,
                        now=timezone.now(),
                    )

            messages.success(request, "Congratulations, your account has been created! Go ahead and sign in to proceed.")
            return redirect("login")

    return render(request, "core/register_activate.html", {"form": form, "username": username})
