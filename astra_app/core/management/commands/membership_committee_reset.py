import datetime
import json
from typing import override

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.country_codes import country_attr_name
from core.e2e_membership_request_reset import seed_membership_request_workflow
from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client, is_e2e_fake_freeipa_enabled
from core.models import FreeIPAPermissionGrant, Membership, MembershipLog, MembershipRequest, MembershipType, Note
from core.permissions import ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL

COMMITTEE_REVIEWER_USERNAME = "regular01"
COMMITTEE_REQUEST_USERNAMES = tuple(f"regular{index:02d}" for index in range(2, 19))
COMMITTEE_SUPPORT_REVIEWERS = ("committee-alpha", "committee-beta")

COMMITTEE_REQUEST_MATRIX: tuple[dict[str, object], ...] = (
    {
        "alias": "pending_shell_observer",
        "username": "regular02",
        "membership_type_code": "mirror",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Domain": "https://mirror-shell.example.test"}],
        "sort_offset_minutes": 25,
    },
    {
        "alias": "on_hold_shell_observer",
        "username": "regular03",
        "membership_type_code": "mirror",
        "status": MembershipRequest.Status.on_hold,
        "responses": [{"Domain": "https://mirror-on-hold-shell.example.test"}],
        "sort_offset_minutes": 30,
    },
    {
        "alias": "pending_filter_renewal",
        "username": "regular04",
        "membership_type_code": "mirror",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Domain": "https://mirror-renewal.example.test"}],
        "is_renewal": True,
    },
    {
        "alias": "pending_filter_nonrenewal",
        "username": "regular05",
        "membership_type_code": "mirror",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Domain": "https://mirror-nonrenewal.example.test"}],
    },
    {
        "alias": "pending_row_action",
        "username": "regular06",
        "membership_type_code": "mirror",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Domain": "https://mirror-row-action.example.test"}],
    },
    {
        "alias": "pending_bulk_accept_primary",
        "username": "regular07",
        "membership_type_code": "individual",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Contributions": "Bulk accept primary pending request."}],
    },
    {
        "alias": "pending_bulk_accept_secondary",
        "username": "regular08",
        "membership_type_code": "individual",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Contributions": "Bulk accept secondary pending request."}],
    },
    {
        "alias": "pending_bulk_select_all_extra",
        "username": "regular09",
        "membership_type_code": "individual",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Contributions": "Bulk accept extra pending request for select-all coverage."}],
    },
    {
        "alias": "pending_row_approve",
        "username": "regular11",
        "membership_type_code": "mirror",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Domain": "https://mirror-row-approve.example.test"}],
        "sort_offset_minutes": 24,
    },
    {
        "alias": "pending_row_reject",
        "username": "regular12",
        "membership_type_code": "mirror",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Domain": "https://mirror-row-reject.example.test"}],
        "sort_offset_minutes": 23,
    },
    {
        "alias": "pending_row_rfi",
        "username": "regular13",
        "membership_type_code": "mirror",
        "status": MembershipRequest.Status.pending,
        "responses": [{"Domain": "https://mirror-row-rfi.example.test"}],
        "sort_offset_minutes": 22,
    },
    {
        "alias": "on_hold_bulk_approve_primary",
        "username": "regular10",
        "membership_type_code": "individual",
        "status": MembershipRequest.Status.on_hold,
        "responses": [{"Contributions": "Bulk approve primary on-hold request."}],
        "sort_offset_minutes": 29,
    },
    {
        "alias": "on_hold_bulk_approve_secondary",
        "username": "regular02",
        "membership_type_code": "individual",
        "status": MembershipRequest.Status.on_hold,
        "responses": [{"Contributions": "Bulk approve secondary on-hold request."}],
        "sort_offset_minutes": 28,
    },
    {
        "alias": "on_hold_bulk_select_all_extra",
        "username": "regular04",
        "membership_type_code": "individual",
        "status": MembershipRequest.Status.on_hold,
        "responses": [{"Contributions": "Bulk approve extra on-hold request for select-all coverage."}],
        "sort_offset_minutes": 27,
    },
    {
        "alias": "on_hold_row_approve",
        "username": "regular14",
        "membership_type_code": "mirror",
        "status": MembershipRequest.Status.on_hold,
        "responses": [{"Domain": "https://mirror-row-approve-on-hold.example.test"}],
        "sort_offset_minutes": 26,
    },
)

