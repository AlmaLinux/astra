from unittest.mock import patch

from django.test import TestCase

from core.agreements import list_agreements_for_user, missing_required_agreements_for_user_in_group
from core.freeipa.agreement import FreeIPAFASAgreement


class AgreementsPerformanceTests(TestCase):
    def test_list_agreements_for_user_uses_bulk_data_without_per_agreement_get(self) -> None:
        agreement = FreeIPAFASAgreement(
            "cla",
            {
                "cn": ["cla"],
                "description": ["Contributor agreement"],
                "ipaenabledflag": [True],
                "member_group": ["packagers"],
                "memberuser_user": ["alice"],
            },
        )

        with (
            patch("core.agreements.FreeIPAFASAgreement.all", return_value=[agreement]),
            patch(
                "core.agreements.FreeIPAFASAgreement.get",
                side_effect=AssertionError("per-agreement FreeIPA get() should not be called"),
            ),
        ):
            agreements = list_agreements_for_user(
                "alice",
                user_groups=["packagers"],
            )

        self.assertEqual([item.cn for item in agreements], ["cla"])
        self.assertTrue(agreements[0].signed)

    def test_missing_required_agreements_uses_bulk_data_without_get_per_cn(self) -> None:
        agreements = [
            FreeIPAFASAgreement(
                "cla",
                {
                    "cn": ["cla"],
                    "description": ["Contributor agreement"],
                    "ipaenabledflag": [True],
                    "member_group": ["packagers"],
                    "memberuser_user": ["alice"],
                },
            ),
            FreeIPAFASAgreement(
                "nda",
                {
                    "cn": ["nda"],
                    "description": ["NDA"],
                    "ipaenabledflag": [True],
                    "member_group": ["packagers"],
                },
            ),
        ]

        with (
            patch("core.agreements.FreeIPAFASAgreement.all", return_value=agreements),
            patch(
                "core.agreements.FreeIPAFASAgreement.get",
                side_effect=AssertionError("per-agreement FreeIPA get() should not be called"),
            ),
        ):
            missing = missing_required_agreements_for_user_in_group("alice", "packagers")

        self.assertEqual(missing, ["nda"])