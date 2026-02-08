
from unittest.mock import patch

from django.test import TestCase
from python_freeipa import exceptions

from core.backends import FreeIPAFASAgreement


class FreeIPAFASAgreementDeleteUnlinksTests(TestCase):
    def test_delete_unlinks_groups_and_users_then_retries(self):
        initial = FreeIPAFASAgreement(
            "test_agreement",
            {
                "cn": ["test_agreement"],
                "member_group": ["g1"],
                "memberuser_user": ["u1"],
            },
        )

        groups_unlinked = False
        users_unlinked = False

        calls: list[str] = []

        def rpc(_client, method: str, args, params):
            calls.append(method)
            if method == "fasagreement_del" and calls.count("fasagreement_del") == 1:
                raise exceptions.Denied(
                    "Insufficient access: Not allowed to delete User Agreement with linked groups",
                    0,
                )
            if method == "fasagreement_remove_group":
                nonlocal groups_unlinked
                groups_unlinked = True
                return {"failed": {"member": {"group": []}}}
            if method == "fasagreement_remove_user":
                nonlocal users_unlinked
                users_unlinked = True
                return {"failed": {"memberuser": {"user": []}}}
            return {}

        def get(_cn: str) -> FreeIPAFASAgreement:
            data = {
                "cn": ["test_agreement"],
                "member_group": [] if groups_unlinked else ["g1"],
                "memberuser_user": [] if users_unlinked else ["u1"],
            }
            return FreeIPAFASAgreement("test_agreement", data)

        def retry(_get_client, fn):
            return fn(object())

        with (
            patch("core.backends._with_freeipa_service_client_retry", side_effect=retry),
            patch.object(FreeIPAFASAgreement, "_rpc", side_effect=rpc),
            patch.object(FreeIPAFASAgreement, "get", side_effect=get),
        ):
            initial.delete()

        self.assertEqual(
            calls,
            [
                "fasagreement_del",
                "fasagreement_remove_group",
                "fasagreement_remove_user",
                "fasagreement_del",
            ],
        )
