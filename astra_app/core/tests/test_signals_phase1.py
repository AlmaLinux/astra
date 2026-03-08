import datetime
import importlib
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.dispatch import Signal
from django.test import TestCase, override_settings
from django.urls import clear_url_caches, reverse
from django.utils import timezone

from core.elections_services import (
    close_election,
    extend_election_end_datetime,
    submit_ballot,
    tally_election,
)
from core.membership_request_workflow import (
    approve_membership_request,
    put_membership_request_on_hold,
    record_membership_request_created,
    reject_membership_request,
    rescind_membership_request,
    resubmit_membership_request,
)
from core.models import (
    Ballot,
    Candidate,
    Election,
    FreeIPAPermissionGrant,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
    Organization,
    VotingCredential,
)
from core.organization_claim import make_organization_claim_token
from core.permissions import ASTRA_ADD_ELECTION
from core.tests.ballot_chain import compute_chain_hash
from core.tests.utils_test_data import ensure_core_categories, ensure_email_templates
from core.tokens import election_genesis_chain_hash


class Phase1SignalsRegistryTests(TestCase):
    def test_all_current_canonical_signals_are_importable_and_signal_instances(self) -> None:
        signal_module = importlib.import_module("core.signals")

        expected_names = [
            "account_invitation_accepted",
            "election_opened",
            "election_closed",
            "election_tallied",
            "election_deadline_extended",
            "election_quorum_met",
            "membership_request_submitted",
            "membership_request_approved",
            "membership_request_rejected",
            "membership_request_rescinded",
            "membership_rfi_sent",
            "membership_rfi_replied",
            "membership_expiring_soon",
            "membership_expired",
            "organization_membership_request_submitted",
            "organization_membership_request_approved",
            "organization_membership_request_rejected",
            "organization_membership_request_rescinded",
            "organization_membership_rfi_sent",
            "organization_membership_rfi_replied",
            "organization_claimed",
            "organization_created",
            "user_country_changed",
            "organization_country_changed",
        ]

        self.assertEqual(set(signal_module.CANONICAL_SIGNALS.keys()), set(expected_names))
        for name in expected_names:
            with self.subTest(signal=name):
                signal_obj = getattr(signal_module, name)
                self.assertIsInstance(signal_obj, Signal)


