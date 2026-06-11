import datetime
import json
from typing import Final, TypedDict, cast, override
from unittest.mock import patch

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.elections_services import (
    BallotReceipt,
    close_election,
    election_quorum_status,
    issue_credentials_at_start_transition,
    submit_ballot,
    tally_election,
)
from core.freeipa import e2e_registry
from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client, is_e2e_fake_freeipa_enabled
from core.models import (
    AuditLogEntry,
    Ballot,
    Candidate,
    Election,
    ElectionRoll,
    ExclusionGroup,
    FreeIPAPermissionGrant,
    Membership,
    MembershipType,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION
from core.tokens import election_chain_next_hash, election_genesis_chain_hash

ACTOR_PASSWORD: Final[str] = "password"
VIEWER_USERNAME: Final[str] = "regular16"
MANAGER_USERNAME: Final[str] = "regular17"

OPEN_LIST_ALIAS: Final[str] = "open_list_election"
PAST_LIST_ALIAS: Final[str] = "past_list_election"
DRAFT_MANAGER_ALIAS: Final[str] = "draft_manager_election"
MANAGER_OPEN_ALIAS: Final[str] = "manager_open_election"
DETAIL_OPEN_ALIAS: Final[str] = "detail_open_election"
DETAIL_TALLIED_ALIAS: Final[str] = "detail_tallied_election"

CLOSED_RECEIPT_ALIAS: Final[str] = "verify_closed_receipt"
TALLIED_RECEIPT_ALIAS: Final[str] = "verify_tallied_receipt"
SUPERSEDED_RECEIPT_ALIAS: Final[str] = "verify_superseded_receipt"
OPEN_MANAGER_CREDENTIAL_ALIAS: Final[str] = "open_manager_credential"

OPEN_ELECTION_NAME: Final[str] = "Wave 6 Open Election"
PAST_ELECTION_NAME: Final[str] = "Wave 6 Past Election"
DRAFT_ELECTION_NAME: Final[str] = "Wave 6 Draft Election"
MANAGER_OPEN_ELECTION_NAME: Final[str] = "Wave 6 Manager Open Election"
TALLIED_ELECTION_NAME: Final[str] = "Wave 6 Tallied Election"

MANAGER_ELIGIBLE_USERNAME: Final[str] = MANAGER_USERNAME
CANDIDATE_ONE_USERNAME: Final[str] = "regular18"
CANDIDATE_TWO_USERNAME: Final[str] = "regular19"
ELECTIONS_ELIGIBLE_GROUP_CN: Final[str] = "wave6-e2e-electorate"

SLICE_ELECTION_NAMES: Final[tuple[str, ...]] = (
    OPEN_ELECTION_NAME,
    PAST_ELECTION_NAME,
    DRAFT_ELECTION_NAME,
    MANAGER_OPEN_ELECTION_NAME,
    TALLIED_ELECTION_NAME,
)

class BallotSeedDefinition(TypedDict):
    credential_public_id: str
    ranking: list[int]
    weight: int
    nonce: str
    created_at: datetime.datetime
    previous_chain_hash: str


class MembershipSeedDefinition(TypedDict):
    username: str
    membership_type_code: str
    created_at: datetime.datetime
    expires_at: datetime.datetime


class AuditEntrySeedDefinition(TypedDict):
    timestamp: datetime.datetime
    event_type: str
    payload: dict[str, object]
    is_public: bool


def _dt(*, year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime.datetime:
    return timezone.make_aware(datetime.datetime(year, month, day, hour, minute, 0), timezone=datetime.UTC)


def _ballot_hash_for_seed(
    *,
    election_id: int,
    credential_public_id: str,
    ranking: list[int],
    weight: int,
    nonce: str,
) -> str:
    return Ballot.compute_hash(
        election_id=election_id,
        credential_public_id=credential_public_id,
        ranking=ranking,
        weight=weight,
        nonce=nonce,
    )


def _ballot_chain_hash_for_seed(
    *,
    previous_chain_hash: str,
    election_id: int,
    credential_public_id: str,
    ranking: list[int],
    weight: int,
    nonce: str,
) -> str:
    ballot_hash = _ballot_hash_for_seed(
        election_id=election_id,
        credential_public_id=credential_public_id,
        ranking=ranking,
        weight=weight,
        nonce=nonce,
    )
    return election_chain_next_hash(previous_chain_hash=previous_chain_hash, ballot_hash=ballot_hash)


class Command(BaseCommand):
    help = "Reset the Wave 6 elections E2E scenario state."

    @override
    def handle(self, *args, **options) -> None:
        del args, options

        if not is_e2e_fake_freeipa_enabled():
            raise CommandError(
                "elections_reset requires ASTRA_E2E_MODE=True and ASTRA_E2E_FAKE_FREEIPA_ENABLED=True."
            )

        with transaction.atomic():
            self._ensure_membership_types()
            self._clear_slice_memberships()
            elections_by_alias = self._upsert_slice_elections()
            self._ensure_manager_permission()
            self._ensure_signed_coc()
            payload = self._seed_slice(elections_by_alias=elections_by_alias)

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
                "votes": 1,
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
                "votes": 2,
            },
        )

    def _clear_slice_memberships(self) -> None:
        Membership.objects.filter(
            target_username__in=(MANAGER_ELIGIBLE_USERNAME, CANDIDATE_ONE_USERNAME, CANDIDATE_TWO_USERNAME),
            membership_type_id__in=("individual", "mirror"),
        ).delete()

    def _ensure_signed_coc(self) -> None:
        agreement_cn = settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN
        agreement = FreeIPAFASAgreement.get(agreement_cn)
        if agreement is None:
            agreement = FreeIPAFASAgreement.create(agreement_cn, description="CoC")

        for username in (VIEWER_USERNAME, MANAGER_USERNAME):
            if username not in agreement.users:
                agreement.add_user(username)

        client = get_e2e_service_client()
        for username in (VIEWER_USERNAME, MANAGER_USERNAME):
            client.user_mod(username, c="US")

        committee_group_cn = str(settings.FREEIPA_ELECTION_COMMITTEE_GROUP).strip()
        if client.group_find(o_cn=committee_group_cn)["count"] == 0:
            client.group_add(
                committee_group_cn,
                o_description="Wave 6 E2E election committee group",
            )

        required_members = {MANAGER_ELIGIBLE_USERNAME, CANDIDATE_ONE_USERNAME, CANDIDATE_TWO_USERNAME}
        group_registry = e2e_registry._e2e_group_registry()
        group_registry[ELECTIONS_ELIGIBLE_GROUP_CN] = {
            "cn": [ELECTIONS_ELIGIBLE_GROUP_CN],
            "description": ["Wave 6 E2E deterministic election electorate"],
            "member_user": sorted(required_members),
            "member_group": [],
            "membermanager_user": [],
            "membermanager_group": [],
            "fasgroup": ["FALSE"],
        }
        e2e_registry._write_e2e_group_registry(group_registry)

    def _upsert_slice_elections(self) -> dict[str, Election]:
        replace_when_ballots_exist_aliases = {
            OPEN_LIST_ALIAS,
            DRAFT_MANAGER_ALIAS,
            MANAGER_OPEN_ALIAS,
        }
        definitions = self._slice_election_definitions()

        elections_by_alias: dict[str, Election] = {}
        for alias, defaults in definitions:
            election = Election.objects.active().filter(name=str(defaults["name"])).first()
            if election is not None and alias in replace_when_ballots_exist_aliases and Ballot.objects.filter(election=election).exists():
                self._retire_contaminated_slice_election(election=election)
                election = None
            if election is None:
                election = Election.objects.create(**cast(dict[str, object], defaults))
            else:
                update_defaults = {
                    key: value for key, value in cast(dict[str, object], defaults).items() if key not in {"status", "tally_result"}
                }
                Election.objects.filter(pk=election.pk).update(**update_defaults)
                election.refresh_from_db()
            elections_by_alias[alias] = election

        elections_by_alias[DETAIL_OPEN_ALIAS] = elections_by_alias[OPEN_LIST_ALIAS]
        return elections_by_alias

    def _slice_election_definitions(self) -> tuple[tuple[str, dict[str, object]], ...]:
        return (
            (
                OPEN_LIST_ALIAS,
                {
                    "name": OPEN_ELECTION_NAME,
                    "description": "Wave 6 open-election summary coverage.",
                    "url": "https://example.test/elections/wave6-open",
                    "start_datetime": _dt(year=2026, month=4, day=1, hour=10),
                    "end_datetime": _dt(year=2026, month=4, day=10, hour=10),
                    "number_of_seats": 2,
                    "eligible_group_cn": ELECTIONS_ELIGIBLE_GROUP_CN,
                    "status": Election.Status.draft,
                    "tally_result": {},
                },
            ),
            (
                PAST_LIST_ALIAS,
                {
                    "name": PAST_ELECTION_NAME,
                    "description": "Wave 6 closed-election grouping and verification coverage.",
                    "url": "https://example.test/elections/wave6-past",
                    "start_datetime": _dt(year=2026, month=3, day=1, hour=10),
                    "end_datetime": _dt(year=2026, month=3, day=5, hour=10),
                    "number_of_seats": 1,
                    "eligible_group_cn": ELECTIONS_ELIGIBLE_GROUP_CN,
                    "status": Election.Status.draft,
                    "tally_result": {},
                },
            ),
            (
                DRAFT_MANAGER_ALIAS,
                {
                    "name": DRAFT_ELECTION_NAME,
                    "description": "Wave 6 manager-only draft routing coverage.",
                    "url": "",
                    "start_datetime": _dt(year=2026, month=5, day=1, hour=10),
                    "end_datetime": _dt(year=2026, month=5, day=10, hour=10),
                    "number_of_seats": 1,
                    "eligible_group_cn": ELECTIONS_ELIGIBLE_GROUP_CN,
                    "status": Election.Status.draft,
                    "tally_result": {},
                },
            ),
            (
                MANAGER_OPEN_ALIAS,
                {
                    "name": MANAGER_OPEN_ELECTION_NAME,
                    "description": "Wave 6 manager list-routing coverage.",
                    "url": "",
                    "start_datetime": _dt(year=2026, month=4, day=11, hour=10),
                    "end_datetime": _dt(year=2026, month=4, day=18, hour=10),
                    "number_of_seats": 1,
                    "eligible_group_cn": ELECTIONS_ELIGIBLE_GROUP_CN,
                    "status": Election.Status.draft,
                    "tally_result": {},
                },
            ),
            (
                DETAIL_TALLIED_ALIAS,
                {
                    "name": TALLIED_ELECTION_NAME,
                    "description": "Wave 6 tallied-election results coverage.",
                    "url": "https://example.test/elections/wave6-tallied",
                    "start_datetime": _dt(year=2026, month=2, day=1, hour=10),
                    "end_datetime": _dt(year=2026, month=2, day=7, hour=10),
                    "number_of_seats": 2,
                    "eligible_group_cn": ELECTIONS_ELIGIBLE_GROUP_CN,
                    "status": Election.Status.draft,
                    "tally_result": {},
                },
            ),
        )

    def _create_slice_election(self, *, alias: str) -> Election:
        for definition_alias, defaults in self._slice_election_definitions():
            if definition_alias == alias:
                return Election.objects.create(**cast(dict[str, object], defaults))
        raise CommandError(f"Unknown slice election alias '{alias}'.")

    def _reconcile_append_only_slice_election(
        self,
        *,
        alias: str,
        election: Election,
        candidate_definitions: tuple[dict[str, str], ...],
        expected_credential_public_ids: tuple[str, ...],
        ballot_definition_factory,
    ) -> tuple[Election, list[Candidate], tuple[BallotSeedDefinition, ...], bool]:
        candidates = self._upsert_candidates(election=election, definitions=candidate_definitions)
        ballot_definitions = ballot_definition_factory(election=election, candidates=candidates)

        if not Ballot.objects.filter(election=election).exists():
            return election, candidates, ballot_definitions, False

        credential_count = VotingCredential.objects.filter(election=election).count()
        ballot_mismatch = self._ballot_seed_mismatch_message(
            election=election,
            definitions=ballot_definitions,
        )
        if ballot_mismatch is None and credential_count == len(expected_credential_public_ids):
            return election, candidates, ballot_definitions, True

        self._retire_contaminated_slice_election(election=election)
        recreated_election = self._create_slice_election(alias=alias)
        recreated_candidates = self._upsert_candidates(
            election=recreated_election,
            definitions=candidate_definitions,
        )
        recreated_ballot_definitions = ballot_definition_factory(
            election=recreated_election,
            candidates=recreated_candidates,
        )
        return recreated_election, recreated_candidates, recreated_ballot_definitions, False

    def _retire_contaminated_slice_election(self, *, election: Election) -> None:
        for credential in VotingCredential.objects.filter(election=election).order_by("pk"):
            VotingCredential.objects.filter(pk=credential.pk).update(
                public_id=f"{credential.public_id}-retired-{credential.pk}"
            )

        Election.objects.filter(pk=election.pk).update(status=Election.Status.deleted)

    def _closed_ballot_definitions(
        self,
        *,
        election: Election,
        closed_candidates: list[Candidate],
    ) -> tuple[BallotSeedDefinition, ...]:
        return (
            {
                "credential_public_id": "wave6-closed-manager-credential",
                "ranking": [candidate.id for candidate in closed_candidates],
                "weight": 1,
                "nonce": "1" * 32,
                "created_at": _dt(year=2026, month=3, day=5, hour=12),
                "previous_chain_hash": election_genesis_chain_hash(election.id),
            },
        )

    def _tallied_ballot_definitions(
        self,
        *,
        election: Election,
        tallied_candidates: list[Candidate],
    ) -> tuple[BallotSeedDefinition, ...]:
        return (
            {
                "credential_public_id": "wave6-tallied-credential-one",
                "ranking": [tallied_candidates[1].id, tallied_candidates[0].id],
                "weight": 1,
                "nonce": "2" * 32,
                "created_at": _dt(year=2026, month=2, day=5, hour=12),
                "previous_chain_hash": election_genesis_chain_hash(election.id),
            },
            {
                "credential_public_id": "wave6-tallied-credential-one",
                "ranking": [tallied_candidates[0].id, tallied_candidates[1].id],
                "weight": 1,
                "nonce": "3" * 32,
                "created_at": _dt(year=2026, month=2, day=6, hour=12),
                "previous_chain_hash": _ballot_chain_hash_for_seed(
                    previous_chain_hash=election_genesis_chain_hash(election.id),
                    election_id=election.id,
                    credential_public_id="wave6-tallied-credential-one",
                    ranking=[tallied_candidates[1].id, tallied_candidates[0].id],
                    weight=1,
                    nonce="2" * 32,
                ),
            },
            {
                "credential_public_id": "wave6-tallied-credential-two",
                "ranking": [tallied_candidates[0].id],
                "weight": 1,
                "nonce": "4" * 32,
                "created_at": _dt(year=2026, month=2, day=6, hour=13),
                "previous_chain_hash": _ballot_chain_hash_for_seed(
                    previous_chain_hash=_ballot_chain_hash_for_seed(
                        previous_chain_hash=election_genesis_chain_hash(election.id),
                        election_id=election.id,
                        credential_public_id="wave6-tallied-credential-one",
                        ranking=[tallied_candidates[1].id, tallied_candidates[0].id],
                        weight=1,
                        nonce="2" * 32,
                    ),
                    election_id=election.id,
                    credential_public_id="wave6-tallied-credential-one",
                    ranking=[tallied_candidates[0].id, tallied_candidates[1].id],
                    weight=1,
                    nonce="3" * 32,
                ),
            },
        )

    def _ensure_manager_permission(self) -> None:
        FreeIPAPermissionGrant.objects.update_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=MANAGER_USERNAME,
            defaults={},
        )

    def _seed_slice(self, *, elections_by_alias: dict[str, Election]) -> dict[str, object]:
        open_election = elections_by_alias[OPEN_LIST_ALIAS]
        closed_election = elections_by_alias[PAST_LIST_ALIAS]
        draft_election = elections_by_alias[DRAFT_MANAGER_ALIAS]
        manager_open_election = elections_by_alias[MANAGER_OPEN_ALIAS]
        tallied_election = elections_by_alias[DETAIL_TALLIED_ALIAS]

        active_membership_expires_at = _dt(year=2026, month=12, day=31, hour=10)
        self._upsert_memberships(
            definitions=(
                {
                    "username": MANAGER_ELIGIBLE_USERNAME,
                    "membership_type_code": "individual",
                    "created_at": _dt(year=2026, month=1, day=5, hour=10),
                    "expires_at": active_membership_expires_at,
                },
                {
                    "username": CANDIDATE_ONE_USERNAME,
                    "membership_type_code": "individual",
                    "created_at": _dt(year=2026, month=1, day=5, hour=10),
                    "expires_at": active_membership_expires_at,
                },
                {
                    "username": CANDIDATE_TWO_USERNAME,
                    "membership_type_code": "individual",
                    "created_at": _dt(year=2026, month=1, day=5, hour=10),
                    "expires_at": active_membership_expires_at,
                },
            )
        )

        self._upsert_candidates(election=draft_election, definitions=())
        self._upsert_candidates(election=manager_open_election, definitions=())
        open_candidate_definitions = (
            {"username": "alice", "nominated_by": "regular21", "description": "Platform continuity candidate."},
            {"username": "bob", "nominated_by": "regular22", "description": "Infrastructure reliability candidate."},
        )
        self._upsert_candidates(election=open_election, definitions=open_candidate_definitions)
        closed_candidate_definitions = ({"username": "closed-candidate", "nominated_by": "regular23", "description": ""},)
        closed_election, closed_candidates, closed_ballot_definitions, closed_preserved = self._reconcile_append_only_slice_election(
            alias=PAST_LIST_ALIAS,
            election=closed_election,
            candidate_definitions=closed_candidate_definitions,
            expected_credential_public_ids=(
                "wave6-closed-manager-credential",
                "wave6-closed-candidate-one-credential",
                "wave6-closed-candidate-two-credential",
            ),
            ballot_definition_factory=lambda *, election, candidates: self._closed_ballot_definitions(
                election=election,
                closed_candidates=candidates,
            ),
        )
        tallied_candidate_definitions = (
            {"username": "alice", "nominated_by": "regular24", "description": "Seasoned release steward."},
            {"username": "bob", "nominated_by": "regular25", "description": "Operations-focused candidate."},
        )
        tallied_election, tallied_candidates, tallied_ballot_definitions, tallied_preserved = self._reconcile_append_only_slice_election(
            alias=DETAIL_TALLIED_ALIAS,
            election=tallied_election,
            candidate_definitions=tallied_candidate_definitions,
            expected_credential_public_ids=(
                "wave6-tallied-credential-one",
                "wave6-tallied-credential-two",
                "wave6-tallied-credential-three",
            ),
            ballot_definition_factory=lambda *, election, candidates: self._tallied_ballot_definitions(
                election=election,
                tallied_candidates=candidates,
            ),
        )
        elections_by_alias[PAST_LIST_ALIAS] = closed_election
        elections_by_alias[DETAIL_TALLIED_ALIAS] = tallied_election

        employees_group, _ = ExclusionGroup.objects.update_or_create(
            election=tallied_election,
            name="Employees",
            defaults={"max_elected": 1},
        )
        ExclusionGroup.objects.filter(election=tallied_election).exclude(pk=employees_group.pk).delete()
        ExclusionGroup.objects.filter(
            election__in=[
                open_election,
                closed_election,
                draft_election,
                manager_open_election,
            ]
        ).delete()
        employees_group.candidates.set([tallied_candidates[0], tallied_candidates[1]])

        self._clear_slice_workflow_state(
            elections=(open_election, closed_election, draft_election, manager_open_election, tallied_election)
        )

        open_credentials = self._start_seed_election(
            election=open_election,
            actor=MANAGER_USERNAME,
            started_at=_dt(year=2026, month=4, day=1, hour=10),
            credential_public_ids=(
                "wave6-open-manager-credential",
                "wave6-open-candidate-one-credential",
                "wave6-open-candidate-two-credential",
            ),
        )
        self._start_seed_election(
            election=manager_open_election,
            actor=MANAGER_USERNAME,
            started_at=_dt(year=2026, month=4, day=11, hour=10),
            credential_public_ids=(
                "wave6-manager-open-manager-credential",
                "wave6-manager-open-candidate-one-credential",
                "wave6-manager-open-candidate-two-credential",
            ),
        )
        if closed_preserved:
            closed_credentials = self._replay_append_only_seed_election(
                election=closed_election,
                actor=MANAGER_USERNAME,
                started_at=_dt(year=2026, month=3, day=1, hour=10),
                closed_at=_dt(year=2026, month=3, day=5, hour=13),
                credential_public_ids=(
                    "wave6-closed-manager-credential",
                    "wave6-closed-candidate-one-credential",
                    "wave6-closed-candidate-two-credential",
                ),
                ballot_definitions=closed_ballot_definitions,
            )
        else:
            closed_credentials = self._start_seed_election(
                election=closed_election,
                actor=MANAGER_USERNAME,
                started_at=_dt(year=2026, month=3, day=1, hour=10),
                credential_public_ids=(
                    "wave6-closed-manager-credential",
                    "wave6-closed-candidate-one-credential",
                    "wave6-closed-candidate-two-credential",
                ),
            )

        if tallied_preserved:
            tallied_credentials = self._replay_append_only_seed_election(
                election=tallied_election,
                actor=MANAGER_USERNAME,
                started_at=_dt(year=2026, month=2, day=1, hour=10),
                closed_at=_dt(year=2026, month=2, day=7, hour=10),
                credential_public_ids=(
                    "wave6-tallied-credential-one",
                    "wave6-tallied-credential-two",
                    "wave6-tallied-credential-three",
                ),
                ballot_definitions=tallied_ballot_definitions,
                tallied_at=_dt(year=2026, month=2, day=7, hour=11),
            )
        else:
            tallied_credentials = self._start_seed_election(
                election=tallied_election,
                actor=MANAGER_USERNAME,
                started_at=_dt(year=2026, month=2, day=1, hour=10),
                credential_public_ids=(
                    "wave6-tallied-credential-one",
                    "wave6-tallied-credential-two",
                    "wave6-tallied-credential-three",
                ),
            )

        open_credentials_by_username = {str(credential.freeipa_username): credential for credential in open_credentials}

        open_manager_credential = open_credentials_by_username[MANAGER_ELIGIBLE_USERNAME]
        closed_credentials_by_public_id = {credential.public_id: credential for credential in closed_credentials}
        tallied_credentials_by_public_id = {credential.public_id: credential for credential in tallied_credentials}

        closed_credential = closed_credentials_by_public_id["wave6-closed-manager-credential"]
        tallied_credential_one = tallied_credentials_by_public_id["wave6-tallied-credential-one"]
        tallied_credential_two = tallied_credentials_by_public_id["wave6-tallied-credential-two"]

        if not closed_preserved:
            self._submit_seed_ballot(
                election=closed_election,
                credential_public_id=closed_credential.public_id,
                ranking=[candidate.id for candidate in closed_candidates],
                submitted_at=_dt(year=2026, month=3, day=5, hour=12),
                nonce="1" * 32,
            )
            self._upsert_seed_quorum_reached_audit_entry(
                election=closed_election,
                reached_at=closed_ballot_definitions[0]["created_at"],
            )

        if not tallied_preserved:
            self._submit_seed_ballot(
                election=tallied_election,
                credential_public_id=tallied_credential_one.public_id,
                ranking=[tallied_candidates[1].id, tallied_candidates[0].id],
                submitted_at=_dt(year=2026, month=2, day=5, hour=12),
                nonce="2" * 32,
            )
            self._submit_seed_ballot(
                election=tallied_election,
                credential_public_id=tallied_credential_one.public_id,
                ranking=[tallied_candidates[0].id, tallied_candidates[1].id],
                submitted_at=_dt(year=2026, month=2, day=6, hour=12),
                nonce="3" * 32,
            )
            self._submit_seed_ballot(
                election=tallied_election,
                credential_public_id=tallied_credential_two.public_id,
                ranking=[tallied_candidates[0].id],
                submitted_at=_dt(year=2026, month=2, day=6, hour=13),
                nonce="4" * 32,
            )
            self._upsert_seed_quorum_reached_audit_entry(
                election=tallied_election,
                reached_at=tallied_ballot_definitions[0]["created_at"],
            )

        self._assert_expected_ballots(election=open_election, definitions=())
        self._assert_expected_ballots(election=draft_election, definitions=())
        self._assert_expected_ballots(election=manager_open_election, definitions=())
        self._assert_expected_ballots(
            election=closed_election,
            definitions=closed_ballot_definitions,
        )
        self._assert_expected_ballots(
            election=tallied_election,
            definitions=tallied_ballot_definitions,
        )

        if not closed_preserved:
            with patch("django.utils.timezone.now", return_value=_dt(year=2026, month=3, day=5, hour=13)):
                close_election(election=closed_election, actor=MANAGER_USERNAME)
        if not tallied_preserved:
            with patch("django.utils.timezone.now", return_value=_dt(year=2026, month=2, day=7, hour=10)):
                close_election(election=tallied_election, actor=MANAGER_USERNAME)
            with patch("django.utils.timezone.now", return_value=_dt(year=2026, month=2, day=7, hour=11)):
                tally_election(election=tallied_election, actor=MANAGER_USERNAME)

        open_election.refresh_from_db()
        closed_election.refresh_from_db()
        tallied_election.refresh_from_db()

        elections_payload = {
            OPEN_LIST_ALIAS: self._election_payload(open_election, route_name="election-detail"),
            PAST_LIST_ALIAS: self._election_payload(closed_election, route_name="election-detail"),
            DRAFT_MANAGER_ALIAS: self._election_payload(elections_by_alias[DRAFT_MANAGER_ALIAS], route_name="election-edit"),
            MANAGER_OPEN_ALIAS: self._election_payload(elections_by_alias[MANAGER_OPEN_ALIAS], route_name="election-detail"),
            DETAIL_OPEN_ALIAS: self._election_payload(open_election, route_name="election-detail"),
            DETAIL_TALLIED_ALIAS: self._election_payload(tallied_election, route_name="election-detail"),
        }

        return {
            "scenario": "elections",
            "status": "reset",
            "actors": {
                "viewer": {"username": VIEWER_USERNAME, "password": ACTOR_PASSWORD},
                "manager": {"username": MANAGER_USERNAME, "password": ACTOR_PASSWORD},
            },
            "elections": elections_payload,
            "receipts": {
                CLOSED_RECEIPT_ALIAS: {
                    "ballot_hash": _ballot_hash_for_seed(
                        election_id=closed_election.id,
                        credential_public_id=closed_credential.public_id,
                        ranking=[candidate.id for candidate in closed_candidates],
                        weight=1,
                        nonce="1" * 32,
                    ),
                    "verification_state": "closed",
                },
                TALLIED_RECEIPT_ALIAS: {
                    "ballot_hash": _ballot_hash_for_seed(
                        election_id=tallied_election.id,
                        credential_public_id=tallied_credential_two.public_id,
                        ranking=[tallied_candidates[0].id],
                        weight=1,
                        nonce="4" * 32,
                    ),
                    "verification_state": "tallied",
                },
                SUPERSEDED_RECEIPT_ALIAS: {
                    "ballot_hash": _ballot_hash_for_seed(
                        election_id=tallied_election.id,
                        credential_public_id=tallied_credential_one.public_id,
                        ranking=[tallied_candidates[1].id, tallied_candidates[0].id],
                        weight=1,
                        nonce="2" * 32,
                    ),
                    "verification_state": "superseded",
                },
            },
            "credentials": {
                OPEN_MANAGER_CREDENTIAL_ALIAS: {
                    "public_id": open_manager_credential.public_id,
                    "freeipa_username": str(open_manager_credential.freeipa_username),
                    "weight": int(open_manager_credential.weight),
                    "election_alias": DETAIL_OPEN_ALIAS,
                },
            },
            "routes": {
                "algorithm": reverse("election-algorithm"),
                "audit_tallied": reverse("election-audit-log", args=[tallied_election.id]),
                "ballot_verify": reverse("ballot-verify"),
                "closed_detail": elections_payload[PAST_LIST_ALIAS]["route"],
                "edit_draft": elections_payload[DRAFT_MANAGER_ALIAS]["route"],
                "open_detail": elections_payload[DETAIL_OPEN_ALIAS]["route"],
                "open_vote": reverse("election-vote", args=[open_election.id]),
                "tallied_detail": elections_payload[DETAIL_TALLIED_ALIAS]["route"],
                "turnout_report": reverse("elections-turnout-report"),
            },
            "scenarios": {
                "elections-list-viewer-shell": {
                    "actor": VIEWER_USERNAME,
                    "aliases": [OPEN_LIST_ALIAS, PAST_LIST_ALIAS],
                    "destructive": False,
                    "route_target": reverse("elections"),
                },
                "elections-list-manager-draft-routing": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DRAFT_MANAGER_ALIAS, MANAGER_OPEN_ALIAS],
                    "destructive": False,
                    "route_target": reverse("elections"),
                },
                "elections-detail-open-summary": {
                    "actor": VIEWER_USERNAME,
                    "aliases": [DETAIL_OPEN_ALIAS],
                    "destructive": False,
                    "route_target": elections_payload[DETAIL_OPEN_ALIAS]["route"],
                },
                "elections-detail-tallied-results": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DETAIL_TALLIED_ALIAS],
                    "destructive": False,
                    "route_target": elections_payload[DETAIL_TALLIED_ALIAS]["route"],
                },
                "elections-vote-ranking-submit-and-copy-receipt": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DETAIL_OPEN_ALIAS, OPEN_MANAGER_CREDENTIAL_ALIAS],
                    "destructive": True,
                    "route_target": reverse("election-vote", args=[open_election.id]),
                },
                "elections-vote-ineligible-state": {
                    "actor": VIEWER_USERNAME,
                    "aliases": [DETAIL_OPEN_ALIAS],
                    "destructive": False,
                    "route_target": elections_payload[DETAIL_OPEN_ALIAS]["route"],
                },
                "elections-detail-operator-actions": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DETAIL_OPEN_ALIAS, PAST_LIST_ALIAS, OPEN_MANAGER_CREDENTIAL_ALIAS],
                    "destructive": False,
                    "route_target": elections_payload[DETAIL_OPEN_ALIAS]["route"],
                },
                "elections-turnout-report-shell": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DETAIL_TALLIED_ALIAS, PAST_LIST_ALIAS],
                    "destructive": False,
                    "route_target": reverse("elections-turnout-report"),
                },
                "elections-audit-log-finished-shell": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DETAIL_TALLIED_ALIAS],
                    "destructive": False,
                    "route_target": reverse("election-audit-log", args=[tallied_election.id]),
                },
                "elections-algorithm-shell": {
                    "actor": VIEWER_USERNAME,
                    "aliases": [],
                    "destructive": False,
                    "route_target": reverse("election-algorithm"),
                },
                "elections-edit-draft-save-and-start": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DRAFT_MANAGER_ALIAS],
                    "destructive": True,
                    "route_target": elections_payload[DRAFT_MANAGER_ALIAS]["route"],
                },
                "elections-edit-manage-candidates-exclusion-groups-and-email": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DRAFT_MANAGER_ALIAS],
                    "destructive": True,
                    "route_target": elections_payload[DRAFT_MANAGER_ALIAS]["route"],
                },
                "elections-ballot-verify-closed-public-state": {
                    "actor": "public",
                    "aliases": [CLOSED_RECEIPT_ALIAS],
                    "destructive": False,
                    "route_target": reverse("ballot-verify"),
                },
                "elections-ballot-verify-tallied-public-states": {
                    "actor": "public",
                    "aliases": [TALLIED_RECEIPT_ALIAS, SUPERSEDED_RECEIPT_ALIAS],
                    "destructive": False,
                    "route_target": reverse("ballot-verify"),
                },
                "elections-email-open-reminder": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DETAIL_OPEN_ALIAS],
                    "destructive": True,
                    "route_target": elections_payload[DETAIL_OPEN_ALIAS]["route"],
                },
                "elections-email-closed-send": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [PAST_LIST_ALIAS],
                    "destructive": True,
                    "route_target": elections_payload[PAST_LIST_ALIAS]["route"],
                },
                "elections-email-tallied-send": {
                    "actor": MANAGER_USERNAME,
                    "aliases": [DETAIL_TALLIED_ALIAS],
                    "destructive": True,
                    "route_target": elections_payload[DETAIL_TALLIED_ALIAS]["route"],
                },
            },
        }

    def _upsert_candidates(
        self,
        *,
        election: Election,
        definitions: tuple[dict[str, str], ...],
    ) -> list[Candidate]:
        expected_usernames = {definition["username"] for definition in definitions}
        Candidate.objects.filter(election=election).exclude(freeipa_username__in=expected_usernames).delete()

        candidates: list[Candidate] = []
        for definition in definitions:
            candidate, _ = Candidate.objects.update_or_create(
                election=election,
                freeipa_username=definition["username"],
                defaults={
                    "nominated_by": definition["nominated_by"],
                    "description": definition["description"],
                    "url": "",
                },
            )
            candidates.append(candidate)

        candidates.sort(key=lambda candidate: (candidate.freeipa_username, candidate.id))
        return candidates

    def _upsert_memberships(
        self,
        *,
        definitions: tuple[MembershipSeedDefinition, ...],
    ) -> None:
        for definition in definitions:
            membership, _ = Membership.objects.update_or_create(
                target_username=str(definition["username"]),
                membership_type_id=str(definition["membership_type_code"]),
                defaults={
                    "expires_at": definition["expires_at"],
                },
            )
            Membership.objects.filter(pk=membership.pk).update(
                created_at=definition["created_at"],
                expires_at=definition["expires_at"],
            )

    def _clear_slice_workflow_state(
        self,
        *,
        elections: tuple[Election, ...],
    ) -> None:
        for election in elections:
            if Ballot.objects.filter(election=election).exists():
                election.refresh_from_db()
                continue
            VotingCredential.objects.filter(election=election).delete()
            ElectionRoll.objects.filter(election=election).delete()
            AuditLogEntry.objects.filter(election=election).delete()
            Election.objects.filter(pk=election.pk).update(
                status=Election.Status.draft,
                tally_result={},
                public_ballots_file="",
                public_audit_file="",
                artifacts_generated_at=None,
            )
            election.refresh_from_db()

    def _start_seed_election(
        self,
        *,
        election: Election,
        actor: str,
        started_at: datetime.datetime,
        credential_public_ids: tuple[str, ...],
    ) -> list[VotingCredential]:
        if election.status != Election.Status.draft:
            raise CommandError(f"Expected draft election before start replay for '{election.name}'.")

        candidate_snapshot = list(
            Candidate.objects.filter(election=election)
            .only("id", "freeipa_username", "tiebreak_uuid")
            .order_by("freeipa_username", "id")
        )
        with (
            patch("django.utils.timezone.now", return_value=started_at),
            patch("core.elections_services.VotingCredential.generate_public_id", side_effect=list(credential_public_ids)),
        ):
            election.status = Election.Status.open
            election.start_datetime = started_at
            election.save(update_fields=["status", "start_datetime"])
            credentials = issue_credentials_at_start_transition(election=election)
            AuditLogEntry.objects.create(
                election=election,
                event_type="election_started",
                payload={
                    "eligible_voters": len(credentials),
                    "emailed": 0,
                    "skipped": 0,
                    "failures": 0,
                    "genesis_chain_hash": election_genesis_chain_hash(election.id),
                    "actor": actor,
                    "candidates": [
                        {
                            "id": candidate.id,
                            "freeipa_username": candidate.freeipa_username,
                            "tiebreak_uuid": str(candidate.tiebreak_uuid),
                        }
                        for candidate in candidate_snapshot
                    ],
                },
                is_public=True,
            )

        return list(VotingCredential.objects.filter(election=election).order_by("freeipa_username", "id"))

    def _replay_append_only_seed_election(
        self,
        *,
        election: Election,
        actor: str,
        started_at: datetime.datetime,
        closed_at: datetime.datetime,
        credential_public_ids: tuple[str, ...],
        ballot_definitions: tuple[BallotSeedDefinition, ...],
        tallied_at: datetime.datetime | None = None,
    ) -> list[VotingCredential]:
        self._reconverge_anonymized_credentials(
            election=election,
            credential_public_ids=credential_public_ids,
        )
        self._ensure_election_roll(election=election)
        lifecycle_event_types = [
            "ballot_submitted",
            "election_anonymized",
            "election_closed",
            "election_started",
            "quorum_reached",
        ]
        if tallied_at is not None:
            lifecycle_event_types.extend(["tally_completed", "tally_round"])
        AuditLogEntry.objects.filter(
            election=election,
            event_type__in=lifecycle_event_types,
        ).delete()
        Election.objects.filter(pk=election.pk).update(
            status=Election.Status.open,
            start_datetime=started_at,
            tally_result={},
            public_ballots_file="",
            public_audit_file="",
            artifacts_generated_at=None,
        )
        election.refresh_from_db()
        started_entry = AuditLogEntry.objects.create(
            election=election,
            event_type="election_started",
            payload=self._election_started_payload(election=election, actor=actor),
            is_public=True,
        )
        AuditLogEntry.objects.filter(pk=started_entry.pk).update(timestamp=started_at)
        self._replace_seed_ballot_submission_audit_entries(
            election=election,
            definitions=ballot_definitions,
        )
        self._upsert_seed_quorum_reached_audit_entry(
            election=election,
            reached_at=ballot_definitions[0]["created_at"],
        )
        with patch("django.utils.timezone.now", return_value=closed_at):
            close_election(election=election, actor=actor)
        if tallied_at is not None:
            with patch("django.utils.timezone.now", return_value=tallied_at):
                tally_election(election=election, actor=actor)
        return list(VotingCredential.objects.filter(election=election).order_by("created_at", "id"))

    def _reconverge_anonymized_credentials(
        self,
        *,
        election: Election,
        credential_public_ids: tuple[str, ...],
    ) -> None:
        credentials = list(VotingCredential.objects.filter(election=election).order_by("created_at", "id"))
        if len(credentials) != len(credential_public_ids):
            raise CommandError(
                f"Expected {len(credential_public_ids)} preserved credentials for slice-owned election '{election.name}'."
            )
        for credential in credentials:
            VotingCredential.objects.filter(pk=credential.pk).update(
                public_id=f"{credential.public_id}-reseed-{credential.pk}",
            )
        for credential, public_id in zip(credentials, credential_public_ids, strict=True):
            VotingCredential.objects.filter(pk=credential.pk).update(
                public_id=public_id,
                freeipa_username=None,
                weight=1,
            )

    def _ensure_election_roll(self, *, election: Election) -> None:
        """Populate ElectionRoll from group membership if missing.

        The replay path preserves existing ballots and credentials, but on
        first run after the ElectionRoll migration the roll won't exist yet.
        """
        if ElectionRoll.objects.filter(election=election).exists():
            return

        from core.elections_eligibility import eligible_voters_from_memberships

        eligible = eligible_voters_from_memberships(election=election)
        ElectionRoll.objects.bulk_create(
            [ElectionRoll(election=election, freeipa_username=v.username) for v in eligible],
        )

    def _replace_seed_ballot_submission_audit_entries(
        self,
        *,
        election: Election,
        definitions: tuple[BallotSeedDefinition, ...],
    ) -> None:
        superseded_ballot_hash_by_credential: dict[str, str] = {}
        for definition in definitions:
            ballot_hash = _ballot_hash_for_seed(
                election_id=election.id,
                credential_public_id=definition["credential_public_id"],
                ranking=definition["ranking"],
                weight=definition["weight"],
                nonce=definition["nonce"],
            )
            payload: dict[str, object] = {"ballot_hash": ballot_hash}
            superseded_ballot_hash = superseded_ballot_hash_by_credential.get(definition["credential_public_id"])
            if superseded_ballot_hash is not None:
                payload["supersedes_ballot_hash"] = superseded_ballot_hash
            entry = AuditLogEntry.objects.create(
                election=election,
                event_type="ballot_submitted",
                payload=payload,
                is_public=False,
            )
            AuditLogEntry.objects.filter(pk=entry.pk).update(timestamp=definition["created_at"])
            superseded_ballot_hash_by_credential[definition["credential_public_id"]] = ballot_hash

    def _submit_seed_ballot(
        self,
        *,
        election: Election,
        credential_public_id: str,
        ranking: list[int],
        submitted_at: datetime.datetime,
        nonce: str,
    ) -> BallotReceipt:
        with (
            patch("django.utils.timezone.now", return_value=submitted_at),
            patch("core.elections_services.secrets.token_hex", return_value=nonce),
        ):
            return submit_ballot(
                election=election,
                credential_public_id=credential_public_id,
                ranking=ranking,
            )

    def _assert_expected_ballots(
        self,
        *,
        election: Election,
        definitions: tuple[BallotSeedDefinition, ...],
    ) -> None:
        ballot_mismatch = self._ballot_seed_mismatch_message(election=election, definitions=definitions)
        if ballot_mismatch is not None:
            raise CommandError(ballot_mismatch)

    def _ballot_seed_mismatch_message(
        self,
        *,
        election: Election,
        definitions: tuple[BallotSeedDefinition, ...],
    ) -> str | None:
        unexpected_ballots = self._unexpected_ballot_hashes(election=election, definitions=definitions)
        if unexpected_ballots:
            return (
                f"Unexpected existing ballots for slice-owned election '{election.name}': {', '.join(unexpected_ballots)}"
            )
        if not definitions:
            return None

        expected_ballots: list[dict[str, object]] = []
        for definition in definitions:
            ranking = definition["ranking"]
            weight = definition["weight"]
            credential_public_id = definition["credential_public_id"]
            nonce = definition["nonce"]
            previous_chain_hash = definition["previous_chain_hash"]
            ballot_hash = Ballot.compute_hash(
                election_id=election.id,
                credential_public_id=credential_public_id,
                ranking=ranking,
                weight=weight,
                nonce=nonce,
            )
            expected_ballots.append(
                {
                    "ballot_hash": ballot_hash,
                    "credential_public_id": credential_public_id,
                    "ranking": ranking,
                    "weight": weight,
                    "created_at": definition["created_at"],
                    "previous_chain_hash": previous_chain_hash,
                    "chain_hash": election_chain_next_hash(
                        previous_chain_hash=previous_chain_hash,
                        ballot_hash=ballot_hash,
                    ),
                }
            )

        existing_ballots = list(Ballot.objects.filter(election=election).order_by("created_at", "id"))
        existing_ballots_by_hash = {ballot.ballot_hash: ballot for ballot in existing_ballots}
        for definition in expected_ballots:
            existing_ballot = existing_ballots_by_hash.get(str(definition["ballot_hash"]))
            if existing_ballot is None:
                continue
            if (
                existing_ballot.credential_public_id != definition["credential_public_id"]
                or list(existing_ballot.ranking) != definition["ranking"]
                or existing_ballot.weight != definition["weight"]
                or existing_ballot.created_at != definition["created_at"]
                or existing_ballot.previous_chain_hash != definition["previous_chain_hash"]
                or existing_ballot.chain_hash != definition["chain_hash"]
            ):
                return f"Existing ballot for slice-owned election '{election.name}' does not match the canonical seed."
        return None

    def _unexpected_ballot_hashes(
        self,
        *,
        election: Election,
        definitions: tuple[BallotSeedDefinition, ...],
    ) -> list[str]:
        expected_ballot_hashes = {
            Ballot.compute_hash(
                election_id=election.id,
                credential_public_id=definition["credential_public_id"],
                ranking=definition["ranking"],
                weight=definition["weight"],
                nonce=definition["nonce"],
            )
            for definition in definitions
        }
        return [
            ballot.ballot_hash
            for ballot in Ballot.objects.filter(election=election).order_by("created_at", "id")
            if ballot.ballot_hash not in expected_ballot_hashes
        ]

    def _upsert_seed_quorum_reached_audit_entry(
        self,
        *,
        election: Election,
        reached_at: datetime.datetime,
    ) -> None:
        status = election_quorum_status(election=election)
        if not bool(status["quorum_met"]):
            return

        entry, _ = AuditLogEntry.objects.get_or_create(
            election=election,
            event_type="quorum_reached",
            defaults={"payload": status, "is_public": True},
        )
        AuditLogEntry.objects.filter(pk=entry.pk).update(
            payload=status,
            is_public=True,
            timestamp=reached_at,
        )

    def _election_started_payload(self, *, election: Election, actor: str) -> dict[str, object]:
        candidates = list(
            Candidate.objects.filter(election=election)
            .only("id", "freeipa_username", "tiebreak_uuid")
            .order_by("freeipa_username", "id")
        )
        credential_count = VotingCredential.objects.filter(election=election).count()
        return {
            "eligible_voters": credential_count,
            "emailed": 0,
            "skipped": 0,
            "failures": 0,
            "genesis_chain_hash": election_genesis_chain_hash(election.id),
            "actor": actor,
            "candidates": [
                {
                    "id": candidate.id,
                    "freeipa_username": candidate.freeipa_username,
                    "tiebreak_uuid": str(candidate.tiebreak_uuid),
                }
                for candidate in candidates
            ],
        }

    def _election_payload(self, election: Election, *, route_name: str) -> dict[str, object]:
        return {
            "id": election.id,
            "name": election.name,
            "route": reverse(route_name, args=[election.id]),
        }