ON_HOLD_PAGINATION_FILLERS: tuple[dict[str, str], ...] = (
    {"alias": "on_hold_page_two_filler_01", "username": "regular03", "membership_type_code": "individual"},
    {"alias": "on_hold_page_two_filler_02", "username": "regular05", "membership_type_code": "individual"},
    {"alias": "on_hold_page_two_filler_03", "username": "regular06", "membership_type_code": "individual"},
    {"alias": "on_hold_page_two_filler_04", "username": "regular07", "membership_type_code": "mirror"},
    {"alias": "on_hold_page_two_filler_05", "username": "regular08", "membership_type_code": "mirror"},
    {"alias": "on_hold_page_two_filler_06", "username": "regular09", "membership_type_code": "mirror"},
    {"alias": "on_hold_page_two_filler_07", "username": "regular10", "membership_type_code": "mirror"},
)

SCENARIO_ALIAS_MATRIX: dict[str, dict[str, object]] = {
    "committee-queue-shell": {
        "aliases": ["pending_shell_observer", "on_hold_shell_observer"],
        "destructive": False,
    },
    "committee-pending-filter-renewals": {
        "aliases": ["pending_filter_renewal", "pending_filter_nonrenewal"],
        "destructive": False,
    },
    "committee-pending-row-actions": {
        "aliases": ["pending_row_action"],
        "destructive": True,
    },
    "committee-pending-bulk-accept": {
        "aliases": [
            "pending_bulk_accept_primary",
            "pending_bulk_accept_secondary",
            "pending_bulk_select_all_extra",
        ],
        "destructive": True,
    },
    "committee-on-hold-bulk-approve": {
        "aliases": [
            "on_hold_bulk_approve_primary",
            "on_hold_bulk_approve_secondary",
            "on_hold_bulk_select_all_extra",
        ],
        "destructive": True,
    },
    "committee-row-actions": {
        "aliases": [
            "pending_row_approve",
            "pending_row_reject",
            "pending_row_rfi",
            "pending_row_action",
            "on_hold_row_approve",
        ],
        "destructive": True,
    },
    "committee-request-detail": {
        "aliases": ["pending_row_action"],
        "destructive": True,
    },
}


