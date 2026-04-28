from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser


class UserProfileVueTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    @override_settings(
        DJANGO_VITE={
            "default": {
                "dev_mode": True,
                "dev_server_protocol": "http",
                "dev_server_host": "localhost",
                "dev_server_port": 5173,
                "static_url_prefix": "",
            }
        },
    )
    def test_user_profile_page_renders_vue_controller_shell_contract(self) -> None:
        username = "alice"
        self._login_as_freeipa(username)

        with patch(
            "core.views_users._profile_context_for_user",
            side_effect=AssertionError("profile page route must not build server-rendered profile data"),
        ):
            response = self.client.get(f"/user/{username}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-user-profile-root")
        self.assertContains(response, f'data-user-profile-api-url="/api/v1/users/{username}/profile/detail"')
        self.assertContains(response, 'data-user-profile-settings-profile-url="/settings/?tab=profile"')
        self.assertContains(response, 'data-user-profile-membership-history-url-template="/membership/log/__username__/?username=__username__"')
        self.assertContains(response, 'data-user-profile-membership-request-url="/membership/request/"')
        self.assertContains(response, 'data-user-profile-membership-request-detail-url-template="/membership/request/__request_id__/"')
        self.assertContains(
            response,
            'data-user-profile-membership-set-expiry-url-template="/membership/manage/__username__/__membership_type_code__/expiry/"',
        )
        self.assertContains(
            response,
            'data-user-profile-membership-terminate-url-template="/membership/manage/__username__/__membership_type_code__/terminate/"',
        )
        self.assertContains(response, 'data-user-profile-csrf-token="')
        self.assertContains(response, f'data-user-profile-next-url="/user/{username}/"')
        self.assertContains(
            response,
            f'data-user-profile-membership-notes-summary-url="{reverse("api-membership-notes-aggregate-summary")}?target_type=user&amp;target={username}"',
        )
        self.assertContains(
            response,
            f'data-user-profile-membership-notes-detail-url="{reverse("api-membership-notes-aggregate")}?target_type=user&amp;target={username}"',
        )
        self.assertContains(response, f'data-user-profile-membership-notes-add-url="{reverse("api-membership-notes-aggregate-add")}"')
        self.assertContains(response, 'data-user-profile-membership-notes-can-view="false"')
        self.assertContains(response, 'data-user-profile-membership-notes-can-write="false"')
        self.assertContains(response, 'data-user-profile-group-detail-url-template="/group/__group_name__/"')
        self.assertContains(response, 'data-user-profile-agreements-url-template="/settings/?tab=agreements&amp;agreement=__agreement_cn__"')
        self.assertContains(response, 'src="http://localhost:5173/src/entrypoints/userProfile.ts"')
        self.assertNotContains(response, "data-user-profile-summary-root")
        self.assertNotContains(response, "user-profile-summary-bootstrap")
        self.assertNotContains(response, "data-user-profile-groups-root")
        self.assertNotContains(response, "user-profile-groups-bootstrap")

    def test_user_profile_api_returns_vue_profile_payload(self) -> None:
        username = "alice"
        self._login_as_freeipa(username)

        profile_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["Alice"],
                "sn": ["Example"],
                "mail": ["alice@example.test"],
                "timezone": ["UTC"],
                "fasIRCNick": ["aliceirc"],
            },
        )

        with (
            patch("core.views_users._get_full_user", return_value=profile_user),
            patch("core.views_users._is_membership_committee_viewer", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", return_value=({username: "https://avatars.example/alice.png"}, 1, 0)),
        ):
            response = self.client.get(reverse("api-user-profile", args=[username]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["username"], username)
        self.assertEqual(payload["summary"]["fullName"], "Alice Example")
        self.assertEqual(payload["summary"]["email"], "alice@example.test")
        self.assertEqual(payload["summary"]["avatarUrl"], "https://avatars.example/alice.png")
        self.assertNotIn("profileEditUrl", payload["summary"])
        self.assertEqual(payload["groups"]["username"], username)
        self.assertIn("membership", payload)
        self.assertNotIn("historyUrl", payload["membership"])
        self.assertNotIn("requestUrl", payload["membership"])
        self.assertIn("accountSetup", payload)

    def test_user_profile_detail_api_returns_data_only_payload(self) -> None:
        username = "alice"
        self._login_as_freeipa(username)

        membership_type_data = {
            "name": "Individual",
            "code": "individual",
            "description": "Personal membership",
        }

        context = {
            "fu": FreeIPAUser(
                username,
                {
                    "uid": [username],
                    "givenname": ["Alice"],
                    "sn": ["Example"],
                    "mail": ["alice@example.test"],
                    "timezone": ["UTC"],
                },
            ),
            "profile_email": "alice@example.test",
            "viewer_is_membership_committee": True,
            "profile_country": "United States",
            "country_code": "US",
            "pronouns": "she/her",
            "locale": "en_US",
            "timezone_name": "UTC",
            "irc_nicks": ["aliceirc"],
            "social_profiles": [
                {
                    "platform": "x",
                    "label": "X (Twitter)",
                    "title": "X (Twitter) URLs",
                    "icon": "fab fa-x-twitter",
                    "urls": [{"href": "https://x.com/alice", "text": "@alice"}],
                }
            ],
            "social_profile_urls": [
                {
                    "platform": "x",
                    "urls": ["https://x.com/alice"],
                }
            ],
            "website_urls": [
                {"href": "https://example.com", "text": "https://example.com"},
                {"href": None, "text": "plain.example.test/path"},
            ],
            "website_url_values": ["https://example.com", "plain.example.test/path"],
            "rss_urls": [{"href": "https://example.com/feed.xml", "text": "https://example.com/feed.xml"}],
            "rss_url_values": ["https://example.com/feed.xml"],
            "rhbz_email": "",
            "github_username": "alice",
            "gitlab_username": "",
            "gpg_keys": [],
            "ssh_keys": [],
            "is_self": True,
            "groups": [{"cn": "infra", "role": "Sponsor"}],
            "agreements": ["Code of Conduct"],
            "missing_agreements": [{"cn": "FPCA", "required_by": ["infra"]}],
            "account_setup_required_actions": [
                {
                    "id": "country-code-missing-alert",
                    "label": "Add your country",
                    "url_label": "Set country code",
                }
            ],
            "account_setup_required_is_rfi": False,
            "account_setup_recommended_actions": [
                {
                    "id": "membership-request-recommended-alert",
                    "label": "Request membership",
                    "url_label": "Request",
                }
            ],
            "show_membership_card": True,
            "membership_can_request_any": True,
            "memberships": [
                {
                    "membership_type": membership_type_data,
                    "created_at": "2024-01-15T12:00:00+00:00",
                    "expires_at": "2026-04-30T00:00:00+00:00",
                    "is_expiring_soon": True,
                    "has_pending_request_in_category": False,
                    "can_request_tier_change": True,
                    "request_id": 17,
                }
            ],
            "membership_pending_requests": [
                {
                    "membership_type": membership_type_data,
                    "request_id": 23,
                    "status": "on_hold",
                    "organization_name": "Acme Org",
                }
            ],
            "current_time": None,
        }

        with (
            patch("core.views_users._profile_context_for_request", return_value=context),
            patch(
                "core.views_users.membership_review_permissions",
                return_value={
                    "membership_can_view": True,
                    "membership_can_add": True,
                    "membership_can_change": True,
                    "membership_can_delete": True,
                },
            ),
        ):
            response = self.client.get(reverse("api-user-profile-detail", args=[username]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["username"], username)
        self.assertNotIn("currentTimeLabel", payload["summary"])
        self.assertEqual(payload["summary"]["countryCode"], "US")
        self.assertNotIn("profileCountry", payload["summary"])
        self.assertEqual(payload["summary"]["socialProfiles"], [{"platform": "x", "urls": ["https://x.com/alice"]}])
        self.assertNotIn("label", payload["summary"]["socialProfiles"][0])
        self.assertNotIn("title", payload["summary"]["socialProfiles"][0])
        self.assertNotIn("icon", payload["summary"]["socialProfiles"][0])
        self.assertEqual(payload["summary"]["websiteUrls"], ["https://example.com", "plain.example.test/path"])
        self.assertEqual(payload["summary"]["rssUrls"], ["https://example.com/feed.xml"])
        self.assertEqual(payload["groups"]["groups"][0]["role"], "sponsor")
        self.assertNotIn("label", payload["accountSetup"]["requiredActions"][0])
        self.assertNotIn("urlLabel", payload["accountSetup"]["requiredActions"][0])
        self.assertEqual(payload["membership"]["entries"][0]["membershipType"]["code"], "individual")
        self.assertEqual(payload["membership"]["entries"][0]["createdAt"], "2024-01-15T12:00:00+00:00")
        self.assertEqual(payload["membership"]["entries"][0]["expiresAt"], "2026-04-30T00:00:00+00:00")
        self.assertTrue(payload["membership"]["entries"][0]["isExpiringSoon"])
        self.assertTrue(payload["membership"]["entries"][0]["canManage"])
        self.assertNotIn("badge", payload["membership"]["entries"][0])
        self.assertNotIn("memberSinceLabel", payload["membership"]["entries"][0])
        self.assertNotIn("expiresLabel", payload["membership"]["entries"][0])
        self.assertNotIn("expiresTone", payload["membership"]["entries"][0])
        self.assertNotIn("management", payload["membership"]["entries"][0])
        self.assertEqual(payload["membership"]["pendingEntries"][0]["status"], "on_hold")
        self.assertNotIn("badge", payload["membership"]["pendingEntries"][0])
        self.assertNotIn("notes", payload["membership"])

    def test_user_profile_detail_api_is_get_only(self) -> None:
        username = "alice"
        self._login_as_freeipa(username)

        response = self.client.post(reverse("api-user-profile-detail", args=[username]))
        self.assertEqual(response.status_code, 405)

    def test_user_profile_detail_api_treats_mixed_case_path_as_self_when_resolved_user_matches(self) -> None:
        self._login_as_freeipa("alex")

        profile_user = FreeIPAUser(
            "alex",
            {
                "uid": ["alex"],
                "givenname": ["Alex"],
                "sn": ["Example"],
                "mail": ["alex@example.test"],
                "timezone": ["UTC"],
            },
        )

        with (
            patch("core.views_users._get_full_user", return_value=profile_user),
            patch("core.views_users._is_membership_committee_viewer", return_value=False),
            patch("core.views_users.FreeIPAGroup.all", return_value=[]),
            patch("core.views_users.has_enabled_agreements", return_value=False),
            patch("core.views_users.resolve_avatar_urls_for_users", return_value=({"alex": "https://avatars.example/alex.png"}, 1, 0)),
        ):
            response = self.client.get(reverse("api-user-profile-detail", args=["ALEX"]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["summary"]["isSelf"])
        self.assertTrue(payload["groups"]["isSelf"])
        self.assertEqual(payload["summary"]["username"], "alex")
        self.assertEqual(payload["membership"]["username"], "alex")
