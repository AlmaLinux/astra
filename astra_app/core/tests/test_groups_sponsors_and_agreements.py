
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import TestCase

from core import views_users


class GroupsSponsorsAndAgreementsTests(TestCase):
    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        return request

    def test_profile_groups_include_role_with_sponsor_precedence(self):
        fu = SimpleNamespace(
            username="alice",
            is_authenticated=True,
            get_username=lambda: "alice",
            groups_list=["g1", "g2"],
            _user_data={},
            email="a@example.org",
            get_full_name=lambda: "Alice User",
        )

        g1 = SimpleNamespace(cn="g1", fas_group=True, members=["alice"], sponsors=[])
        g2 = SimpleNamespace(cn="g2", fas_group=True, members=["alice"], sponsors=["alice"])

        with patch("core.views_users.FreeIPAGroup.all", autospec=True, return_value=[g1, g2]):
            with patch("core.views_users.has_enabled_agreements", autospec=True, return_value=False):
                ctx = views_users._profile_context_for_user(
                    request=SimpleNamespace(),
                    fu=fu,
                    is_self=True,
                    viewer_is_membership_committee=False,
                )

        groups = ctx["groups"]
        self.assertEqual([g["cn"] for g in groups], ["g1", "g2"])
        roles = {g["cn"]: g["role"] for g in groups}
        self.assertEqual(roles["g1"], "Member")
        self.assertEqual(roles["g2"], "Sponsor")
