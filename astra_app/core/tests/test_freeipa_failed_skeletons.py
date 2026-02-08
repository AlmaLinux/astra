
from django.test import SimpleTestCase

from core.backends import FreeIPAOperationFailed, _raise_if_freeipa_failed


class FreeIPAFailedSkeletonTests(SimpleTestCase):
    def test_fasagreement_add_group_failed_skeleton_is_not_error(self):
        # FreeIPA often returns a `failed` skeleton even when the operation succeeded.
        # For fasagreement_add_group, we've observed:
        #   failed={'member': {'group': []}}
        # which should not be treated as an error.
        res = {"failed": {"member": {"group": []}}}
        _raise_if_freeipa_failed(res, action="fasagreement_add_group", subject="agreement=a group=g")

    def test_fasagreement_add_group_nonempty_failed_bucket_is_error(self):
        res = {"failed": {"member": {"group": ["someerror"]}}}
        with self.assertRaises(FreeIPAOperationFailed):
            _raise_if_freeipa_failed(res, action="fasagreement_add_group", subject="agreement=a group=g")

    def test_fasagreement_add_user_already_member_is_not_error(self):
        res = {"failed": {"memberuser": {"user": ["This entry is already a member"]}}}
        _raise_if_freeipa_failed(res, action="fasagreement_add_user", subject="agreement=a user=u")

    def test_fasagreement_add_user_unexpected_message_is_error(self):
        res = {"failed": {"memberuser": {"user": ["someerror"]}}}
        with self.assertRaises(FreeIPAOperationFailed):
            _raise_if_freeipa_failed(res, action="fasagreement_add_user", subject="agreement=a user=u")