class Phase1ElectionSignalTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()
        ensure_email_templates()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_manage_elections(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
            permission=ASTRA_ADD_ELECTION,
        )

    def _create_closed_election_with_ballot(self) -> Election:
        now = timezone.now()
        election = Election.objects.create(
            name="Signal tally election",
            description="",
            start_datetime=now - datetime.timedelta(days=10),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        candidate_a = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nom")
        candidate_b = Candidate.objects.create(election=election, freeipa_username="bob", nominated_by="nom")

        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-tally",
            ranking=[candidate_a.id, candidate_b.id],
            weight=1,
            nonce="0" * 32,
        )
        genesis_hash = election_genesis_chain_hash(election.id)
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-tally",
            ranking=[candidate_a.id, candidate_b.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )
        return election

    def test_election_opened_signal_emitted_from_edit_view(self) -> None:
        now = timezone.now()
        started_at = now + datetime.timedelta(hours=2)
        election = Election.objects.create(
            name="Draft election",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.draft,
            voting_email_subject="Hello {{ username }}",
            voting_email_html="<p>Hi {{ username }}</p>",
            voting_email_text="Hi {{ username }}",
        )
        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nominator")

        voter_type = MembershipType.objects.create(
            code="voter-signal",
            name="Voter",
            category_id="individual",
            sort_order=1,
            enabled=True,
            votes=1,
        )

        from core.models import Membership

        voter_membership = Membership.objects.create(
            target_username="voter1",
            membership_type=voter_type,
            expires_at=None,
        )
        Membership.objects.filter(pk=voter_membership.pk).update(created_at=now - datetime.timedelta(days=200))

        candidate_membership = Membership.objects.create(
            target_username="alice",
            membership_type=voter_type,
            expires_at=None,
        )
        Membership.objects.filter(pk=candidate_membership.pk).update(created_at=now - datetime.timedelta(days=200))

        nominator_membership = Membership.objects.create(
            target_username="nominator",
            membership_type=voter_type,
            expires_at=None,
        )
        Membership.objects.filter(pk=nominator_membership.pk).update(created_at=now - datetime.timedelta(days=200))

        self._login_as_freeipa_user("admin")
        self._grant_manage_elections("admin")

        start_str = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        end_str = (now + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")

        signal_module = importlib.import_module("core.signals")

        with (
            patch("core.freeipa.user.FreeIPAUser.get") as freeipa_get,
            patch("core.views_elections.edit.timezone.now", return_value=started_at),
            patch("post_office.mail.send", autospec=True),
            patch.object(signal_module.election_opened, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            admin_user = Mock()
            admin_user.username = "admin"
            freeipa_get.side_effect = [admin_user, Mock(username="voter1", email="voter1@example.com"), None, None]

            response = self.client.post(
                reverse("election-edit", args=[election.id]),
                data={
                    "action": "start_election",
                    "name": election.name,
                    "description": election.description,
                    "url": election.url,
                    "start_datetime": start_str,
                    "end_datetime": end_str,
                    "number_of_seats": str(election.number_of_seats),
                    "quorum": str(election.quorum),
                    "email_template_id": "",
                    "subject": election.voting_email_subject,
                    "html_content": election.voting_email_html,
                    "text_content": election.voting_email_text,
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), Election)
        self.assertEqual(kwargs.get("actor"), "admin")
        self.assertIsNotNone(kwargs.get("election"))
        self.assertEqual(kwargs.get("election").id, election.id)

    def test_election_closed_signal_emitted_with_expected_kwargs(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Close signal election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-close",
            freeipa_username="voter1",
            weight=1,
        )

        signal_module = importlib.import_module("core.signals")
        with (
            patch.object(signal_module.election_closed, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            close_election(election=election, actor="closer")

        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), Election)
        self.assertEqual(kwargs.get("actor"), "closer")
        self.assertEqual(kwargs.get("election").id, election.id)

    def test_election_tallied_signal_emitted_with_expected_kwargs(self) -> None:
        election = self._create_closed_election_with_ballot()

        signal_module = importlib.import_module("core.signals")
        with (
            patch.object(signal_module.election_tallied, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            tally_election(election=election, actor="tallier")

        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), Election)
        self.assertEqual(kwargs.get("actor"), "tallier")
        self.assertEqual(kwargs.get("election").id, election.id)

    def test_election_deadline_extended_signal_emitted_with_expected_kwargs(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Extend signal election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        old_end = election.end_datetime
        new_end = old_end + datetime.timedelta(hours=12)

        signal_module = importlib.import_module("core.signals")
        with (
            patch.object(signal_module.election_deadline_extended, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            extend_election_end_datetime(
                election=election,
                new_end_datetime=new_end,
                actor="extender",
            )

        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), Election)
        self.assertEqual(kwargs.get("actor"), "extender")
        self.assertEqual(kwargs.get("election").id, election.id)
        self.assertEqual(kwargs.get("previous_end_datetime"), old_end)
        self.assertEqual(kwargs.get("new_end_datetime"), new_end)

    def test_election_quorum_met_signal_emitted_on_first_quorum_reach(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Quorum signal election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
            quorum=100,
        )
        candidate = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nom")
        VotingCredential.objects.create(
            election=election,
            public_id="cred-quorum",
            freeipa_username="voter1",
            weight=1,
        )

        signal_module = importlib.import_module("core.signals")
        with (
            patch("core.elections_services.election_quorum_status", return_value={
                "required_participating_voter_count": 1,
                "required_participating_vote_weight_total": 1,
                "quorum_met": True,
            }),
            patch.object(signal_module.election_quorum_met, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            submit_ballot(
                election=election,
                credential_public_id="cred-quorum",
                ranking=[candidate.id],
            )

        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), Election)
        self.assertEqual(kwargs.get("election").id, election.id)

    def test_on_commit_signal_does_not_fire_before_commit_for_close(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="On-commit close signal election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-close-on-commit",
            freeipa_username="voter1",
            weight=1,
        )

        signal_module = importlib.import_module("core.signals")
        with patch.object(signal_module.election_closed, "send", autospec=True) as send_mock:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                close_election(election=election, actor="closer")

            self.assertFalse(send_mock.called)
            for callback in callbacks:
                callback()

        send_mock.assert_called_once()


class Phase1MembershipWorkflowSignalTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_email_templates()
        MembershipTypeCategory.objects.update_or_create(
            pk="individual",
            defaults={
                "is_individual": True,
                "is_organization": False,
                "sort_order": 0,
            },
        )
        MembershipTypeCategory.objects.update_or_create(
            pk="sponsorship",
            defaults={
                "is_individual": False,
                "is_organization": True,
                "sort_order": 1,
            },
        )
        self.user_type, _ = MembershipType.objects.update_or_create(
            code="individual-signal",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )
        self.org_type, _ = MembershipType.objects.update_or_create(
            code="org-signal",
            defaults={
                "name": "Org",
                "group_cn": "",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
            },
        )

    def _make_user_request(
        self,
        *,
        username: str,
        status: str = MembershipRequest.Status.pending,
    ) -> MembershipRequest:
        return MembershipRequest.objects.create(
            requested_username=username,
            membership_type=self.user_type,
            status=status,
        )

    def _make_org_request(self, *, status: str = MembershipRequest.Status.pending) -> MembershipRequest:
        organization = Organization.objects.create(
            name="Signal Org",
            representative="",
            business_contact_email="org@example.com",
        )
        return MembershipRequest.objects.create(
            requested_username="",
            requested_organization=organization,
            membership_type=self.org_type,
            status=status,
        )

    def _user_target_obj(self) -> object:
        class _Target:
            username = "alice"
            email = ""
            first_name = "Alice"
            last_name = "User"
            full_name = "Alice User"

            def add_to_group(self, *, group_name: str) -> None:
                _ = group_name

        return _Target()

    def test_user_target_workflow_signals_emit_expected_kwargs(self) -> None:
        signal_module = importlib.import_module("core.signals")

        request_submitted = self._make_user_request(username="alice-submitted")
        request_approved = self._make_user_request(username="alice-approved")
        request_rejected = self._make_user_request(username="alice-rejected")
        request_rescinded = self._make_user_request(username="alice-rescinded")
        request_rfi_sent = self._make_user_request(username="alice-rfi-sent")
        request_rfi_replied = self._make_user_request(
            username="alice-rfi-replied",
            status=MembershipRequest.Status.on_hold,
        )

        with (
            patch("core.membership_request_workflow.missing_required_agreements_for_user_in_group", return_value=[]),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=self._user_target_obj()),
            patch.object(signal_module.membership_request_submitted, "send", autospec=True) as submitted_send,
            patch.object(signal_module.membership_request_approved, "send", autospec=True) as approved_send,
            patch.object(signal_module.membership_request_rejected, "send", autospec=True) as rejected_send,
            patch.object(signal_module.membership_request_rescinded, "send", autospec=True) as rescinded_send,
            patch.object(signal_module.membership_rfi_sent, "send", autospec=True) as rfi_sent_send,
            patch.object(signal_module.membership_rfi_replied, "send", autospec=True) as rfi_replied_send,
            self.captureOnCommitCallbacks(execute=True),
        ):
            record_membership_request_created(
                membership_request=request_submitted,
                actor_username="alice",
                send_submitted_email=False,
            )
            approve_membership_request(
                membership_request=request_approved,
                actor_username="reviewer",
                send_approved_email=False,
            )
            reject_membership_request(
                membership_request=request_rejected,
                actor_username="reviewer",
                rejection_reason="No",
                send_rejected_email=False,
            )
            rescind_membership_request(
                membership_request=request_rescinded,
                actor_username="alice",
            )
            put_membership_request_on_hold(
                membership_request=request_rfi_sent,
                actor_username="reviewer",
                rfi_message="Need more info",
                send_rfi_email=False,
                application_url="https://example.test/app",
            )
            resubmit_membership_request(
                membership_request=request_rfi_replied,
                actor_username="alice",
                updated_responses=[{"Question": "Answer"}],
            )

        for signal_name, send_mock, request_obj, actor in [
            ("membership_request_submitted", submitted_send, request_submitted, "alice"),
            ("membership_request_approved", approved_send, request_approved, "reviewer"),
            ("membership_request_rejected", rejected_send, request_rejected, "reviewer"),
            ("membership_request_rescinded", rescinded_send, request_rescinded, "alice"),
            ("membership_rfi_sent", rfi_sent_send, request_rfi_sent, "reviewer"),
            ("membership_rfi_replied", rfi_replied_send, request_rfi_replied, "alice"),
        ]:
            with self.subTest(signal=signal_name):
                send_mock.assert_called_once()
                kwargs = send_mock.call_args.kwargs
                self.assertEqual(kwargs.get("sender"), MembershipRequest)
                self.assertEqual(kwargs.get("membership_request").id, request_obj.id)
                self.assertEqual(kwargs.get("actor"), actor)

    def test_org_target_workflow_signals_emit_expected_kwargs(self) -> None:
        signal_module = importlib.import_module("core.signals")

        request_submitted = self._make_org_request()
        request_approved = self._make_org_request()
        request_rejected = self._make_org_request()
        request_rescinded = self._make_org_request()
        request_rfi_sent = self._make_org_request()
        request_rfi_replied = self._make_org_request(status=MembershipRequest.Status.on_hold)

        with (
            patch.object(signal_module.organization_membership_request_submitted, "send", autospec=True) as submitted_send,
            patch.object(signal_module.organization_membership_request_approved, "send", autospec=True) as approved_send,
            patch.object(signal_module.organization_membership_request_rejected, "send", autospec=True) as rejected_send,
            patch.object(signal_module.organization_membership_request_rescinded, "send", autospec=True) as rescinded_send,
            patch.object(signal_module.organization_membership_rfi_sent, "send", autospec=True) as rfi_sent_send,
            patch.object(signal_module.organization_membership_rfi_replied, "send", autospec=True) as rfi_replied_send,
            self.captureOnCommitCallbacks(execute=True),
        ):
            record_membership_request_created(
                membership_request=request_submitted,
                actor_username="alice",
                send_submitted_email=False,
            )
            approve_membership_request(
                membership_request=request_approved,
                actor_username="reviewer",
                send_approved_email=False,
            )
            reject_membership_request(
                membership_request=request_rejected,
                actor_username="reviewer",
                rejection_reason="No",
                send_rejected_email=False,
            )
            rescind_membership_request(
                membership_request=request_rescinded,
                actor_username="alice",
            )
            put_membership_request_on_hold(
                membership_request=request_rfi_sent,
                actor_username="reviewer",
                rfi_message="Need more info",
                send_rfi_email=False,
                application_url="https://example.test/app",
            )
            resubmit_membership_request(
                membership_request=request_rfi_replied,
                actor_username="alice",
                updated_responses=[{"Question": "Answer"}],
            )

        for send_mock, request_obj, actor in [
            (submitted_send, request_submitted, "alice"),
            (approved_send, request_approved, "reviewer"),
            (rejected_send, request_rejected, "reviewer"),
            (rescinded_send, request_rescinded, "alice"),
            (rfi_sent_send, request_rfi_sent, "reviewer"),
            (rfi_replied_send, request_rfi_replied, "alice"),
        ]:
            with self.subTest(signal=str(send_mock)):
                send_mock.assert_called_once()
                kwargs = send_mock.call_args.kwargs
                self.assertEqual(kwargs.get("sender"), MembershipRequest)
                self.assertEqual(kwargs.get("membership_request").id, request_obj.id)
                self.assertEqual(kwargs.get("actor"), actor)
                self.assertEqual(kwargs.get("organization_id"), request_obj.requested_organization_id)
                self.assertEqual(kwargs.get("organization_display_name"), request_obj.organization_display_name)

    def test_org_target_disambiguation_uses_org_signal_only(self) -> None:
        signal_module = importlib.import_module("core.signals")
        org_request = self._make_org_request()

        with (
            patch.object(signal_module.membership_request_approved, "send", autospec=True) as user_send,
            patch.object(signal_module.organization_membership_request_approved, "send", autospec=True) as org_send,
            self.captureOnCommitCallbacks(execute=True),
        ):
            approve_membership_request(
                membership_request=org_request,
                actor_username="reviewer",
                send_approved_email=False,
            )

        org_send.assert_called_once()
        user_send.assert_not_called()

    def test_user_target_disambiguation_uses_user_signal_only(self) -> None:
        signal_module = importlib.import_module("core.signals")
        user_request = self._make_user_request(username="alice-disambiguation")

        with (
            patch("core.membership_request_workflow.missing_required_agreements_for_user_in_group", return_value=[]),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=self._user_target_obj()),
            patch.object(signal_module.membership_request_approved, "send", autospec=True) as user_send,
            patch.object(signal_module.organization_membership_request_approved, "send", autospec=True) as org_send,
            self.captureOnCommitCallbacks(execute=True),
        ):
            approve_membership_request(
                membership_request=user_request,
                actor_username="reviewer",
                send_approved_email=False,
            )

        user_send.assert_called_once()
        org_send.assert_not_called()

    def test_on_commit_signal_does_not_fire_before_commit_for_approve(self) -> None:
        signal_module = importlib.import_module("core.signals")
        membership_request = self._make_user_request(username="alice-on-commit")

        with (
            patch("core.membership_request_workflow.missing_required_agreements_for_user_in_group", return_value=[]),
            patch("core.membership_request_workflow.FreeIPAUser.get", return_value=self._user_target_obj()),
            patch.object(signal_module.membership_request_approved, "send", autospec=True) as send_mock,
        ):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                approve_membership_request(
                    membership_request=membership_request,
                    actor_username="reviewer",
                    send_approved_email=False,
                )

            self.assertFalse(send_mock.called)
            for callback in callbacks:
                callback()

        send_mock.assert_called_once()


class Phase1ManagementCommandSignalTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_email_templates()

    def test_membership_expiring_soon_signal_not_emitted_when_no_memberships_are_expiring(self) -> None:
        signal_module = importlib.import_module("core.signals")
        with (
            patch("core.management.commands.membership_expiration_notifications.get_expiring_memberships", return_value=[]),
            patch.object(signal_module.membership_expiring_soon, "send", autospec=True) as send_mock,
        ):
            call_command("membership_expiration_notifications")

        send_mock.assert_not_called()

    def test_membership_expired_signal_not_emitted_when_no_memberships_are_expired(self) -> None:
        signal_module = importlib.import_module("core.signals")
        with patch.object(signal_module.membership_expired, "send", autospec=True) as send_mock:
            call_command("membership_expired_cleanup")

        send_mock.assert_not_called()


class Phase1OrganizationSignalTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _organization_create_payload(self, *, name: str) -> dict[str, str]:
        return {
            "name": name,
            "country_code": "US",
            "business_contact_name": "Biz",
            "business_contact_email": "biz@example.com",
            "business_contact_phone": "",
            "pr_marketing_contact_name": "PR",
            "pr_marketing_contact_email": "pr@example.com",
            "pr_marketing_contact_phone": "",
            "technical_contact_name": "Tech",
            "technical_contact_email": "tech@example.com",
            "technical_contact_phone": "",
            "website_logo": "https://example.com/logo",
            "website": "https://example.com/",
        }

    def test_organization_claimed_signal_emitted_with_expected_kwargs(self) -> None:
        organization = Organization.objects.create(name="Claimable")
        token = make_organization_claim_token(organization)

        self._login_as_freeipa_user("alice")

        signal_module = importlib.import_module("core.signals")
        with (
            patch("core.views_organizations.block_action_without_coc", return_value=None),
            patch("core.views_organizations.block_action_without_country_code", return_value=None),
            patch("core.views_organizations.FreeIPAUser.get", return_value=Mock(username="alice", _user_data={})),
            patch.object(signal_module.organization_claimed, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(reverse("organization-claim", args=[token]), follow=False)

        self.assertEqual(response.status_code, 302)
        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), Organization)
        self.assertEqual(kwargs.get("actor"), "alice")
        self.assertEqual(kwargs.get("organization").id, organization.id)

    def test_organization_created_signal_emitted_with_expected_kwargs(self) -> None:
        self._login_as_freeipa_user("alice")
        signal_module = importlib.import_module("core.signals")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=Mock(username="alice", _user_data={})),
            patch("core.views_utils.has_signed_coc", return_value=True),
            patch.object(signal_module.organization_created, "send", autospec=True) as send_mock,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                reverse("organization-create"),
                data=self._organization_create_payload(name="Created Org"),
                follow=False,
            )

        self.assertEqual(response.status_code, 302)
        created = Organization.objects.get(name="Created Org")
        send_mock.assert_called_once()
        kwargs = send_mock.call_args.kwargs
        self.assertEqual(kwargs.get("sender"), Organization)
        self.assertEqual(kwargs.get("actor"), "alice")
        self.assertEqual(kwargs.get("organization").id, created.id)


