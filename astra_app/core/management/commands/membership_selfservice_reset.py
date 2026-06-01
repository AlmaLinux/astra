import datetime
import json
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.e2e_membership_request_reset import seed_membership_request_workflow
from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client, is_e2e_fake_freeipa_enabled
from core.models import (
    Membership,
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Note,
    Organization,
)


class Command(BaseCommand):
    help = "Reset the self-service membership E2E scenario state."

    actor_usernames = ["regular01", "regular32", "regular33", "regular34", "regular35", "regular36"]
    cleanup_usernames = [*actor_usernames, "regular02", "regular04", "regular05", "regular06"]
    representative_org_names = [
        "Regular01 Sponsor Form Org",
        "Regular01 No Types Org",
    ]
    active_membership_alias = "regular03_active_mirror_membership"
    ordered_history_aliases = [
        "regular03_history_expiry_changed",
        "regular03_history_approved",
        "regular03_history_requested",
    ]

    @override
    def handle(self, *args, **options) -> None:
        del args, options

        if not is_e2e_fake_freeipa_enabled():
            raise CommandError(
                "membership_selfservice_reset requires ASTRA_E2E_MODE=True and ASTRA_E2E_FAKE_FREEIPA_ENABLED=True."
            )

        with transaction.atomic():
            self._ensure_membership_types()
            self._clear_existing_slice()
            self._ensure_signed_coc()
            payload = self._seed_slice()

        self.stdout.write(json.dumps(payload))

    def _ensure_membership_types(self) -> None:
        MembershipTypeCategory.objects.update_or_create(
            name="individual",
            defaults={"is_individual": True, "is_organization": False, "sort_order": 0},
        )
        MembershipTypeCategory.objects.update_or_create(
            name="mirror",
            defaults={"is_individual": True, "is_organization": True, "sort_order": 1},
        )
        MembershipTypeCategory.objects.update_or_create(
            name="sponsorship",
            defaults={"is_individual": False, "is_organization": True, "sort_order": 2},
        )
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
        self._ensure_membership_type_preserving_order(
            code="gold",
            defaults={
                "name": "Gold Sponsor Member",
                "group_cn": "almalinux-gold",
                "category_id": "sponsorship",
                "sort_order": 3,
                "enabled": True,
            },
        )
        self._ensure_membership_type_preserving_order(
            code="platinum",
            defaults={
                "name": "Platinum Sponsor",
                "group_cn": "platinum-sponsors",
                "category_id": "sponsorship",
                "sort_order": 4,
                "enabled": True,
            },
        )
        self._ensure_membership_type_preserving_order(
            code="ruby",
            defaults={
                "name": "Ruby Sponsor Member",
                "group_cn": "almalinux-ruby",
                "category_id": "sponsorship",
                "sort_order": 5,
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
        self._ensure_membership_type_preserving_order(
            code="silver",
            defaults={
                "name": "Silver Sponsor Member",
                "group_cn": "almalinux-silver",
                "category_id": "sponsorship",
                "sort_order": 2,
                "enabled": True,
            },
        )

    def _ensure_membership_type_preserving_order(self, *, code: str, defaults: dict[str, object]) -> None:
        membership_type, created = MembershipType.objects.get_or_create(code=code, defaults=defaults)
        if created:
            return
        updates: dict[str, object] = {}
        if membership_type.name != defaults["name"]:
            updates["name"] = defaults["name"]
        if membership_type.group_cn != defaults["group_cn"]:
            updates["group_cn"] = defaults["group_cn"]
        if membership_type.category_id != defaults["category_id"]:
            updates["category_id"] = defaults["category_id"]
        if membership_type.enabled != defaults["enabled"]:
            updates["enabled"] = defaults["enabled"]
        if updates:
            MembershipType.objects.filter(pk=membership_type.pk).update(**updates)

    def _clear_existing_slice(self) -> None:
        membership_type_codes = ["individual", "mirror"]

        request_ids = self._self_service_request_ids(membership_type_codes=membership_type_codes)
        representative_organizations = list(
            Organization.objects.filter(name__in=self.representative_org_names).values_list("pk", flat=True)
        )

        if request_ids:
            Note.objects.filter(membership_request_id__in=request_ids).delete()
            MembershipLog.objects.filter(
                Q(membership_request_id__in=request_ids)
                | Q(
                    target_username="regular33",
                    membership_type_id="mirror",
                )
            ).delete()
        else:
            MembershipLog.objects.filter(
                target_username="regular33",
                membership_type_id="mirror",
            ).delete()

        MembershipRequest.objects.filter(pk__in=request_ids).delete()
        Membership.objects.filter(
            target_username="regular33",
            membership_type_id="mirror",
        ).delete()
        if representative_organizations:
            MembershipRequest.objects.filter(requested_organization_id__in=representative_organizations).delete()
            Membership.objects.filter(target_organization_id__in=representative_organizations).delete()
            Organization.objects.filter(pk__in=representative_organizations).delete()

    def _self_service_request_ids(self, *, membership_type_codes: list[str]) -> list[int]:
        owned_markers = {
            "Seeded duplicate request for Wave 1.",
            "https://mirror.regular03.example.test",
            "https://mirror.regular33.example.test",
            "https://mirror.regular04.example.test",
            "https://mirror.regular34.example.test",
            "Pending request for rescind scenario.",
            "Initial mirror details pending clarification.",
            "https://mirror.regular06-updated.example.test",
            "https://mirror.regular36-updated.example.test",
        }
        request_ids: list[int] = []
        for membership_request in MembershipRequest.objects.filter(
            requested_username__in=self.cleanup_usernames,
            membership_type_id__in=membership_type_codes,
        ).only("pk", "responses"):
            response_values = {
                str(value)
                for response in membership_request.responses
                for value in response.values()
            }
            if response_values & owned_markers:
                request_ids.append(membership_request.pk)
        return request_ids

    def _ensure_signed_coc(self) -> None:
        agreement_cn = settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN
        agreement = FreeIPAFASAgreement.get(agreement_cn)
        if agreement is None:
            agreement = FreeIPAFASAgreement.create(agreement_cn, description="CoC")

        for username in self.actor_usernames:
            if username not in agreement.users:
                agreement.add_user(username)

        client = get_e2e_service_client()
        country_attr = str(settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR).strip() or "c"
        for username in self.actor_usernames:
            client.user_mod(username, **{country_attr: "US"})

    def _seed_slice(self) -> dict[str, object]:
        now = timezone.now().astimezone(datetime.UTC).replace(microsecond=0)
        membership_type_by_code = {
            membership_type.code: membership_type
            for membership_type in MembershipType.objects.filter(code__in=["individual", "mirror"])
        }
        mirror_type = membership_type_by_code["mirror"]
        self._ensure_group_membership(username="regular33", group_cn=mirror_type.group_cn)

        approved_request_requested_at = now - datetime.timedelta(days=46)
        approved_request_decided_at = now - datetime.timedelta(days=45)
        renewal_window_days = max(int(settings.MEMBERSHIP_EXPIRING_SOON_DAYS) - 1, 1)
        membership_expires_at = now + datetime.timedelta(days=renewal_window_days)
        duplicate_requested_at = now - datetime.timedelta(days=4)
        rescind_requested_at = now - datetime.timedelta(days=3)
        on_hold_requested_at = now - datetime.timedelta(days=2)
        on_hold_at = now - datetime.timedelta(days=1)
        followup_requested_at = now - datetime.timedelta(days=6)
        followup_on_hold_at = now - datetime.timedelta(days=5)
        followup_resubmitted_at = now - datetime.timedelta(days=4, hours=12)

        approved_request = seed_membership_request_workflow(
            requested_username="regular33",
            requested_organization=None,
            membership_type=mirror_type,
            initial_responses=[
                {"Domain": "https://mirror.regular33.example.test"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/303"},
                {"Additional information": "Primary EU mirror"},
            ],
            requested_at=approved_request_requested_at,
            review_notes=[
                (
                    "committee-alpha",
                    "vote_approve",
                    "Mirror details look complete.",
                    approved_request_requested_at + datetime.timedelta(hours=2),
                ),
                (
                    "committee-beta",
                    "vote_approve",
                    "Agreement details verified.",
                    approved_request_requested_at + datetime.timedelta(hours=4),
                ),
            ],
            final_state=MembershipRequest.Status.approved,
            final_actor_username="committee-chair",
            final_action_at=approved_request_decided_at,
            approved_expires_at=membership_expires_at,
            application_url=reverse("membership-request-detail", kwargs={"pk": 0}),
        )
        membership = Membership.objects.get(target_username="regular33", membership_type_id="mirror")
        approved_request.refresh_from_db()
        membership.refresh_from_db()

        duplicate_request = seed_membership_request_workflow(
            requested_username="regular32",
            requested_organization=None,
            membership_type=membership_type_by_code["individual"],
            initial_responses=[{"Contributions": "Seeded duplicate request for Wave 1."}],
            requested_at=duplicate_requested_at,
            review_notes=[
                (
                    "committee-alpha",
                    "vote_approve",
                    "Duplicate request still needs committee resolution.",
                    duplicate_requested_at + datetime.timedelta(hours=1),
                ),
                (
                    "committee-beta",
                    "vote_disapprove",
                    "Leave this pending for duplicate-state coverage.",
                    duplicate_requested_at + datetime.timedelta(hours=2),
                ),
            ],
            final_state=MembershipRequest.Status.pending,
            final_actor_username="committee-chair",
            application_url=reverse("membership-request-detail", kwargs={"pk": 0}),
        )

        on_hold_request = seed_membership_request_workflow(
            requested_username="regular34",
            requested_organization=None,
            membership_type=mirror_type,
            initial_responses=[
                {"Domain": "https://mirror.regular34.example.test"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/404"},
                {"Additional information": "Needs refreshed mirror details."},
            ],
            requested_at=on_hold_requested_at,
            review_notes=[
                (
                    "committee-alpha",
                    "vote_disapprove",
                    "Mirror details need another round.",
                    on_hold_requested_at + datetime.timedelta(hours=1),
                ),
                (
                    "committee-beta",
                    "vote_disapprove",
                    "Ask for updated domain information.",
                    on_hold_requested_at + datetime.timedelta(hours=2),
                ),
            ],
            final_state=MembershipRequest.Status.on_hold,
            final_actor_username="committee-chair",
            final_action_at=on_hold_at,
            rfi_message="Please confirm the refreshed mirror endpoint and pull request.",
            application_url=reverse("membership-request-detail", kwargs={"pk": 0}),
        )

        rescind_request = seed_membership_request_workflow(
            requested_username="regular35",
            requested_organization=None,
            membership_type=membership_type_by_code["individual"],
            initial_responses=[{"Contributions": "Pending request for rescind scenario."}],
            requested_at=rescind_requested_at,
            review_notes=[
                (
                    "committee-alpha",
                    "vote_approve",
                    "Requester can still rescind this pending request.",
                    rescind_requested_at + datetime.timedelta(hours=1),
                ),
                (
                    "committee-beta",
                    "vote_approve",
                    "Keep pending for rescind coverage.",
                    rescind_requested_at + datetime.timedelta(hours=2),
                ),
            ],
            final_state=MembershipRequest.Status.pending,
            final_actor_username="committee-chair",
            application_url=reverse("membership-request-detail", kwargs={"pk": 0}),
        )

        rfi_followup_request = seed_membership_request_workflow(
            requested_username="regular36",
            requested_organization=None,
            membership_type=mirror_type,
            initial_responses=[
                {"Domain": "https://mirror.regular36.example.test"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/606"},
                {"Additional information": "Initial mirror details pending clarification."},
            ],
            requested_at=followup_requested_at,
            review_notes=[
                (
                    "committee-alpha",
                    "vote_disapprove",
                    "Need clearer mirror hosting details.",
                    followup_requested_at + datetime.timedelta(hours=1),
                ),
                (
                    "committee-beta",
                    "vote_disapprove",
                    "Follow-up should return to committee review after clarification.",
                    followup_requested_at + datetime.timedelta(hours=2),
                ),
            ],
            final_state="rfi_followup_pending",
            final_actor_username="committee-chair",
            final_action_at=followup_on_hold_at,
            rfi_message="Please add the updated mirror endpoint and refreshed PR link.",
            application_url=reverse("membership-request-detail", kwargs={"pk": 0}),
            resubmitted_responses=[
                {"Domain": "https://mirror.regular36-updated.example.test"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/606"},
                {"Additional information": "Updated after committee RFI."},
            ],
            resubmitted_at=followup_resubmitted_at,
        )

        history_rows = self._seed_regular03_history(
            membership_type=mirror_type,
            membership_request=approved_request,
            membership_expires_at=membership_expires_at,
            now=now,
        )
        representative_form_org = Organization.objects.create(
            name="Regular01 Sponsor Form Org",
            representative="regular01",
            website="https://regular01-sponsor-form.example.test",
            business_contact_email="regular01-sponsor-form@example.test",
            business_contact_name="Regular01 Sponsor Form Contact",
            country_code="US",
        )
        representative_no_types_org = Organization.objects.create(
            name="Regular01 No Types Org",
            representative="regular32",
            website="https://regular01-no-types.example.test",
            business_contact_email="regular01-no-types@example.test",
            business_contact_name="Regular01 No Types Contact",
            country_code="US",
        )
        Membership.objects.create(
            target_organization=representative_no_types_org,
            membership_type_id="silver",
            expires_at=now + datetime.timedelta(days=180),
        )
        Membership.objects.create(
            target_organization=representative_no_types_org,
            membership_type_id="mirror",
            expires_at=now + datetime.timedelta(days=180),
        )
        MembershipRequest.objects.create(
            requested_username="",
            requested_organization=representative_no_types_org,
            membership_type_id="gold",
            status=MembershipRequest.Status.pending,
            responses=[{"Sponsorship details": "Existing sponsorship tier-change request."}],
        )
        organization_target_request = seed_membership_request_workflow(
            requested_username="",
            requested_organization=representative_form_org,
            membership_type=mirror_type,
            initial_responses=[
                {"Domain": "https://mirror.regular01-org.example.test"},
                {"Pull request": "https://github.com/AlmaLinux/mirrors/pull/601"},
                {"Additional information": "Organization-sponsored mirror application for cross-link coverage."},
            ],
            requested_at=now - datetime.timedelta(hours=12),
            review_notes=[
                (
                    "committee-alpha",
                    "vote_approve",
                    "Representative-org request is ready for queue assertions.",
                    now - datetime.timedelta(hours=11),
                ),
                (
                    "committee-beta",
                    "vote_disapprove",
                    "Keep pending for organization-target coverage.",
                    now - datetime.timedelta(hours=10),
                ),
            ],
            final_state=MembershipRequest.Status.pending,
            final_actor_username="committee-chair",
            application_url=reverse("organization-membership-request", args=[representative_form_org.pk]),
        )
        settings_membership_route = f"{reverse('settings')}?tab=membership"
        profile_routes = {
            username: reverse("user-profile", kwargs={"username": username})
            for username in self.actor_usernames
        }
        organizations = {
            "representative_form_org": {
                "organization_id": representative_form_org.pk,
                "name": representative_form_org.name,
                "representative_username": representative_form_org.representative,
                "detail_route": reverse("organization-detail", args=[representative_form_org.pk]),
                "request_route": reverse("organization-membership-request", args=[representative_form_org.pk]),
            },
            "representative_no_types_org": {
                "organization_id": representative_no_types_org.pk,
                "name": representative_no_types_org.name,
                "representative_username": representative_no_types_org.representative,
                "detail_route": reverse("organization-detail", args=[representative_no_types_org.pk]),
                "request_route": reverse("organization-membership-request", args=[representative_no_types_org.pk]),
            },
        }

        return {
            "scenario": "membership-self-service",
            "status": "reset",
            "routes": {
                "create": reverse("membership-request"),
                "profiles": profile_routes,
                "settings_membership": {
                    "regular33": settings_membership_route,
                },
            },
            "actors": {
                "regular01": {
                    "username": "regular01",
                    "password": "password",
                    "profile_route": profile_routes["regular01"],
                    "organization_aliases": {"representative_form_org": representative_form_org.pk},
                    "request_aliases": {"organization_target_pending": organization_target_request.pk},
                    "membership_type_codes": ["individual"],
                },
                "regular32": {
                    "username": "regular32",
                    "password": "password",
                    "profile_route": profile_routes["regular32"],
                    "organization_aliases": {"representative_no_types_org": representative_no_types_org.pk},
                    "request_aliases": {"duplicate_pending": duplicate_request.pk},
                    "membership_type_codes": ["individual"],
                },
                "regular33": {
                    "username": "regular33",
                    "password": "password",
                    "profile_route": profile_routes["regular33"],
                    "settings_membership_route": settings_membership_route,
                    "organization_aliases": {},
                    "request_aliases": {"renewal_source": approved_request.pk},
                    "membership_type_codes": ["mirror"],
                },
                "regular34": {
                    "username": "regular34",
                    "password": "password",
                    "profile_route": profile_routes["regular34"],
                    "organization_aliases": {},
                    "request_aliases": {"resubmit_on_hold": on_hold_request.pk},
                    "membership_type_codes": ["mirror"],
                },
                "regular35": {
                    "username": "regular35",
                    "password": "password",
                    "profile_route": profile_routes["regular35"],
                    "organization_aliases": {},
                    "request_aliases": {"rescind_pending": rescind_request.pk},
                    "membership_type_codes": ["individual"],
                },
                "regular36": {
                    "username": "regular36",
                    "password": "password",
                    "profile_route": profile_routes["regular36"],
                    "organization_aliases": {},
                    "request_aliases": {"rfi_followup_review": rfi_followup_request.pk},
                    "membership_type_codes": ["mirror"],
                },
            },
            "organizations": organizations,
            "requests": {
                "duplicate_pending": self._request_payload(alias="duplicate_pending", membership_request=duplicate_request),
                "organization_target_pending": self._request_payload(
                    alias="organization_target_pending",
                    membership_request=organization_target_request,
                    actor_username=representative_form_org.representative,
                ),
                "renewal_source": self._request_payload(alias="renewal_source", membership_request=approved_request),
                "rfi_followup_review": self._request_payload(
                    alias="rfi_followup_review",
                    membership_request=rfi_followup_request,
                ),
                "resubmit_on_hold": self._request_payload(alias="resubmit_on_hold", membership_request=on_hold_request),
                "rescind_pending": self._request_payload(alias="rescind_pending", membership_request=rescind_request),
            },
            "settings": {
                "membership": {
                    "actor_username": "regular33",
                    "route": settings_membership_route,
                    "active_membership_alias": self.active_membership_alias,
                    "active_membership": {
                        "membership_type_code": membership.membership_type.code,
                        "membership_type_name": membership.membership_type.name,
                        "created_at": self._isoformat(membership.created_at),
                        "expires_at": self._isoformat(membership.expires_at),
                        "terminate_membership_type_code": membership.membership_type.code,
                        "terminate_route": reverse(
                            "settings-membership-terminate",
                            kwargs={"membership_type_code": membership.membership_type.code},
                        ),
                    },
                    "ordered_history_aliases": self.ordered_history_aliases,
                    "history_rows": history_rows,
                },
            },
        }

    def _ensure_group_membership(self, *, username: str, group_cn: str) -> None:
        client = get_e2e_service_client()
        if client.group_find(o_cn=group_cn).get("count", 0) == 0:
            client.group_add(group_cn, o_description=f"Fake E2E group for {group_cn}")
        client.group_add_member(group_cn, o_user=[username])

    def _seed_regular03_history(
        self,
        *,
        membership_type: MembershipType,
        membership_request: MembershipRequest,
        membership_expires_at: datetime.datetime,
        now: datetime.datetime,
    ) -> dict[str, dict[str, str]]:
        history_specs = [
            {
                "alias": "regular03_history_requested",
                "action": MembershipLog.Action.requested,
                "created_at": now - datetime.timedelta(days=46, hours=2),
                "expires_at": None,
            },
            {
                "alias": "regular03_history_approved",
                "action": MembershipLog.Action.approved,
                "created_at": now - datetime.timedelta(days=45, hours=1),
                "expires_at": membership_expires_at,
            },
            {
                "alias": "regular03_history_expiry_changed",
                "action": MembershipLog.Action.expiry_changed,
                "created_at": now - datetime.timedelta(days=5),
                "expires_at": membership_expires_at,
            },
        ]
        payload_rows: dict[str, dict[str, str]] = {}

        for spec in history_specs:
            membership_log = MembershipLog.objects.filter(
                membership_request=membership_request,
                action=spec["action"],
            ).order_by("-pk").first()
            if membership_log is None:
                membership_log = MembershipLog.objects.create(
                    actor_username="reviewer",
                    target_username="regular33",
                    membership_type=membership_type,
                    membership_request=membership_request,
                    requested_group_cn=membership_type.group_cn,
                    action=spec["action"],
                    expires_at=spec["expires_at"],
                )
            MembershipLog.objects.filter(pk=membership_log.pk).update(created_at=spec["created_at"])
            payload_rows[spec["alias"]] = {
                "membership_type_code": membership_type.code,
                "membership_type_name": membership_type.name,
                "action": spec["action"],
                "action_label": str(MembershipLog.Action(spec["action"]).label),
                "created_at": self._isoformat(spec["created_at"]),
            }

        return payload_rows

    def _request_payload(
        self,
        *,
        alias: str,
        membership_request: MembershipRequest,
        actor_username: str | None = None,
    ) -> dict[str, object]:
        target_kind = "organization" if membership_request.requested_organization_id is not None else "user"
        return {
            "alias": alias,
            "request_id": membership_request.pk,
            "detail_route": reverse("membership-request-detail", kwargs={"pk": membership_request.pk}),
            "actor_username": actor_username or membership_request.requested_username,
            "browser_state": membership_request.status,
            "target_kind": target_kind,
            "target_organization_id": membership_request.requested_organization_id,
        }

    def _isoformat(self, value: datetime.datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(datetime.UTC).isoformat().replace("+00:00", "Z")