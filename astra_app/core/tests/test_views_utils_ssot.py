from types import SimpleNamespace
from typing import Any, cast

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse, QueryDict
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from core.views_utils import (
    agreement_settings_url,
    build_page_url_prefix,
    build_url_for_page,
    get_username,
    settings_url,
)


class ViewsUtilsSSOTTests(SimpleTestCase):
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
