
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from post_office.models import EmailTemplate

from core.models import Candidate, Election, FreeIPAPermissionGrant, Membership, MembershipType
from core.permissions import ASTRA_ADD_ELECTION


class ElectionEditEmailSaveModeTests(TestCase):
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

    def _make_eligible(self, *, election: Election, username: str) -> None:
        mt = MembershipType.objects.create(
            code=f"voter-{username}",
            name="Voter",
            description="",
            category_id="individual",
            sort_order=1,
            enabled=True,
            votes=1,
        )
        m = Membership.objects.create(target_username=username, membership_type=mt, expires_at=None)
        created_at = election.start_datetime - datetime.timedelta(days=200)
        Membership.objects.filter(pk=m.pk).update(created_at=created_at)

    def test_save_draft_keep_existing_does_not_overwrite_email_snapshot(self) -> None:
        now = timezone.now()
        t1 = EmailTemplate.objects.create(
            name="t1",
            subject="Subj 1",
            html_content="<p>HTML 1</p>",
            content="Text 1",
        )
        t2 = EmailTemplate.objects.create(
            name="t2",
            subject="Subj 2",
            html_content="<p>HTML 2</p>",
            content="Text 2",
        )

        election = Election.objects.create(
            name="Draft",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.draft,
            voting_email_template=t1,
            voting_email_subject="Saved subject",
            voting_email_html="Saved html",
            voting_email_text="Saved text",
        )

        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="bob")
        self._make_eligible(election=election, username="alice")
        self._make_eligible(election=election, username="bob")

        self._login_as_freeipa_user("admin")
        self._grant_manage_elections("admin")

        resp = self.client.post(
            reverse("election-edit", args=[election.id]),
            data={
                "action": "save_draft",
                "email_save_mode": "keep_existing",
                "name": "Draft renamed",
                "description": election.description,
                "url": election.url,
                "start_datetime": (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
                "end_datetime": (now + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                "number_of_seats": str(election.number_of_seats),
                "quorum": str(election.quorum),
                # Intentionally changed email fields; should NOT be persisted.
                "email_template_id": str(t2.pk),
                "subject": "New subject",
                "html_content": "New html",
                "text_content": "New text",
                # Candidate formset: include the single existing candidate.
                "candidates-TOTAL_FORMS": "1",
                "candidates-INITIAL_FORMS": "1",
                "candidates-MIN_NUM_FORMS": "0",
                "candidates-MAX_NUM_FORMS": "1000",
                "candidates-0-id": str(Candidate.objects.get(election=election).id),
                "candidates-0-freeipa_username": "alice",
                "candidates-0-nominated_by": "bob",
                "candidates-0-description": "",
                "candidates-0-url": "",
                "candidates-0-DELETE": "",
                # No exclusion groups.
                "groups-TOTAL_FORMS": "0",
                "groups-INITIAL_FORMS": "0",
                "groups-MIN_NUM_FORMS": "0",
                "groups-MAX_NUM_FORMS": "1000",
            },
            follow=False,
        )

        self.assertEqual(resp.status_code, 302)
        election.refresh_from_db()
        self.assertEqual(election.name, "Draft renamed")
        self.assertEqual(election.voting_email_template_id, t1.pk)
        self.assertEqual(election.voting_email_subject, "Saved subject")
        self.assertEqual(election.voting_email_html, "Saved html")
        self.assertEqual(election.voting_email_text, "Saved text")

    def test_save_draft_default_overwrites_email_snapshot(self) -> None:
        now = timezone.now()
        t1 = EmailTemplate.objects.create(
            name="t1b",
            subject="Subj 1",
            html_content="<p>HTML 1</p>",
            content="Text 1",
        )

        election = Election.objects.create(
            name="Draft",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=2),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="bob")
        self._make_eligible(election=election, username="alice")
        self._make_eligible(election=election, username="bob")

        self._login_as_freeipa_user("admin")
        self._grant_manage_elections("admin")

        resp = self.client.post(
            reverse("election-edit", args=[election.id]),
            data={
                "action": "save_draft",
                "name": election.name,
                "description": election.description,
                "url": election.url,
                "start_datetime": (now + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
                "end_datetime": (now + datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                "number_of_seats": str(election.number_of_seats),
                "quorum": str(election.quorum),
                "email_template_id": str(t1.pk),
                "subject": "New subject",
                "html_content": "New html",
                "text_content": "New text",
                # Candidate formset: include the single existing candidate.
                "candidates-TOTAL_FORMS": "1",
                "candidates-INITIAL_FORMS": "1",
                "candidates-MIN_NUM_FORMS": "0",
                "candidates-MAX_NUM_FORMS": "1000",
                "candidates-0-id": str(Candidate.objects.get(election=election).id),
                "candidates-0-freeipa_username": "alice",
                "candidates-0-nominated_by": "bob",
                "candidates-0-description": "",
                "candidates-0-url": "",
                "candidates-0-DELETE": "",
                # No exclusion groups.
                "groups-TOTAL_FORMS": "0",
                "groups-INITIAL_FORMS": "0",
                "groups-MIN_NUM_FORMS": "0",
                "groups-MAX_NUM_FORMS": "1000",
            },
            follow=False,
        )

        self.assertEqual(resp.status_code, 302)
        election.refresh_from_db()
        self.assertEqual(election.voting_email_template_id, t1.pk)
        self.assertEqual(election.voting_email_subject, "New subject")
        self.assertEqual(election.voting_email_html, "New html")
        self.assertEqual(election.voting_email_text, "New text")
