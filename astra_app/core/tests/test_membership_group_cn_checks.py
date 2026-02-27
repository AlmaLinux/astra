from django.core import checks
from django.test import TestCase

from core.models import MembershipType
from core.tests.utils_test_data import ensure_core_categories


class MembershipTypeGroupCNChecksTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def test_check_warns_for_enabled_membership_type_without_group_cn(self) -> None:
        MembershipType.objects.update_or_create(
            code="missing_group_cn",
            defaults={
                "name": "Missing Group",
                "group_cn": "",
                "category_id": "individual",
                "sort_order": 999,
                "enabled": True,
            },
        )

        issues = checks.run_checks()

        self.assertTrue(
            any(
                issue.id == "core.W001"
                and issue.obj == "missing_group_cn"
                for issue in issues
            )
        )

    def test_check_warns_for_enabled_membership_type_with_blank_group_cn(self) -> None:
        MembershipType.objects.update_or_create(
            code="missing_group_cn_null",
            defaults={
                "name": "Missing Group Null",
                "group_cn": "",
                "category_id": "individual",
                "sort_order": 998,
                "enabled": True,
            },
        )

        issues = checks.run_checks()

        self.assertTrue(
            any(
                issue.id == "core.W001"
                and issue.obj == "missing_group_cn_null"
                for issue in issues
            )
        )
