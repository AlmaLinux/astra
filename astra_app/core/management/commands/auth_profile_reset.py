import datetime
import json
from typing import override
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.account_deletion import invalidate_sessions_for_freeipa_username
from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.client import clear_freeipa_service_client_cache
from core.freeipa.e2e_registry import (
    E2E_FREEIPA_USERNAMES,
    get_e2e_service_client,
    is_e2e_fake_freeipa_enabled,
    reset_e2e_fake_freeipa_state,
)
from core.freeipa.utils import _user_cache_key
from core.models import (
    AccountDeletionRequest,
    FreeIPAPermissionGrant,
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
)
from core.permissions import ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP, ASTRA_VIEW_MEMBERSHIP
from core.rate_limit import clear_subject_rate_limit
from core.tokens import (
    make_password_reset_token,
    make_registration_activation_token,
    make_settings_email_validation_token,
)

REGULAR_PASSWORD = "password"
PASSWORD_RESET_USERNAME = "regular02"
EMAIL_VALIDATION_USERNAME = "regular08"
SETTINGS_USERNAMES = tuple(f"regular{index:02d}" for index in range(3, 13))
OPTIONAL_UNSIGNED_AGREEMENT_CN = "e2e-contributor-agreement"
REGISTER_CONFIRM_USERNAME = "signup-confirm-01"
REGISTER_ACTIVATE_USERNAME = "signup-activate-01"
PROFILE_OWNER_USERNAME = "regular03"
PRIVATE_PROFILE_USERNAME = "regular07"
MEMBERSHIP_REVIEWER_USERNAME = "regular01"
ACCOUNT_SETUP_USERNAME = "regular50"