@override_settings(DEBUG=True)
class Phase1SignalDebugInspectorTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        from config import urls as config_urls

        clear_url_caches()
        importlib.reload(config_urls)
        self.superuser = Mock(
            is_authenticated=True,
            is_superuser=True,
            get_username=lambda: "admin",
        )
        self.user = Mock(
            is_authenticated=True,
            is_superuser=False,
            get_username=lambda: "viewer",
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_log_endpoint_superuser_returns_200(self) -> None:
        self._login_as_freeipa_user("admin")
        with patch("core.middleware.FreeIPAUser.get", return_value=self.superuser):
            response = self.client.get("/__debug__/signals/log/")
        self.assertEqual(response.status_code, 200)

    def test_log_endpoint_anonymous_redirects(self) -> None:
        response = self.client.get("/__debug__/signals/log/")
        self.assertEqual(response.status_code, 302)

    def test_log_endpoint_non_superuser_denied(self) -> None:
        self._login_as_freeipa_user("viewer")
        with patch("core.middleware.FreeIPAUser.get", return_value=self.user):
            response = self.client.get("/__debug__/signals/log/")
        self.assertIn(response.status_code, {302, 403})

    def test_log_ring_buffer_shows_recent_emissions(self) -> None:
        self._login_as_freeipa_user("admin")

        signal_module = importlib.import_module("core.signals")
        election = Election.objects.create(
            name="Debug signal election",
            description="",
            start_datetime=timezone.now() - datetime.timedelta(days=1),
            end_datetime=timezone.now() + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        signal_module.election_opened.send(sender=Election, election=election, actor="admin")

        with patch("core.middleware.FreeIPAUser.get", return_value=self.superuser):
            response = self.client.get("/__debug__/signals/log/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "election_opened")

    def test_send_endpoint_known_event_returns_200_json(self) -> None:
        self._login_as_freeipa_user("admin")
        with patch("core.middleware.FreeIPAUser.get", return_value=self.superuser):
            response = self.client.post("/__debug__/signals/send/", data={"event_key": "election_opened"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("event_key"), "election_opened")

    def test_send_endpoint_unknown_event_returns_400_json(self) -> None:
        self._login_as_freeipa_user("admin")
        with patch("core.middleware.FreeIPAUser.get", return_value=self.superuser):
            response = self.client.post("/__debug__/signals/send/", data={"event_key": "not_a_real_event"})
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("error", payload)

    def test_signals_send_debug_view_anonymous_denied(self) -> None:
        response = self.client.post("/__debug__/signals/send/", data={"event_key": "election_opened"})
        self.assertIn(response.status_code, {302, 403})

    def test_signals_send_debug_view_non_superuser_denied(self) -> None:
        self._login_as_freeipa_user("viewer")
        with patch("core.middleware.FreeIPAUser.get", return_value=self.user):
            response = self.client.post("/__debug__/signals/send/", data={"event_key": "election_opened"})
        self.assertIn(response.status_code, {302, 403})

    def test_send_endpoint_kwargs_json_merged_and_debug_forced_true(self) -> None:
        self._login_as_freeipa_user("admin")
        signal_module = importlib.import_module("core.signals")
        with patch("core.middleware.FreeIPAUser.get", return_value=self.superuser):
            with patch.object(signal_module.election_opened, "send", autospec=True) as mock_send:
                response = self.client.post(
                    "/__debug__/signals/send/",
                    data={
                        "event_key": "election_opened",
                        "kwargs_json": '{"actor": "override", "extra": "value", "debug": false}',
                    },
                )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("event_key"), "election_opened")
        self.assertIn("extra", payload.get("kwargs", []))

        mock_send.assert_called_once()
        _, call_kwargs = mock_send.call_args
        self.assertEqual(call_kwargs.get("actor"), "override")
        self.assertEqual(call_kwargs.get("event_key"), "election_opened")
        self.assertTrue(call_kwargs.get("debug"))
        self.assertEqual(call_kwargs.get("extra"), "value")

    def test_send_endpoint_kwargs_json_invalid_json_returns_400(self) -> None:
        self._login_as_freeipa_user("admin")
        with patch("core.middleware.FreeIPAUser.get", return_value=self.superuser):
            response = self.client.post(
                "/__debug__/signals/send/",
                data={
                    "event_key": "election_opened",
                    "kwargs_json": "{not-valid-json}",
                },
            )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("error", payload)

    def test_send_endpoint_kwargs_json_requires_object(self) -> None:
        self._login_as_freeipa_user("admin")
        with patch("core.middleware.FreeIPAUser.get", return_value=self.superuser):
            response = self.client.post(
                "/__debug__/signals/send/",
                data={
                    "event_key": "election_opened",
                    "kwargs_json": "[]",
                },
            )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload.get("error"), "kwargs_json must be a JSON object.")
