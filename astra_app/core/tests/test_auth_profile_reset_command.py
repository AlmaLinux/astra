import io
import json
from importlib import import_module

from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.freeipa.agreement import FreeIPAFASAgreement
from core.freeipa.e2e_registry import get_e2e_service_client
from core.models import MembershipRequest, Note, Organization


class AuthProfileResetCommandTests(TestCase):
    def _create_session(self, *, username: str | None = None) -> str:
        session_store = import_module("django.contrib.sessions.backends.db").SessionStore()
        if username is not None:
            session_store["_freeipa_username"] = username
        session_store.save()
        return session_store.session_key

    @override_settings(ASTRA_E2E_MODE=False, ASTRA_E2E_FAKE_FREEIPA_ENABLED=False)
    def test_command_rejects_runs_outside_fake_freeipa_e2e_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "ASTRA_E2E_FAKE_FREEIPA_ENABLED"):
            call_command("auth_profile_reset")

    @override_settings(ASTRA_E2E_MODE=True, ASTRA_E2E_FAKE_FREEIPA_ENABLED=True)
    def test_command_only_clears_fake_auth_sessions_and_converges_without_creating_unrelated_data(self) -> None:
        stdout_first = io.StringIO()
        stdout_second = io.StringIO()
        client = get_e2e_service_client()
        regular_session_key = self._create_session(username="regular")
        numbered_regular_session_key = self._create_session(username="regular07")
        admin_session_key = self._create_session(username="ADMIN")
        other_session_key = self._create_session(username="other-user")
        anonymous_session_key = self._create_session()

        client.group_add("new-membership-group", o_description="Membership type group")
        FreeIPAFASAgreement.create("AlmaLinux Community Code of Conduct", description="CoC")

        call_command("auth_profile_reset", stdout=stdout_first)
        first_payload = json.loads(stdout_first.getvalue())

        call_command("auth_profile_reset", stdout=stdout_second)
        second_payload = json.loads(stdout_second.getvalue())

        self.assertEqual(first_payload["scenario"], "auth-profile")
        self.assertEqual(second_payload["scenario"], "auth-profile")
        self.assertEqual(first_payload, second_payload)
        self.assertFalse(Session.objects.filter(session_key=regular_session_key).exists())
        self.assertFalse(Session.objects.filter(session_key=numbered_regular_session_key).exists())
        self.assertFalse(Session.objects.filter(session_key=admin_session_key).exists())
        self.assertTrue(Session.objects.filter(session_key=other_session_key).exists())
        self.assertTrue(Session.objects.filter(session_key=anonymous_session_key).exists())
        self.assertEqual(client.group_find(o_cn="new-membership-group", o_all=True, o_no_members=False), {"count": 0, "result": []})
        self.assertEqual(FreeIPAFASAgreement.all(), [])
        self.assertEqual(client.group_find(o_cn="packagers", o_all=True, o_no_members=False)["count"], 1)
        self.assertEqual(MembershipRequest.objects.count(), 0)
        self.assertEqual(Note.objects.count(), 0)
        self.assertEqual(Organization.objects.count(), 0)