class Command(BaseCommand):
    help = "Reset the minimal auth-profile E2E scenario state."

    @override
    def handle(self, *args, **options) -> None:
        del args, options

        if not is_e2e_fake_freeipa_enabled():
            raise CommandError(
                "auth_profile_reset requires ASTRA_E2E_MODE=True and ASTRA_E2E_FAKE_FREEIPA_ENABLED=True."
            )

        # The isolated E2E stack can retain pre-index throttle keys from earlier
        # runs, and generic Django cache backends cannot enumerate them by subject.
        cache.clear()

        for username in E2E_FREEIPA_USERNAMES:
            invalidate_sessions_for_freeipa_username(username)
            cache.delete(_user_cache_key(username))
            clear_subject_rate_limit(scope="auth.login", subject=username)

        reset_e2e_fake_freeipa_state()
        clear_freeipa_service_client_cache()

        with transaction.atomic():
            self._ensure_membership_types()
            self._clear_profile_membership_slice()
            payload = self._seed_playwright_state()
        self.stdout.write(json.dumps(payload))

    def _ensure_membership_types(self) -> None:
        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        MembershipType.objects.update_or_create(
            code="mirror",
            defaults={
                "name": "Mirror",
                "group_cn": "almalinux-mirror",
                "category_id": "mirror",
                "sort_order": 1,
                "enabled": True,
            },
        )

    def _clear_profile_membership_slice(self) -> None:
        target_usernames = [PROFILE_OWNER_USERNAME, PRIVATE_PROFILE_USERNAME, MEMBERSHIP_REVIEWER_USERNAME]
        membership_type_codes = ["individual", "mirror"]
        request_ids = list(
            MembershipRequest.objects.filter(
                requested_username__in=target_usernames,
                membership_type_id__in=membership_type_codes,
            ).values_list("pk", flat=True)
        )
        if request_ids:
            MembershipLog.objects.filter(membership_request_id__in=request_ids).delete()
        MembershipLog.objects.filter(
            target_username__in=target_usernames,
            membership_type_id__in=membership_type_codes,
        ).delete()
        MembershipRequest.objects.filter(
            requested_username__in=target_usernames,
            membership_type_id__in=membership_type_codes,
        ).delete()
        Membership.objects.filter(
            target_username__in=target_usernames,
            membership_type_id__in=membership_type_codes,
        ).delete()
        AccountDeletionRequest.objects.filter(username__in=SETTINGS_USERNAMES).delete()
        FreeIPAPermissionGrant.objects.filter(
            permission__in=[ASTRA_VIEW_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP],
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=MEMBERSHIP_REVIEWER_USERNAME,
        ).delete()

    def _seed_playwright_state(self) -> dict[str, object]:
        self._seed_settings_actors()
        self._seed_registration_stage_users()
        agreements = self._seed_agreements()
        self._seed_profile_membership_slice()
        settings_profile_route = self._settings_tab_route("profile")

        password_reset_token = make_password_reset_token(
            {
                "u": PASSWORD_RESET_USERNAME,
                "e": f"{PASSWORD_RESET_USERNAME}@example.test",
                "lpc": "",
            }
        )
        settings_primary_validate_token = make_settings_email_validation_token(
            {
                "u": EMAIL_VALIDATION_USERNAME,
                "a": "mail",
                "v": "updated-regular08@example.test",
            }
        )
        settings_bugzilla_validate_token = make_settings_email_validation_token(
            {
                "u": EMAIL_VALIDATION_USERNAME,
                "a": "fasRHBZEmail",
                "v": "updated-bugzilla-regular08@example.test",
            }
        )
        registration_activate_token = make_registration_activation_token(
            {
                "u": REGISTER_ACTIVATE_USERNAME,
                "e": f"{REGISTER_ACTIVATE_USERNAME}@example.test",
            }
        )

        actors: dict[str, dict[str, str]] = {
            "regular01": self._actor_payload(username="regular01", password=REGULAR_PASSWORD),
            "regular02": self._actor_payload(username="regular02", password=REGULAR_PASSWORD),
            "account_setup": self._actor_payload(username=ACCOUNT_SETUP_USERNAME, password=REGULAR_PASSWORD),
            "admin": self._actor_payload(
                username="admin",
                password="admin-password",
                settings_route=settings_profile_route,
            ),
        }
        for username in SETTINGS_USERNAMES:
            actors[username] = self._actor_payload(
                username=username,
                password=REGULAR_PASSWORD,
                settings_route=settings_profile_route,
            )

        return {
            "scenario": "auth-profile",
            "status": "reset",
            "actors": actors,
            "routes": {
                "login": reverse("login"),
                "password_reset_request": reverse("password-reset"),
                "password_reset_confirm": self._token_route(
                    route_name="password-reset-confirm",
                    token=password_reset_token,
                ),
                "password_expired": reverse("password-expired"),
                "otp_sync": reverse("otp-sync"),
                "register": reverse("register"),
                "register_confirm": f'{reverse("register-confirm")}?username={REGISTER_CONFIRM_USERNAME}',
                "register_activate": self._token_route(
                    route_name="register-activate",
                    token=registration_activate_token,
                ),
                "settings_profile": settings_profile_route,
                "settings_emails": self._settings_tab_route("emails"),
                "settings_keys": self._settings_tab_route("keys"),
                "settings_security": self._settings_tab_route("security"),
                "settings_privacy": self._settings_tab_route("privacy"),
                "settings_agreements": self._settings_tab_route("agreements"),
                "settings_membership": self._settings_tab_route("membership"),
                "settings_email_validate_primary": self._token_route(
                    route_name="settings-email-validate",
                    token=settings_primary_validate_token,
                ),
                "settings_email_validate_bugzilla": self._token_route(
                    route_name="settings-email-validate",
                    token=settings_bugzilla_validate_token,
                ),
            },
            "agreements": agreements,
        }

    def _seed_registration_stage_users(self) -> None:
        client = get_e2e_service_client()
        client.stageuser_add(
            REGISTER_CONFIRM_USERNAME,
            o_givenname="Signup",
            o_sn="Confirm",
            o_mail=f"{REGISTER_CONFIRM_USERNAME}@example.test",
        )
        client.stageuser_add(
            REGISTER_ACTIVATE_USERNAME,
            o_givenname="Signup",
            o_sn="Activate",
            o_mail=f"{REGISTER_ACTIVATE_USERNAME}@example.test",
        )

    def _seed_settings_actors(self) -> None:
        country_attr = str(settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR).strip()
        client = get_e2e_service_client()
        for username in SETTINGS_USERNAMES:
            suffix = username.replace("regular", "")
            updates: dict[str, object] = {
                country_attr: "US",
                "fasPronoun": ["they/them"],
                "fasLocale": "en-US",
                "fasTimezone": "UTC",
                "fasWebsiteUrl": [f"https://{username}.example.test"],
                "fasRssUrl": [f"https://{username}.example.test/feed.xml"],
                "fasIRCNick": [username],
                "fasGitHubUsername": f"{username}-gh",
                "fasGitLabUsername": f"{username}-gl",
                "fasRHBZEmail": f"bugs.{username}@example.test",
                "fasGPGKeyId": [f"ABCDEF01234567{suffix}"],
                "ipasshpubkey": [
                    f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE2e2e{username}Key {username}@example.test"
                ],
            }
            if username == PRIVATE_PROFILE_USERNAME:
                updates["fasIsPrivate"] = "TRUE"
            client.user_mod(username, **updates)

    def _seed_profile_membership_slice(self) -> None:
        now = timezone.now().astimezone(datetime.UTC).replace(microsecond=0)
        client = get_e2e_service_client()

        client.group_add(
            settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            o_description="Fake E2E membership committee group",
        )
        client.user_mod(
            MEMBERSHIP_REVIEWER_USERNAME,
            memberof_group=["packagers", settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
        )

        owner_membership = Membership.objects.create(
            target_username=PROFILE_OWNER_USERNAME,
            membership_type_id="mirror",
            expires_at=now + datetime.timedelta(days=14),
        )
        Membership.objects.filter(pk=owner_membership.pk).update(created_at=now - datetime.timedelta(days=45))

        private_membership = Membership.objects.create(
            target_username=PRIVATE_PROFILE_USERNAME,
            membership_type_id="individual",
            expires_at=now + datetime.timedelta(days=120),
        )
        Membership.objects.filter(pk=private_membership.pk).update(created_at=now - datetime.timedelta(days=90))

        owner_request = MembershipRequest.objects.create(
            requested_username=PROFILE_OWNER_USERNAME,
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[{"Contributions": "Auth profile pending request seed."}],
        )
        MembershipRequest.objects.filter(pk=owner_request.pk).update(requested_at=now - datetime.timedelta(days=2))

        for permission in (ASTRA_VIEW_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=MEMBERSHIP_REVIEWER_USERNAME,
            )

    def _seed_agreements(self) -> dict[str, dict[str, str]]:
        required_coc_cn = str(settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN).strip()
        agreements_by_cn = {agreement.cn: agreement for agreement in FreeIPAFASAgreement.all()}

        required_coc = agreements_by_cn.get(required_coc_cn)
        if required_coc is None:
            required_coc = FreeIPAFASAgreement.create(required_coc_cn, description="Community Code of Conduct")
        if "packagers" not in required_coc.groups:
            required_coc.add_group("packagers")
        for username in SETTINGS_USERNAMES:
            if username not in required_coc.users:
                required_coc.add_user(username)

        optional_unsigned = agreements_by_cn.get(OPTIONAL_UNSIGNED_AGREEMENT_CN)
        if optional_unsigned is None:
            optional_unsigned = FreeIPAFASAgreement.create(
                OPTIONAL_UNSIGNED_AGREEMENT_CN,
                description="Optional contributor agreement for auth/settings E2E coverage.",
            )

        return {
            "required_coc": {
                "cn": required_coc_cn,
                "route": self._agreement_route(required_coc_cn),
            },
            "optional_unsigned": {
                "cn": OPTIONAL_UNSIGNED_AGREEMENT_CN,
                "route": self._agreement_route(OPTIONAL_UNSIGNED_AGREEMENT_CN),
            },
        }

    def _actor_payload(self, *, username: str, password: str, settings_route: str | None = None) -> dict[str, str]:
        payload = {
            "username": username,
            "password": password,
            "profile_route": reverse("user-profile", kwargs={"username": username}),
        }
        if settings_route is not None:
            payload["settings_route"] = settings_route
        return payload

    def _settings_tab_route(self, tab: str) -> str:
        return f'{reverse("settings")}?tab={tab}'

    def _token_route(self, *, route_name: str, token: str) -> str:
        return f'{reverse(route_name)}?token={quote(token)}'

    def _agreement_route(self, agreement_cn: str) -> str:
        return f'{self._settings_tab_route("agreements")}&agreement={quote(agreement_cn)}'