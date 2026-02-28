import datetime

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from core.admin import CandidateAdmin
from core.models import Candidate, Election


class CandidateDeleteProtectionTests(TestCase):
    def _create_election(self, *, status: str) -> Election:
        now = timezone.now()
        return Election.objects.create(
            name=f"Election ({status})",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=status,
        )

    def test_candidate_delete_succeeds_for_draft_election(self) -> None:
        election = self._create_election(status=Election.Status.draft)
        candidate = Candidate.objects.create(election=election, freeipa_username="alice", nominated_by="nom")

        candidate.delete()

        self.assertFalse(Candidate.objects.filter(pk=candidate.pk).exists())

    def test_candidate_delete_raises_for_non_draft_election(self) -> None:
        for status in (Election.Status.open, Election.Status.closed, Election.Status.tallied):
            with self.subTest(status=status):
                election = self._create_election(status=status)
                candidate = Candidate.objects.create(election=election, freeipa_username=f"user-{status}", nominated_by="nom")

                with self.assertRaisesMessage(ValidationError, f"Cannot delete a candidate from a {status} election"):
                    candidate.delete()

                self.assertTrue(Candidate.objects.filter(pk=candidate.pk).exists())

    def test_candidate_admin_denies_delete_for_non_draft_object(self) -> None:
        admin_user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="pw",
        )
        request = RequestFactory().get("/")
        request.user = admin_user

        candidate_admin = CandidateAdmin(Candidate, AdminSite())
        draft_candidate = Candidate.objects.create(
            election=self._create_election(status=Election.Status.draft),
            freeipa_username="draft-user",
            nominated_by="nom",
        )
        open_candidate = Candidate.objects.create(
            election=self._create_election(status=Election.Status.open),
            freeipa_username="open-user",
            nominated_by="nom",
        )

        self.assertTrue(candidate_admin.has_delete_permission(request, obj=draft_candidate))
        self.assertFalse(candidate_admin.has_delete_permission(request, obj=open_candidate))
