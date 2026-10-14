from typing import override

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from core.tests.utils_test_data import migrate_to_latest

EXPECTED_DUES_BY_CODE = {
    "individual": 0,
    "mirror": 0,
    "silver": 2500,
    "ruby": 5000,
    "gold": 20000,
    "platinum": 100000,
}


class MembershipTypeAnnualDuesMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0100_election_lifecycle_automation")]
    migrate_to = [("core", "0101_membershiptype_annual_dues_usd")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        old_apps = executor.loader.project_state(self.migrate_from).apps
        # TransactionTestCase flushes migration-seeded rows between tests, so
        # recreate the tiers as migration 0020 left them.
        category, _ = old_apps.get_model("core", "MembershipTypeCategory").objects.get_or_create(
            name="sponsorship",
            defaults={"is_organization": True},
        )
        descriptions_by_code = {
            "individual": "Individual member (no dues)",
            "mirror": "",
            "silver": "Silver Sponsor Member (Annual dues: $2,500 USD)",
            "ruby": "Ruby Sponsor Member (Annual dues: $5,000 USD)",
            # Admin-edited wording around the dues text must survive the cleanup.
            "gold": "Gold tier (Annual dues: $20,000 USD) with board seat",
            "platinum": "Platinum Sponsor Member (Annual dues: $100,000 USD)",
            "custom": "Custom Sponsor (Annual dues: $7 USD)",
        }
        old_membership_type = old_apps.get_model("core", "MembershipType")
        for code, description in descriptions_by_code.items():
            old_membership_type.objects.update_or_create(
                code=code,
                defaults={"name": code, "description": description, "category": category},
            )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.membership_type = executor.loader.project_state(self.migrate_to).apps.get_model(
            "core", "MembershipType"
        )

    def test_dues_are_populated_per_tier(self) -> None:
        dues_by_code = dict(self.membership_type.objects.values_list("code", "annual_dues_usd"))

        # Unknown tiers are not guessed from their description.
        self.assertEqual(dues_by_code, {**EXPECTED_DUES_BY_CODE, "custom": 0})

    def test_dues_text_is_stripped_from_descriptions(self) -> None:
        descriptions = dict(self.membership_type.objects.values_list("code", "description"))

        self.assertEqual(
            descriptions,
            {
                "individual": "Individual member (no dues)",
                "mirror": "",
                "silver": "Silver Sponsor Member",
                "ruby": "Ruby Sponsor Member",
                "gold": "Gold tier with board seat",
                "platinum": "Platinum Sponsor Member",
                "custom": "Custom Sponsor",
            },
        )
