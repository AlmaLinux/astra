from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core import views_membership, views_settings
from core.country_codes import country_code_status_from_user_data, country_name_from_code
from core.membership_notes import CUSTOS
from core.models import MembershipRequest, MembershipType, Note


class MembershipCountryRequirementsTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self._coc_patcher = patch("core.views_membership.block_action_without_coc", return_value=None)
        self._coc_patcher.start()
        self.addCleanup(self._coc_patcher.stop)

    def _ensure_membership_type(self) -> MembershipType:
        membership_type, _created = MembershipType.objects.get_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "members",
                "isIndividual": True,
                "enabled": True,
            },
        )

        update_fields: list[str] = []
        if membership_type.name != "Individual":
            membership_type.name = "Individual"
            update_fields.append("name")
        if membership_type.group_cn != "members":
            membership_type.group_cn = "members"
            update_fields.append("group_cn")
        if not membership_type.isIndividual:
            membership_type.isIndividual = True
            update_fields.append("isIndividual")
        if not membership_type.enabled:
            membership_type.enabled = True
            update_fields.append("enabled")
        if update_fields:
            membership_type.save(update_fields=update_fields)

        return membership_type

    def _add_session_and_messages(self, request):
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))
        return request

    def _auth_user(self, username: str = "alice"):
        return SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: username,
            email=f"{username}@example.org",
        )

    def _committee_user(self, username: str = "committee"):
        return SimpleNamespace(
            is_authenticated=True,
            get_username=lambda: username,
            has_perm=lambda _p: True,
            has_perms=lambda _ps: True,
        )

    @staticmethod
    def _fake_freeipa_user(*, username: str, user_data: dict) -> SimpleNamespace:
        return SimpleNamespace(username=username, _user_data=user_data)

    def test_membership_request_blocks_when_country_missing(self) -> None:
        self._ensure_membership_type()

        request = self.factory.get("/membership/request/")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fake_user = self._fake_freeipa_user(username="alice", user_data={})
        with patch("core.views_membership.FreeIPAUser.get", autospec=True, return_value=fake_user):
            response = views_membership.membership_request(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#profile")
        msgs = [m.message for m in get_messages(request)]
        self.assertTrue(any("country" in m.lower() for m in msgs))

    @override_settings(SELF_SERVICE_ADDRESS_COUNTRY_ATTR="c")
    def test_country_code_status_accepts_case_insensitive_attr(self) -> None:
        status = country_code_status_from_user_data({"C": ["US"]})
        self.assertTrue(status.is_valid)
        self.assertEqual(status.code, "US")

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU", "IR"])
    def test_membership_request_does_not_warn_user_when_country_embargoed(self) -> None:
        self._ensure_membership_type()

        request = self.factory.get("/membership/request/")
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fake_user = self._fake_freeipa_user(username="alice", user_data={"fasstatusnote": ["RU"]})

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with patch("core.views_membership.FreeIPAUser.get", autospec=True, return_value=fake_user):
            with patch("core.views_membership.render", autospec=True, side_effect=fake_render):
                response = views_membership.membership_request(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured.get("template"), "core/membership_request.html")
        msgs = [m.message for m in get_messages(request)]
        self.assertFalse(any("embargo" in m.lower() for m in msgs))

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU", "IR"])
    def test_membership_request_submits_and_does_not_persist_embargoed_country_warning(self) -> None:
        self._ensure_membership_type()

        request = self.factory.post(
            "/membership/request/",
            data={
                "membership_type": "individual",
                "q_contributions": "Did contributions",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fake_user = self._fake_freeipa_user(username="alice", user_data={"fasstatusnote": ["RU"]})

        with patch(
            "core.forms_membership.get_valid_membership_type_codes_for_username",
            autospec=True,
            return_value=set(),
        ):
            with patch(
                "core.forms_membership.get_extendable_membership_type_codes_for_username",
                autospec=True,
                return_value=set(),
            ):
                with patch("core.views_membership.FreeIPAUser.get", autospec=True, return_value=fake_user):
                    with patch(
                        "core.views_membership.record_membership_request_created",
                        autospec=True,
                        return_value=None,
                    ):
                        response = views_membership.membership_request(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("user-profile", kwargs={"username": "alice"}))

        mr = MembershipRequest.objects.get(requested_username="alice")
        # Embargoed-country warning is computed at display time for committee review,
        # not persisted into the request itself.
        keys = {k.lower() for item in (mr.responses or []) for k in item.keys()}
        self.assertNotIn("country warning", keys)
        self.assertNotIn("country code", keys)

        system_note = mr.notes.filter(username=CUSTOS).first()
        self.assertIsNotNone(system_note)
        assert system_note is not None
        self.assertEqual(
            system_note.content,
            f"alice is from {country_name_from_code('RU')} (RU), which is on the embargoed list.",
        )

    @override_settings(MEMBERSHIP_EMBARGOED_COUNTRY_CODES=["RU", "IR"])
    def test_membership_committee_sees_embargoed_country_warning(self) -> None:
        self._ensure_membership_type()

        mr = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[],
        )

        request = self.factory.get(f"/membership/requests/{mr.pk}/")
        self._add_session_and_messages(request)
        request.user = self._committee_user()

        fake_target = SimpleNamespace(
            username="alice",
            full_name="Alice User",
            _user_data={"fasstatusnote": ["RU"]},
        )

        captured: dict[str, object] = {}

        def fake_render(_request, template, context):
            captured["template"] = template
            captured["context"] = context
            return HttpResponse("ok")

        with patch("core.views_membership.FreeIPAUser.get", autospec=True, return_value=fake_target):
            with patch("core.views_membership.render", autospec=True, side_effect=fake_render):
                response = views_membership.membership_request_detail(request, pk=mr.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured.get("template"), "core/membership_request_detail.html")
        ctx = captured.get("context") or {}
        self.assertEqual(ctx.get("embargoed_country_code"), "RU")
        self.assertEqual(ctx.get("embargoed_country_label"), f"{country_name_from_code('RU')} (RU)")

    def test_country_change_with_pending_request_creates_system_note(self) -> None:
        self._ensure_membership_type()

        mr = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.pending,
            responses=[],
        )

        request = self.factory.post(
            "/settings/",
            data={
                "tab": "profile",
                "givenname": "Alice",
                "sn": "User",
                "country_code": "US",
                "fasPronoun": "",
                "fasLocale": "",
                "fasTimezone": "",
                "fasWebsiteUrl": "",
                "fasRssUrl": "",
                "fasIRCNick": "",
                "fasGitHubUsername": "",
                "fasGitLabUsername": "",
                "fasIsPrivate": "",
            },
        )
        self._add_session_and_messages(request)
        request.user = self._auth_user("alice")

        fake_user = self._fake_freeipa_user(
            username="alice",
            user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "fasstatusnote": ["RU"],
            },
        )

        with patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user):
            with patch("core.views_settings._update_user_attrs", autospec=True, return_value=([], True)):
                response = views_settings.settings_root(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("settings") + "#profile")

        notes = list(Note.objects.filter(membership_request=mr, username=CUSTOS).order_by("timestamp", "pk"))
        self.assertTrue(
            any(
                n.content
                == f"alice updated their country from {country_name_from_code('RU')} (RU) to {country_name_from_code('US')} (US)."
                for n in notes
            ),
            "Expected a system note about the country change.",
        )
