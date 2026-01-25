from __future__ import annotations

import datetime
import json
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from post_office.models import EmailTemplate

from core.backends import FreeIPAUser
from core.models import Candidate, Election, FreeIPAPermissionGrant, Membership, MembershipType, VotingCredential
from core.permissions import ASTRA_ADD_ELECTION


class ElectionDetailAdminControlsTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_vote_button_shows_email_hint(self) -> None:
        self._login_as_freeipa_user("viewer")

        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )
        membership = Membership.objects.create(target_username="viewer", membership_type=voter_type, expires_at=None)
        Membership.objects.filter(pk=membership.pk).update(created_at=election.start_datetime - datetime.timedelta(days=1))

        VotingCredential.objects.create(
            election=election,
            public_id="cred-viewer",
            freeipa_username="viewer",
            weight=1,
        )

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "viewer@example.com")

    def test_shows_eligible_voters_and_allows_resend(self) -> None:
        self._login_as_freeipa_user("viewer")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Admin election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
        )
        membership = Membership.objects.create(target_username="alice", membership_type=voter_type, expires_at=None)
        Membership.objects.filter(pk=membership.pk).update(created_at=election.start_datetime - datetime.timedelta(days=200))

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "displayname": ["Alice User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "nominator":
                return FreeIPAUser("nominator", {"uid": ["nominator"], "displayname": ["Nom"], "memberof_group": []})
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Eligible voters")
        self.assertContains(resp, ">alice<")
        self.assertContains(resp, "Resend voting credential")

    def test_resend_all_credentials_opens_send_mail_with_voting_credential_context(self) -> None:
        self._login_as_freeipa_user("viewer")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        EmailTemplate.objects.create(
            name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
            subject="Subject",
            html_content="HTML",
            content="Text",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Reminder election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-alice",
            freeipa_username="alice",
            weight=1,
        )

        def _get_user(username: str):
            if username == "alice":
                return FreeIPAUser(
                    "alice",
                    {
                        "uid": ["alice"],
                        "displayname": ["Alice User"],
                        "mail": ["alice@example.com"],
                        "memberof_group": [],
                    },
                )
            return FreeIPAUser(username, {"uid": [username], "mail": [f"{username}@example.com"], "memberof_group": []})

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.post(reverse("election-send-mail-credentials", args=[election.id]))

        self.assertEqual(resp.status_code, 302)
        location = str(resp["Location"])
        self.assertIn(reverse("send-mail"), location)
        self.assertIn("template=", location)
        self.assertIn(settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME, location)
        self.assertIn("type=csv", location)

        raw_csv_payload = self.client.session.get("send_mail_csv_payload_v1")
        self.assertTrue(raw_csv_payload)
        payload = json.loads(str(raw_csv_payload))
        self.assertEqual(payload["recipients"][0]["credential_public_id"], "cred-alice")

    def test_resend_single_credential_opens_send_mail_for_one_user(self) -> None:
        self._login_as_freeipa_user("viewer")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        EmailTemplate.objects.create(
            name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
            subject="Subject",
            html_content="HTML",
            content="Text",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Reminder election (single)",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        VotingCredential.objects.create(
            election=election,
            public_id="cred-alice",
            freeipa_username="alice",
            weight=1,
        )

        VotingCredential.objects.create(
            election=election,
            public_id="cred-bob",
            freeipa_username="bob",
            weight=1,
        )

        def _get_user(username: str):
            return FreeIPAUser(username, {"uid": [username], "mail": [f"{username}@example.com"], "memberof_group": []})

        with patch("core.backends.FreeIPAUser.get", side_effect=_get_user):
            resp = self.client.post(
                reverse("election-send-mail-credentials", args=[election.id]),
                data={"username": "alice"},
            )

        self.assertEqual(resp.status_code, 302)

        raw_csv_payload = self.client.session.get("send_mail_csv_payload_v1")
        self.assertTrue(raw_csv_payload)
        payload = json.loads(str(raw_csv_payload))
        self.assertEqual(len(payload["recipients"]), 1)
        self.assertEqual(payload["recipients"][0]["username"], "alice")
        self.assertEqual(payload["recipients"][0]["credential_public_id"], "cred-alice")

    def test_conclude_requires_confirmation(self) -> None:
        self._login_as_freeipa_user("viewer")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Confirm election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        url = reverse("election-conclude", args=[election.id])

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "mail": ["viewer@example.com"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp1 = self.client.post(url, data={"skip_tally": "1"})
        self.assertEqual(resp1.status_code, 400)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.open)

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp2 = self.client.post(url, data={"skip_tally": "1", "confirm": election.name})
        self.assertEqual(resp2.status_code, 302)
        election.refresh_from_db()
        self.assertEqual(election.status, Election.Status.closed)

    def test_extend_requires_confirmation(self) -> None:
        self._login_as_freeipa_user("viewer")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Extend confirm election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        url = reverse("election-extend-end", args=[election.id])
        original_end = election.end_datetime
        new_end = election.end_datetime + datetime.timedelta(days=1)
        new_end_local = new_end.strftime("%Y-%m-%dT%H:%M")

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "mail": ["viewer@example.com"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp1 = self.client.post(url, data={"end_datetime": new_end_local})
        self.assertEqual(resp1.status_code, 400)
        election.refresh_from_db()
        self.assertEqual(election.end_datetime, original_end)

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp2 = self.client.post(url, data={"end_datetime": new_end_local, "confirm": election.name})
        self.assertEqual(resp2.status_code, 302)
        election.refresh_from_db()
        self.assertGreater(election.end_datetime, original_end)

    def test_does_not_show_resend_buttons_when_not_open(self) -> None:
        self._login_as_freeipa_user("viewer")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Closed election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("election-detail", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Resend all credentials")
        self.assertNotContains(resp, "Resend voting credential")

    def test_resend_uses_existing_credentials_when_present(self) -> None:
        self._login_as_freeipa_user("viewer")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Resend existing credentials election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )
        membership = Membership.objects.create(target_username="alice", membership_type=voter_type, expires_at=None)
        Membership.objects.filter(pk=membership.pk).update(created_at=election.start_datetime - datetime.timedelta(days=200))

        VotingCredential.objects.create(
            election=election,
            public_id="cred-alice-existing",
            freeipa_username="alice",
            weight=5,
        )

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "displayname": ["Alice User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "viewer":
                return viewer
            if username == "alice":
                return alice
            if username == "nominator":
                return FreeIPAUser("nominator", {"uid": ["nominator"], "displayname": ["Nom"], "memberof_group": []})
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch(
                "core.views_elections.issue_voting_credentials_from_memberships_detailed",
                side_effect=AssertionError("should not bulk issue"),
            ),
        ):
            resp = self.client.post(reverse("election-send-mail-credentials", args=[election.id]), data={"username": "alice"})

        self.assertEqual(resp.status_code, 302)

        raw_csv_payload = self.client.session.get("send_mail_csv_payload_v1")
        self.assertTrue(raw_csv_payload)
        payload = json.loads(str(raw_csv_payload))
        self.assertEqual(payload["recipients"][0]["credential_public_id"], "cred-alice-existing")


class ElectionVoteNoJsFallbackTests(TestCase):
    def test_vote_submit_accepts_username_ranking(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Vote fallback election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        c2 = Candidate.objects.create(
            election=election,
            freeipa_username="bob",
            nominated_by="nominator",
        )

        cred = VotingCredential.objects.create(
            election=election,
            public_id="cred-1",
            freeipa_username="voter1",
            weight=1,
        )

        voter_type = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )
        membership = Membership.objects.create(target_username="voter1", membership_type=voter_type, expires_at=None)
        Membership.objects.filter(pk=membership.pk).update(created_at=election.start_datetime - datetime.timedelta(days=1))

        session = self.client.session
        session["_freeipa_username"] = "voter1"
        session.save()

        voter1 = FreeIPAUser("voter1", {"uid": ["voter1"], "memberof_group": []})

        with patch("core.backends.FreeIPAUser.get", return_value=voter1):
            resp = self.client.post(
                reverse("election-vote-submit", args=[election.id]),
                data={
                    "credential_public_id": cred.public_id,
                    "ranking_usernames": f"{c1.freeipa_username},{c2.freeipa_username}",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))


class ElectionEditEmailTemplateActionsTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _grant_election_manage(self, username: str) -> None:
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=username,
        )

    def test_election_template_json_save_and_save_as(self) -> None:
        self._login_as_freeipa_user("viewer")
        self._grant_election_manage("viewer")

        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "memberof_group": [],
            },
        )

        tpl = EmailTemplate.objects.create(
            name="Election Template",
            subject="Old subject",
            html_content="<p>Old</p>",
            content="Old text",
        )

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            json_resp = self.client.get(
                reverse(
                    "email-template-json",
                    args=[tpl.pk],
                )
            )
        self.assertEqual(json_resp.status_code, 200)
        self.assertEqual(json_resp.json().get("id"), tpl.pk)
        self.assertEqual(json_resp.json().get("subject"), "Old subject")

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            save_resp = self.client.post(
                reverse(
                    "email-template-save",
                ),
                data={
                    "email_template_id": str(tpl.pk),
                    "subject": "New subject",
                    "html_content": "<p>New</p>",
                    "text_content": "New text",
                },
            )
        self.assertEqual(save_resp.status_code, 200)

        tpl.refresh_from_db()
        self.assertEqual(tpl.subject, "New subject")
        self.assertEqual(tpl.html_content, "<p>New</p>")
        self.assertEqual(tpl.content, "New text")

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            save_as_resp = self.client.post(
                reverse(
                    "email-template-save-as",
                ),
                data={
                    "name": "New Election Template",
                    "subject": "S",
                    "html_content": "<p>H</p>",
                    "text_content": "T",
                },
            )
        self.assertEqual(save_as_resp.status_code, 200)
        new_id = save_as_resp.json().get("id")
        self.assertTrue(EmailTemplate.objects.filter(pk=new_id, name="New Election Template").exists())


class UnifiedEmailPreviewTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_election_preview_endpoint_renders(self) -> None:
        self._login_as_freeipa_user("editor")
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_ELECTION,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="editor",
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Preview election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        editor = FreeIPAUser(
            "editor",
            {
                "uid": ["editor"],
                "mail": ["editor@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.backends.FreeIPAUser.get", return_value=editor):
            resp = self.client.post(
                reverse("election-email-render-preview", args=[election.id]),
                data={
                    "subject": "Hello {{ username }}",
                    "html_content": "<p>{{ election_name }}</p>",
                    "text_content": "{{ election_name }}",
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("Preview election", payload.get("text", ""))