class Command(BaseCommand):
    help = "Reset the Wave 2 membership committee E2E scenario state."

    @override
    def handle(self, *args, **options) -> None:
        del args, options

        if not is_e2e_fake_freeipa_enabled():
            raise CommandError(
                "membership_committee_reset requires ASTRA_E2E_MODE=True and ASTRA_E2E_FAKE_FREEIPA_ENABLED=True."
            )

        with transaction.atomic():
            self._ensure_membership_types()
            self._ensure_permission_grants()
            self._clear_existing_slice()
            self._ensure_committee_actor()
            payload = self._seed_slice()

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

    def _ensure_permission_grants(self) -> None:
        for permission in (ASTRA_ADD_MEMBERSHIP, ASTRA_ADD_SEND_MAIL):
            FreeIPAPermissionGrant.objects.get_or_create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
            )

    def _clear_existing_slice(self) -> None:
        membership_type_codes = ["individual", "mirror"]
        usernames = [*COMMITTEE_REQUEST_USERNAMES, COMMITTEE_REVIEWER_USERNAME]

        request_queryset = MembershipRequest.objects.filter(
            requested_username__in=COMMITTEE_REQUEST_USERNAMES,
            membership_type_id__in=membership_type_codes,
        )
        request_ids = list(request_queryset.values_list("pk", flat=True))

        if request_ids:
            Note.objects.filter(membership_request_id__in=request_ids).delete()
            MembershipLog.objects.filter(
                Q(membership_request_id__in=request_ids)
                | Q(target_username__in=COMMITTEE_REQUEST_USERNAMES, membership_type_id__in=membership_type_codes)
                | Q(actor_username__in=usernames)
            ).delete()
        else:
            MembershipLog.objects.filter(
                Q(target_username__in=COMMITTEE_REQUEST_USERNAMES, membership_type_id__in=membership_type_codes)
                | Q(actor_username__in=usernames)
            ).delete()

        request_queryset.delete()
        Membership.objects.filter(
            target_username__in=COMMITTEE_REQUEST_USERNAMES,
            membership_type_id__in=membership_type_codes,
        ).delete()

    def _ensure_committee_actor(self) -> None:
        client = get_e2e_service_client()
        country_attr = country_attr_name()
        embargoed_country_code = settings.MEMBERSHIP_EMBARGOED_COUNTRY_CODES[0]

        client.group_add(settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP, o_description="Fake E2E membership committee group")
        client.group_add("almalinux-individual", o_description="Fake E2E individual membership group")
        client.group_add("almalinux-mirror", o_description="Fake E2E mirror membership group")
        client.user_mod(
            COMMITTEE_REVIEWER_USERNAME,
            memberof_group=["packagers", settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            **{country_attr: "US"},
        )
        for username in COMMITTEE_REQUEST_USERNAMES:
            client.user_mod(username, **{country_attr: "US"})
        client.user_mod("regular06", **{country_attr: embargoed_country_code})

        agreement_cn = settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN
        agreement = FreeIPAFASAgreement.get(agreement_cn)
        if agreement is None:
            agreement = FreeIPAFASAgreement.create(agreement_cn, description="CoC")
        if COMMITTEE_REVIEWER_USERNAME not in agreement.users:
            agreement.add_user(COMMITTEE_REVIEWER_USERNAME)

    def _seed_slice(self) -> dict[str, object]:
        alias_payloads: dict[str, dict[str, object]] = {}
        request_aliases: dict[str, int] = {}
        workflow_examples: dict[str, dict[str, object]] = {}

        for offset, row in enumerate(COMMITTEE_REQUEST_MATRIX, start=1):
            membership_request = self._create_request(
                alias=str(row["alias"]),
                username=str(row["username"]),
                membership_type_code=str(row["membership_type_code"]),
                status=str(row["status"]),
                responses=list(row["responses"]),
                is_renewal=bool(row.get("is_renewal", False)),
                offset_minutes=int(row.get("sort_offset_minutes", offset)),
            )
            request_aliases[str(row["alias"])] = membership_request.pk
            alias_payloads[str(row["alias"])] = self._request_payload(
                membership_request=membership_request,
                alias=str(row["alias"]),
                is_renewal=bool(row.get("is_renewal", False)),
            )

        workflow_example_specs = (
            ("accepted", "regular15", "individual", MembershipRequest.Status.approved, "Approved after committee consensus."),
            ("rejected", "regular16", "individual", MembershipRequest.Status.rejected, "Rejected because required evidence was not provided."),
            ("ignored", "regular17", "mirror", MembershipRequest.Status.ignored, "Ignored for detail reopen coverage."),
        )
        for offset, spec in enumerate(workflow_example_specs, start=len(COMMITTEE_REQUEST_MATRIX) + len(ON_HOLD_PAGINATION_FILLERS) + 1):
            alias, username, membership_type_code, final_state, rejection_reason = spec
            membership_request = self._create_request(
                alias=alias,
                username=username,
                membership_type_code=membership_type_code,
                status=str(final_state),
                responses=self._workflow_example_responses(alias=alias, membership_type_code=membership_type_code),
                is_renewal=False,
                offset_minutes=offset,
            )
            workflow_examples[alias] = self._request_payload(
                membership_request=membership_request,
                alias=alias,
                is_renewal=False,
            )

        followup_offset = len(COMMITTEE_REQUEST_MATRIX) + len(ON_HOLD_PAGINATION_FILLERS) + len(workflow_example_specs) + 1
        followup_request = self._create_request(
            alias="rfi_followup_review",
            username="regular18",
            membership_type_code="mirror",
            status="rfi_followup_pending",
            responses=self._workflow_example_responses(alias="rfi_followup_review", membership_type_code="mirror"),
            is_renewal=False,
            offset_minutes=followup_offset,
        )
        workflow_examples["rfi_followup_review"] = self._request_payload(
            membership_request=followup_request,
            alias="rfi_followup_review",
            is_renewal=False,
        )

        for offset, filler in enumerate(ON_HOLD_PAGINATION_FILLERS, start=len(COMMITTEE_REQUEST_MATRIX) + 1):
            membership_request = self._create_request(
                alias=str(filler["alias"]),
                username=str(filler["username"]),
                membership_type_code=str(filler["membership_type_code"]),
                status=MembershipRequest.Status.on_hold,
                responses=[{"Domain": f"https://{filler['alias']}.example.test"}],
                is_renewal=False,
                offset_minutes=offset,
            )
            alias_payloads[str(filler["alias"])] = self._request_payload(
                membership_request=membership_request,
                alias=str(filler["alias"]),
                is_renewal=False,
            )

        shell_request = MembershipRequest.objects.get(pk=request_aliases["pending_shell_observer"])
        Note.objects.create(
            membership_request=shell_request,
            username=COMMITTEE_REVIEWER_USERNAME,
            content="Seeded queue note for stable browser note badges.",
        )

        return {
            "scenario": "membership-committee",
            "status": "reset",
            "actors": {
                "committee_reviewer": {
                    "username": COMMITTEE_REVIEWER_USERNAME,
                    "password": "password",
                    "request_aliases": request_aliases,
                }
            },
            "workflow_examples": workflow_examples,
            "scenarios": {
                scenario_name: {
                    "actor": COMMITTEE_REVIEWER_USERNAME,
                    "aliases": list(definition["aliases"]),
                    "destructive": bool(definition["destructive"]),
                }
                for scenario_name, definition in SCENARIO_ALIAS_MATRIX.items()
            },
            "requests": alias_payloads,
        }

    def _create_request(
        self,
        *,
        alias: str,
        username: str,
        membership_type_code: str,
        status: str,
        responses: list[dict[str, str]],
        is_renewal: bool,
        offset_minutes: int,
    ) -> MembershipRequest:
        now = timezone.now() - datetime.timedelta(minutes=offset_minutes)
        membership_type = MembershipType.objects.get(code=membership_type_code)
        if is_renewal:
            Membership.objects.create(
                target_username=username,
                membership_type_id=membership_type_code,
                expires_at=timezone.now() + datetime.timedelta(days=180),
            )

        final_action_at = now + datetime.timedelta(minutes=3)
        review_notes = [
            (
                COMMITTEE_SUPPORT_REVIEWERS[0],
                "vote_approve",
                f"{alias} reviewed by first committee member.",
                now + datetime.timedelta(minutes=1),
            ),
            (
                COMMITTEE_SUPPORT_REVIEWERS[1],
                "vote_disapprove",
                f"{alias} reviewed by second committee member.",
                now + datetime.timedelta(minutes=2),
            ),
        ]
        resubmitted_responses = None
        if status == "rfi_followup_pending":
            resubmitted_responses = [
                {"Domain": f"https://{alias}-updated.example.test"},
                {"Additional information": "Updated after committee RFI follow-up."},
            ]

        membership_request = seed_membership_request_workflow(
            requested_username=username,
            requested_organization=None,
            membership_type=membership_type,
            initial_responses=responses,
            requested_at=now,
            review_notes=review_notes,
            final_state=status,
            final_actor_username=COMMITTEE_REVIEWER_USERNAME,
            final_action_at=final_action_at,
            rejection_reason="Rejected by seeded committee workflow." if status == MembershipRequest.Status.rejected else "",
            rfi_message="Please provide the requested committee follow-up details.",
            application_url=reverse("membership-request-detail", kwargs={"pk": 0}),
            resubmitted_responses=resubmitted_responses,
            resubmitted_at=final_action_at + datetime.timedelta(minutes=1) if resubmitted_responses is not None else None,
        )

        return membership_request

    def _workflow_example_responses(self, *, alias: str, membership_type_code: str) -> list[dict[str, str]]:
        if membership_type_code == "mirror":
            return [
                {"Domain": f"https://{alias}.example.test"},
                {"Additional information": f"Seeded workflow example for {alias}."},
            ]
        return [{"Contributions": f"Seeded workflow example for {alias}."}]

    def _request_payload(
        self,
        *,
        membership_request: MembershipRequest,
        alias: str,
        is_renewal: bool,
    ) -> dict[str, object]:
        return {
            "alias": alias,
            "request_id": membership_request.pk,
            "requested_username": membership_request.requested_username,
            "membership_type_code": membership_request.membership_type_id,
            "status": membership_request.status,
            "is_renewal": is_renewal,
        }