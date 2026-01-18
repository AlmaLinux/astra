from __future__ import annotations

import io
import json
from unittest.mock import patch
from urllib.parse import quote

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from core.backends import FreeIPAUser
from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_SEND_MAIL


class SendMailTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def setUp(self) -> None:
        super().setUp()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def test_requires_permission(self) -> None:
        self._login_as_freeipa_user("alice")

        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("send-mail"))

        self.assertEqual(resp.status_code, 302)

    def test_group_recipients_show_variables_and_count(self) -> None:
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        class _FakeGroup:
            cn = "example-group"
            description = ""

            def member_usernames_recursive(self) -> set[str]:
                return {"alice", "bob"}

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "displayname": ["Alice User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "displayname": ["Bob User"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.backends.FreeIPAGroup.all", return_value=[_FakeGroup()]),
        ):
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "group",
                    "group_cn": "example-group",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Send Mail")
        self.assertContains(resp, "Recipients")
        self.assertContains(resp, "2")
        self.assertContains(resp, "{{ full_name }}")
        self.assertContains(resp, "Alice User")

    def test_csv_recipients_show_header_variables(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        csv_bytes = b"Email,Display Name,Company\nalice@example.com,Alice User,Acme\n"
        csv_file = io.BytesIO(csv_bytes)
        csv_file.name = "recipients.csv"

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "csv",
                    "csv_file": csv_file,
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "{{ email }}")
        self.assertContains(resp, "{{ display_name }}")
        self.assertContains(resp, "{{ company }}")
        self.assertContains(resp, "alice@example.com")

    def test_manual_recipients_show_variables_and_count(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "jim@example.com, bob@example.com",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Recipient count")
        self.assertContains(resp, "2")
        self.assertContains(resp, "{{ email }}")
        self.assertContains(resp, "jim@example.com")

    def test_get_prefills_group_recipients_from_query_params(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        class _FakeGroup:
            cn = "example-group"
            description = ""

            def member_usernames_recursive(self) -> set[str]:
                return {"alice", "bob"}

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "displayname": ["Alice User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "sn": ["User"],
                "displayname": ["Bob User"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.backends.FreeIPAGroup.all", return_value=[_FakeGroup()]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=group&to=example-group")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="send-mail-recipient-mode" value="group"')
        # Deep-link should auto-load recipients on GET.
        self.assertContains(resp, "Recipient count")
        self.assertContains(resp, "2")
        self.assertContains(resp, "{{ full_name }}")
        self.assertContains(resp, "alice@example.com")

    def test_get_prefills_cc_from_query_params(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        cc_raw = "cc1@example.com,cc2@example.com"
        url = reverse("send-mail") + f"?cc={quote(cc_raw)}"

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
            patch("core.backends.FreeIPAUser.all", return_value=[]),
        ):
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="id_cc"')
        self.assertContains(resp, f'value="{cc_raw}"')
        # When cc is prefilled, open the Additional recipients section by default.
        self.assertContains(resp, 'id="send-mail-extra-options-toggle"')
        self.assertContains(resp, 'aria-expanded="true"')
        self.assertContains(resp, 'id="send-mail-extra-options"')
        self.assertContains(resp, 'class="collapse mt-2 show"')

    def test_empty_group_still_shows_placeholder_variable_examples(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        class _EmptyGroup:
            cn = "empty-group"
            description = ""

            def member_usernames_recursive(self) -> set[str]:
                return set()

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAGroup.get", return_value=_EmptyGroup()),
            patch("core.backends.FreeIPAGroup.all", return_value=[_EmptyGroup()]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=group&to=empty-group")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Recipient count")
        self.assertContains(resp, ">0<")

        # Even with no recipients, show useful example placeholders.
        self.assertContains(resp, "{{ username }}")
        self.assertContains(resp, "{{ first_name }}")
        self.assertContains(resp, "{{ last_name }}")
        self.assertContains(resp, "{{ email }}")
        self.assertContains(resp, "{{ full_name }}")
        self.assertContains(resp, "-username-")
        self.assertContains(resp, "-first_name-")
        self.assertContains(resp, "-last_name-")
        self.assertContains(resp, "-email-")
        self.assertContains(resp, "-full_name-")

    def test_get_prefills_manual_recipients_from_query_params(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=manual&to=jim@example.com")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="send-mail-recipient-mode" value="manual"')
        self.assertContains(resp, 'name="manual_to"')
        # Deep-link should auto-load recipients on GET.
        self.assertContains(resp, "Recipient count")
        self.assertContains(resp, "1")
        self.assertContains(resp, "{{ email }}")
        self.assertContains(resp, "jim@example.com")

    def test_get_extra_query_params_are_added_to_context(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(
                reverse("send-mail") + "?type=manual&to=jim@example.com&foo=bar&project-name=Atomic+SIG"
            )

        self.assertEqual(resp.status_code, 200)
        # Extra params become template variables.
        self.assertContains(resp, "{{ foo }}")
        self.assertContains(resp, "bar")
        self.assertContains(resp, "{{ project_name }}")
        self.assertContains(resp, "Atomic SIG")

    def test_get_prefills_users_recipients_from_query_params(self) -> None:
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )
        # Make Bob intentionally less complete so Alice wins the "best example" selection.
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "sn": ["User"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAUser.all", return_value=[alice, bob]),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=users&to=alice,bob")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="send-mail-recipient-mode" value="users"')
        # Deep-link should auto-load recipients on GET.
        self.assertContains(resp, "Recipient count")
        self.assertContains(resp, "2")
        self.assertContains(resp, "{{ email }}")
        self.assertContains(resp, "alice@example.com")
        # Should preselect the users in the multi-select.
        self.assertContains(resp, '<option value="alice" selected>')
        self.assertContains(resp, '<option value="bob" selected>')

    def test_variable_examples_choose_best_context_and_placeholder_missing(self) -> None:
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        class _FakeGroup:
            cn = "example-group"
            description = ""

            def member_usernames_recursive(self) -> set[str]:
                return {"alice", "bob"}

        # Alice is first in sorted order but has fewer filled vars.
        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )
        # Bob is more complete but intentionally missing last_name to trigger placeholder.
        bob = FreeIPAUser(
            "bob",
            {
                "uid": ["bob"],
                "givenname": ["Bob"],
                "mail": ["bob@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            if username == "bob":
                return bob
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.backends.FreeIPAGroup.all", return_value=[_FakeGroup()]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=group&to=example-group")

        self.assertEqual(resp.status_code, 200)
        # Examples should be taken from Bob (more fields filled) rather than Alice.
        self.assertContains(resp, "{{ first_name }}")
        self.assertContains(resp, "Bob")
        # Missing values in the chosen example context should use a placeholder.
        self.assertContains(resp, "{{ last_name }}")
        self.assertContains(resp, "-last_name-")

    def test_get_prefills_email_template_from_query_param(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        tpl = EmailTemplate.objects.create(
            name="send-mail-prefill",
            subject="Hello {{ email }}",
            content="Text body for {{ email }}",
            html_content="<p>HTML body for {{ email }}</p>",
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=manual&to=jim@example.com&template=send-mail-prefill")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'<option value="{tpl.pk}" selected>')
        self.assertContains(resp, 'value="Hello {{ email }}"')
        self.assertContains(resp, "Text body for {{ email }}")
        # HTML bodies are shown in a textarea, so they appear HTML-escaped in the page source.
        self.assertContains(resp, "&lt;p&gt;HTML body for {{ email }}&lt;/p&gt;")

    def test_compose_shows_html_to_text_button_and_variables_card(self) -> None:
        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        class _FakeGroup:
            cn = "example-group"
            description = ""

            def member_usernames_recursive(self) -> set[str]:
                return {"alice"}

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "displayname": ["Alice User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.backends.FreeIPAGroup.all", return_value=[_FakeGroup()]),
        ):
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "group",
                    "group_cn": "example-group",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Available variables")
        self.assertContains(resp, 'data-compose-action="copy-html-to-text"')

    def test_save_as_template_appears_and_is_selected(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")

        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        class _FakeGroup:
            cn = "example-group"
            description = ""

            def member_usernames_recursive(self) -> set[str]:
                return {"alice"}

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "displayname": ["Alice User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        original = EmailTemplate.objects.create(
            name="Original Send Mail Template",
            subject="Original subject",
            content="Original text",
            html_content="<p>Original html</p>",
        )

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.backends.FreeIPAGroup.all", return_value=[_FakeGroup()]),
        ):
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "group",
                    "group_cn": "example-group",
                    "email_template_id": str(original.pk),
                    "subject": "Hello {{ full_name }}",
                    "text_content": "Hi {{ full_name }}",
                    "html_content": "<p>Hi {{ full_name }}</p>",
                    "action": "save_as",
                    "save_as_name": "New Send Mail Template",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        tpl = EmailTemplate.objects.get(name="New Send Mail Template")
        self.assertContains(resp, "New Send Mail Template")
        self.assertContains(resp, f'<option value="{tpl.pk}" selected>')
        self.assertNotContains(resp, f'<option value="{original.pk}" selected>')
        self.assertContains(resp, f'id="send-mail-autoload-template-id" value="{tpl.pk}"')

    def test_send_emails_renders_per_recipient(self) -> None:
        from django.conf import settings
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        class _FakeGroup:
            cn = "example-group"
            description = ""

            def member_usernames_recursive(self) -> set[str]:
                return {"alice"}

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "displayname": ["Alice User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        EmailTemplate.objects.create(
            name="send-mail-test",
            subject="Hello {{ first_name }}",
            content="Hi {{ full_name }}",
            html_content="<p>Hi {{ full_name }}</p>",
        )

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.backends.FreeIPAGroup.all", return_value=[_FakeGroup()]),
            patch("core.views_send_mail.EmailMultiAlternatives", autospec=True) as email_cls,
        ):
            email_cls.return_value.send.return_value = 1
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "group",
                    "group_cn": "example-group",
                    "subject": "Hello {{ first_name }}",
                    "text_content": "Hi {{ full_name }}",
                    "html_content": "<p>Hi {{ full_name }}</p>",
                    "action": "send",
                    "cc": "cc1@example.com, cc2@example.com",
                    "bcc": "bcc1@example.com",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        email_cls.assert_called_once()
        args, kwargs = email_cls.call_args
        self.assertEqual(args[0], "Hello Alice")
        self.assertEqual(args[1], "Hi Alice User")
        self.assertEqual(args[2], settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(args[3], ["alice@example.com"])
        self.assertEqual(kwargs["cc"], ["cc1@example.com", "cc2@example.com"])
        self.assertEqual(kwargs["bcc"], ["bcc1@example.com"])

        email_cls.return_value.attach_alternative.assert_called_once_with("<p>Hi Alice User</p>", "text/html")
        email_cls.return_value.send.assert_called_once()

    def test_send_emails_accepts_whitespace_separated_cc_bcc(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        class _FakeGroup:
            cn = "example-group"
            description = ""

            def member_usernames_recursive(self) -> set[str]:
                return {"alice"}

        alice = FreeIPAUser(
            "alice",
            {
                "uid": ["alice"],
                "givenname": ["Alice"],
                "sn": ["User"],
                "displayname": ["Alice User"],
                "mail": ["alice@example.com"],
                "memberof_group": [],
            },
        )

        def _get_user(username: str):
            if username == "reviewer":
                return reviewer
            if username == "alice":
                return alice
            return None

        EmailTemplate.objects.create(
            name="send-mail-test",
            subject="Hello {{ first_name }}",
            content="Hi {{ full_name }}",
            html_content="<p>Hi {{ full_name }}</p>",
        )

        with (
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
            patch("core.backends.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.backends.FreeIPAGroup.all", return_value=[_FakeGroup()]),
            patch("core.views_send_mail.EmailMultiAlternatives", autospec=True) as email_cls,
        ):
            email_cls.return_value.send.return_value = 1
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "group",
                    "group_cn": "example-group",
                    "subject": "Hello {{ first_name }}",
                    "text_content": "Hi {{ full_name }}",
                    "html_content": "<p>Hi {{ full_name }}</p>",
                    "action": "send",
                    "cc": "cc1@example.com\ncc2@example.com; cc3@example.com",
                    "bcc": "bcc1@example.com\n bcc2@example.com",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        email_cls.assert_called_once()
        _args, kwargs = email_cls.call_args
        self.assertEqual(kwargs["cc"], ["cc1@example.com", "cc2@example.com", "cc3@example.com"])
        self.assertEqual(kwargs["bcc"], ["bcc1@example.com", "bcc2@example.com"])

    def test_send_emails_renders_extra_context_vars(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
            patch("core.views_send_mail.EmailMultiAlternatives", autospec=True) as email_cls,
        ):
            email_cls.return_value.send.return_value = 1
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "jim@example.com",
                    "subject": "Hello {{ project }}",
                    "text_content": "Hi",
                    "html_content": "<p>Hi</p>",
                    "extra_context_json": json.dumps({"project": "Atomic"}),
                    "action": "send",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        email_cls.assert_called_once()
        args, _kwargs = email_cls.call_args
        self.assertEqual(args[0], "Hello Atomic")

    def test_send_mail_attaches_related_inline_images(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        class _FakeTemplate:
            def __init__(self, rendered: str) -> None:
                self._rendered = rendered
                self.attach_related_calls: list[object] = []

            def render(self, context: dict[str, str]) -> str:
                return self._rendered

            def attach_related(self, email_message: object) -> None:
                self.attach_related_calls.append(email_message)

        class _FakeEngine:
            def __init__(self) -> None:
                self._templates: list[_FakeTemplate] = []

            def from_string(self, _raw: str) -> _FakeTemplate:
                rendered = ["SUBJ", "TEXT", "<p>HTML</p>"][len(self._templates)]
                tpl = _FakeTemplate(rendered)
                self._templates.append(tpl)
                return tpl

        fake_engine = _FakeEngine()

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.backends.FreeIPAGroup.all", return_value=[]),
            patch("core.views_send_mail.engines", {"post_office": fake_engine}),
            patch("core.views_send_mail.EmailMultiAlternatives", autospec=True) as email_cls,
        ):
            email_cls.return_value.send.return_value = 1
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "jim@example.com",
                    "subject": "SUBJ",
                    "text_content": "TEXT",
                    "html_content": "<p>HTML</p>",
                    "action": "send",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        # html template is the third from_string() call.
        self.assertEqual(len(fake_engine._templates), 3)
        html_template = fake_engine._templates[2]
        self.assertEqual(len(html_template.attach_related_calls), 1)

    @override_settings(DEBUG=True)
    def test_send_mail_supports_inline_image_url_from_storage(self) -> None:
        # In DEBUG mode, django-post-office's inline_image tag raises if it can't
        # resolve the file. This matches local/dev behavior where the current bug
        # is most visible.
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        image_url = "http://localhost:9000/astra-media/mail-images/logo.png"
        html = "{% load post_office %}\n" f"<img src=\"{{% inline_image '{image_url}' %}}\" />\n"

        # Minimal valid 1x1 PNG so MIMEImage can infer subtype.
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        with (
            patch("core.backends.FreeIPAUser.get", return_value=reviewer),
            patch("core.views_send_mail.EmailMultiAlternatives.send", return_value=1),
            patch("django.core.files.storage.default_storage.open", return_value=io.BytesIO(png_bytes)),
        ):
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "jim@example.com",
                    "subject": "SUBJ",
                    "text_content": "TEXT",
                    "html_content": html,
                    "action": "send",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Queued 1 email")
        self.assertNotContains(resp, "Failed to queue")
        self.assertNotContains(resp, "Template error")


class UnifiedEmailPreviewSendMailTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def setUp(self) -> None:
        super().setUp()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

    def test_unified_preview_requires_loaded_recipients(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("send-mail-render-preview"),
                data={
                    "subject": "Hello {{ full_name }}",
                    "html_content": "<p>{{ full_name }}</p>",
                    "text_content": "{{ full_name }}",
                },
            )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Load recipients", resp.json().get("error", ""))

    def test_unified_preview_renders_inline_image_tag_as_url_in_html(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        session = self.client.session
        session["send_mail_preview_first_context_v1"] = json.dumps({"full_name": "Preview User"})
        session.save()

        image_url = "http://localhost:9000/astra-media/mail-images/logo.png"
        html = f'<p>Hello</p><img src="{{% inline_image \'{image_url}\' %}}" />'

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("send-mail-render-preview"),
                data={
                    "subject": "Hello",
                    "html_content": html,
                    "text_content": "Plain text",
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn(image_url, payload.get("html", ""))
        self.assertNotIn("{% inline_image", payload.get("html", ""))

    def test_unified_preview_renders_unquoted_inline_image_tag_as_url_in_html(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        session = self.client.session
        session["send_mail_preview_first_context_v1"] = json.dumps({"full_name": "Preview User"})
        session.save()

        image_url = "http://localhost:9000/astra-media/mail-images/logo.png"
        html = f'<p>Hello</p><img src="{{% inline_image {image_url} %}}" />'

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("send-mail-render-preview"),
                data={
                    "subject": "Hello",
                    "html_content": html,
                    "text_content": "Plain text",
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn(image_url, payload.get("html", ""))
        self.assertNotIn("{% inline_image", payload.get("html", ""))

    def test_unified_preview_with_load_post_office_and_inline_image_tag(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        session = self.client.session
        session["send_mail_preview_first_context_v1"] = json.dumps({"full_name": "Preview User"})
        session.save()

        image_url = "http://localhost:9000/astra-media/mail-images/logo.png"
        html = (
            "{% load post_office %}\n"
            "<p><em>The AlmaLinux Team</em></p>\n"
            f"<img src=\"{{% inline_image '{image_url}' %}}\" />\n"
        )

        with patch("core.backends.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("send-mail-render-preview"),
                data={
                    "subject": "Hello",
                    "html_content": html,
                    "text_content": "Plain text",
                },
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn(image_url, payload.get("html", ""))
        self.assertNotIn("{% inline_image", payload.get("html", ""))
