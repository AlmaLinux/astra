from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse, QueryDict
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from core.views_utils import (
    agreement_settings_url,
    block_action_without_coc,
    build_page_url_prefix,
    build_url_for_page,
    get_username,
    settings_context,
    settings_url,
)


class ViewsUtilsSSOTTests(SimpleTestCase):
    def test_settings_context_exposes_registry_filtered_tabs(self):
        from core.settings_tabs import SETTINGS_TAB_REGISTRY

        with patch("core.views_utils.has_enabled_agreements", return_value=False):
            context = settings_context("agreements")

        self.assertEqual(context["tabs"], [tab.tab_id for tab in SETTINGS_TAB_REGISTRY])
        self.assertEqual(
            [tab.tab_id for tab in context["settings_tabs"]],
            ["profile", "emails", "keys", "security"],
        )
        self.assertEqual(context["active_tab"], "profile")
        self.assertFalse(context["show_agreements_tab"])

    def test_agreement_settings_url_builds_expected_shapes(self):
        self.assertEqual(agreement_settings_url(None), reverse("settings") + "?tab=agreements")
        self.assertEqual(
            agreement_settings_url("cla"),
            reverse("settings") + "?tab=agreements&agreement=cla",
        )
        self.assertEqual(
            agreement_settings_url("cla", return_to="profile"),
            reverse("settings") + "?tab=agreements&agreement=cla&return=profile",
        )

    def test_settings_url_builds_allowlisted_query_shapes(self):
        self.assertEqual(settings_url(), reverse("settings"))
        self.assertEqual(settings_url(tab="profile"), reverse("settings") + "?tab=profile")
        self.assertEqual(
            settings_url(tab="profile", highlight="country_code"),
            reverse("settings") + "?tab=profile&highlight=country_code",
        )
        self.assertEqual(
            settings_url(tab="agreements", status="saved"),
            reverse("settings") + "?tab=agreements&status=saved",
        )

    def test_settings_url_allows_safe_relative_return_path(self):
        url = settings_url(tab="profile", return_to="/organizations/claim/")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query.get("tab"), ["profile"])
        self.assertEqual(query.get("return"), ["/organizations/claim/"])

    def test_settings_url_rejects_protocol_relative_return_path(self):
        url = settings_url(tab="profile", return_to="//evil.example/path")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query.get("tab"), ["profile"])
        self.assertNotIn("return", query)

    def test_block_action_without_coc_redirect_includes_current_path(self):
        request = RequestFactory().get("/organizations/claim/")

        with (
            patch("core.views_utils.has_signed_coc", return_value=False),
            patch("core.views_utils.messages.error"),
            patch("core.views_utils.settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN", "coc"),
        ):
            response = block_action_without_coc(
                request,
                username="alice",
                action_label="claim this organization",
            )

        self.assertEqual(response.status_code, 302)
        location = str(response["Location"])
        parsed = urlparse(location)
        self.assertEqual(parsed.path, reverse("settings"))
        query = parse_qs(parsed.query)
        self.assertEqual(query.get("tab"), ["agreements"])
        self.assertEqual(query.get("agreement"), ["coc"])
        self.assertEqual(query.get("return"), ["/organizations/claim/"])

    def test_get_username_can_skip_user_fallback_without_forcing_lazy_user(self):
        request = RequestFactory().get("/")
        SessionMiddleware(lambda _request: HttpResponse("ok")).process_request(request)
        request.session["_freeipa_username"] = ""

        lazy_eval_count = {"count": 0}

        def _build_user():
            lazy_eval_count["count"] += 1
            return SimpleNamespace(get_username=lambda: "bob")

        cast(Any, request).user = SimpleLazyObject(_build_user)

        self.assertEqual(get_username(request, allow_user_fallback=False), "")
        self.assertEqual(lazy_eval_count["count"], 0)

    def test_build_page_url_prefix_drops_only_selected_page_parameter(self):
        query = QueryDict("q=alice&page=3&sort=name")
        base_query, page_url_prefix = build_page_url_prefix(query, page_param="page")
        self.assertNotIn("page=", base_query)
        self.assertIn("q=alice", base_query)
        self.assertIn("sort=name", base_query)
        self.assertEqual(page_url_prefix, f"?{base_query}&page=")

    def test_build_url_for_page_retains_other_query_parameters(self):
        query = QueryDict("q=alice&page=3&sort=name")
        url = build_url_for_page(
            reverse("elections"),
            query=query,
            page_param="page",
            page_value=7,
        )
        self.assertTrue(url.startswith(reverse("elections") + "?"))
        self.assertIn("q=alice", url)
        self.assertIn("sort=name", url)
        self.assertIn("page=7", url)
