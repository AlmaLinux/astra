
import datetime

from django.test import TestCase, override_settings
from django.utils import timezone

from core.elections_eligibility import eligible_voters_from_memberships
from core.models import Election, Membership, MembershipType, Organization
from core.tests.utils_test_data import FAR_FUTURE, ensure_core_categories


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=90)
class ElectionEligibilityOrganizationRepresentativesTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def test_org_representatives_inherit_org_sponsorship_for_eligibility(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Eligibility election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        org_type = MembershipType.objects.create(
            code="org",
            name="Org",
            votes=5,
            category_id="sponsorship",
            enabled=True,
        )

        org = Organization.objects.create(
            name="Acme",
            representative="rep1",
        )

        Membership.objects.create(
            target_organization=org,
            membership_type=org_type,
            created_at=election.start_datetime - datetime.timedelta(days=120),
            expires_at=FAR_FUTURE,
        )

        eligible = eligible_voters_from_memberships(election=election)
        eligible_by_username = {v.username: v.weight for v in eligible}

        self.assertIn("rep1", eligible_by_username)
        self.assertEqual(eligible_by_username["rep1"], 5)

    def test_org_sponsorship_only_applies_to_single_representative(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Eligibility election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        org_type = MembershipType.objects.create(
            code="org",
            name="Org",
            votes=5,
            category_id="sponsorship",
            enabled=True,
        )

        org = Organization.objects.create(
            name="Acme",
            representative="rep1",
        )

        Membership.objects.create(
            target_organization=org,
            membership_type=org_type,
            created_at=election.start_datetime - datetime.timedelta(days=120),
            expires_at=FAR_FUTURE,
        )

        eligible = eligible_voters_from_memberships(election=election)
        eligible_by_username = {v.username: v.weight for v in eligible}

        self.assertIn("rep1", eligible_by_username)
        self.assertEqual(eligible_by_username["rep1"], 5)
        self.assertNotIn("rep2", eligible_by_username)

    def test_org_membership_with_non_organization_category_does_not_add_vote_weight(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Eligibility election",
            description="",
            start_datetime=now,
            end_datetime=now + datetime.timedelta(days=7),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        misclassified_type = MembershipType.objects.create(
            code="individual-misclassified-org",
            name="Individual Misclassified",
            votes=5,
            category_id="individual",
            enabled=True,
        )

        org = Organization.objects.create(
            name="Acme",
            representative="rep1",
        )

        membership = Membership.objects.create(
            target_organization=org,
            membership_type=misclassified_type,
            expires_at=FAR_FUTURE,
        )
        Membership.objects.filter(pk=membership.pk).update(
            created_at=election.start_datetime - datetime.timedelta(days=120)
        )

        eligible = eligible_voters_from_memberships(election=election)
        eligible_by_username = {v.username: v.weight for v in eligible}

        self.assertNotIn("rep1", eligible_by_username)
