
import io
import json
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlsplit

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.models import AccountInvitation, AccountInvitationSend, FreeIPAPermissionGrant, Organization
from core.permissions import ASTRA_ADD_SEND_MAIL
from core.views_send_mail import _CSV_SESSION_KEY, _PREVIEW_CONTEXT_SESSION_KEY, SendMailForm


class SendMailUrlHelperTests(SimpleTestCase):
    def test_send_mail_url_builds_expected_query_params(self) -> None:
        from core.views_utils import send_mail_url

        url = send_mail_url(
            to_type="manual",
            to="user@example.com",
            template_name="template.name",
            extra_context={
                "membership_request_id": "42",
                "organization_name": "Example Org",
            },
            action_status="approved",
            reply_to="committee@example.com",
        )

        parts = urlsplit(url)
        query = parse_qs(parts.query)

        self.assertEqual(parts.path, reverse("send-mail"))
        self.assertEqual(query["type"], ["manual"])
        self.assertEqual(query["to"], ["user@example.com"])
        self.assertEqual(query["template"], ["template.name"])
        self.assertEqual(query["membership_request_id"], ["42"])
        self.assertEqual(query["organization_name"], ["Example Org"])
        self.assertEqual(query["reply_to"], ["committee@example.com"])
        self.assertEqual(query["action_status"], ["approved"])


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
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
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
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[_FakeGroup()]),
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
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
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[_FakeGroup()]),
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
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
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

    def test_get_prefills_reply_to_from_query_params(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        reply_to_raw = "replies@example.com,support@example.com"
        url = reverse("send-mail") + f"?reply_to={quote(reply_to_raw)}"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
        ):
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="id_reply_to"')
        self.assertContains(resp, f'value="{reply_to_raw}"')
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
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=_EmptyGroup()),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[_EmptyGroup()]),
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
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
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

    def test_get_shows_membership_action_notice(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=manual&to=alice@example.com&action_status=approved")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already been approved")
        self.assertContains(resp, "No email has been sent yet")
        self.assertContains(resp, "notify the requester")

    def test_post_send_clears_membership_action_notice(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            queue_mock.return_value = type("_QueuedEmail", (), {"id": 1})()
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "alice@example.com",
                    "action": "send",
                    "subject": "Hello {{ email }}",
                    "html_content": "",
                    "text_content": "Hi {{ email }}",
                    "action_status": "approved",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Queued 1 email")
        self.assertNotContains(resp, "already been approved")
        self.assertNotContains(resp, "No email has been sent yet")

    def test_get_extra_query_params_are_added_to_context(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
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
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[alice, bob]),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
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
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[_FakeGroup()]),
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
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=manual&to=jim@example.com&template=send-mail-prefill")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'<option value="{tpl.pk}" selected>')
        self.assertContains(resp, 'value="Hello {{ email }}"')
        self.assertContains(resp, "Text body for {{ email }}")
        # HTML bodies are shown in a textarea, so they appear HTML-escaped in the page source.
        self.assertContains(resp, "&lt;p&gt;HTML body for {{ email }}&lt;/p&gt;")

    def test_csv_mode_hides_org_claim_template_choice(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        EmailTemplate.objects.update_or_create(
            name="account-invite",
            defaults={
                "subject": "Account invite",
                "content": "Hello {{ email }}",
                "html_content": "<p>Hello {{ email }}</p>",
            },
        )
        EmailTemplate.objects.update_or_create(
            name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
            defaults={
                "subject": "Org claim",
                "content": "Claim {{ organization_name }}",
                "html_content": "<p>Claim {{ organization_name }}</p>",
            },
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
        ):
            resp = self.client.get(reverse("send-mail") + "?type=csv")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "account-invite")
        self.assertNotContains(resp, settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME)

    def test_csv_mode_send_rejects_org_claim_template_even_if_posted(self) -> None:
        from post_office.models import EmailTemplate

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        template = EmailTemplate.objects.create(
            name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
            subject="Org claim",
            content="Claim {{ organization_name }}",
            html_content="<p>Claim {{ organization_name }}</p>",
        )

        session = self.client.session
        session["send_mail_csv_payload_v1"] = json.dumps(
            {
                "header_to_var": {"Email": "email"},
                "recipients": [{"email": "alice@example.com"}],
            }
        )
        session.save()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "csv",
                    "email_template_id": str(template.pk),
                    "subject": "Hello",
                    "html_content": "<p>Hello</p>",
                    "text_content": "Hello",
                    "action": "send",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "cannot be used with CSV recipients")
        queue_mock.assert_not_called()

    def test_send_clears_csv_and_preview_session_data_after_success(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        session = self.client.session
        session[_CSV_SESSION_KEY] = json.dumps(
            {
                "header_to_var": {"Email": "email"},
                "recipients": [
                    {
                        "email": "alice@example.com",
                        "credential_public_id": "CRED-123",
                    }
                ],
            }
        )
        session[_PREVIEW_CONTEXT_SESSION_KEY] = json.dumps(
            {
                "email": "alice@example.com",
                "credential_public_id": "CRED-123",
            }
        )
        session.save()

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            queue_mock.return_value = type("_QueuedEmail", (), {"id": 1})()
            response = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "csv",
                    "subject": "Credential notice {{ credential_public_id }}",
                    "text_content": "Credential {{ credential_public_id }}",
                    "html_content": "<p>Credential {{ credential_public_id }}</p>",
                    "action": "send",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Queued 1 email")

        updated_session = self.client.session
        self.assertNotIn(_CSV_SESSION_KEY, updated_session)
        self.assertNotIn(_PREVIEW_CONTEXT_SESSION_KEY, updated_session)

    def test_send_mail_election_credential_resend_tags_email_context(self) -> None:
        from post_office.models import Email

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        session = self.client.session
        session[_CSV_SESSION_KEY] = json.dumps(
            {
                "header_to_var": {
                    "Email": "email",
                    "Election ID": "election_id",
                },
                "recipients": [
                    {
                        "email": "alice@example.com",
                        "election_id": "5",
                    }
                ],
            }
        )
        session.save()

        def _queue_email(**kwargs):
            return Email.objects.create(
                from_email=kwargs["sender"],
                to=kwargs["recipients"][0],
                cc="",
                bcc="",
                subject=str(kwargs["subject_source"]),
                message=str(kwargs["text_source"]),
                html_message=str(kwargs["html_source"]),
            )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email", side_effect=_queue_email),
        ):
            response = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "csv",
                    "subject": "Credential notice",
                    "text_content": "Credential text",
                    "html_content": "<p>Credential html</p>",
                    "action": "send",
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        queued = Email.objects.order_by("-id").first()
        assert queued is not None

        ctx = queued.context or {}
        if isinstance(ctx, str):
            ctx = json.loads(ctx)

        self.assertEqual(ctx.get("election_id"), 5)
        self.assertIsInstance(ctx.get("election_id"), int)

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
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[_FakeGroup()]),
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
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[_FakeGroup()]),
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
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[_FakeGroup()]),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            queue_mock.return_value = type("_QueuedEmail", (), {"id": 1})()
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
        queue_mock.assert_called_once()
        kwargs = queue_mock.call_args.kwargs
        self.assertEqual(kwargs["recipients"], ["alice@example.com"])
        self.assertEqual(kwargs["sender"], settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(kwargs["subject_source"], "Hello {{ first_name }}")
        self.assertEqual(kwargs["text_source"], "Hi {{ full_name }}")
        self.assertEqual(kwargs["html_source"], "<p>Hi {{ full_name }}</p>")
        self.assertEqual(kwargs["context"]["first_name"], "Alice")
        self.assertEqual(kwargs["context"]["full_name"], "Alice User")
        self.assertEqual(kwargs["cc"], ["cc1@example.com", "cc2@example.com"])
        self.assertEqual(kwargs["bcc"], ["bcc1@example.com"])

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
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[_FakeGroup()]),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            queue_mock.return_value = type("_QueuedEmail", (), {"id": 1})()
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
        queue_mock.assert_called_once()
        kwargs = queue_mock.call_args.kwargs
        self.assertEqual(kwargs["cc"], ["cc1@example.com", "cc2@example.com", "cc3@example.com"])
        self.assertEqual(kwargs["bcc"], ["bcc1@example.com", "bcc2@example.com"])

    def test_send_emails_sets_reply_to_header(self) -> None:
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
            patch("core.freeipa.user.FreeIPAUser.get", side_effect=_get_user),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=_FakeGroup()),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[_FakeGroup()]),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            queue_mock.return_value = type("_QueuedEmail", (), {"id": 1})()
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "group",
                    "group_cn": "example-group",
                    "subject": "Hello {{ first_name }}",
                    "text_content": "Hi {{ full_name }}",
                    "html_content": "<p>Hi {{ full_name }}</p>",
                    "action": "send",
                    "reply_to": "replies@example.com, support@example.com",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        queue_mock.assert_called_once()
        kwargs = queue_mock.call_args.kwargs
        self.assertEqual(kwargs["reply_to"], ["replies@example.com", "support@example.com"])

    def test_send_emails_renders_extra_context_vars(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            queue_mock.return_value = type("_QueuedEmail", (), {"id": 1})()
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
        queue_mock.assert_called_once()
        kwargs = queue_mock.call_args.kwargs
        self.assertEqual(kwargs["context"]["project"], "Atomic")

    @override_settings(PUBLIC_BASE_URL="https://astra.almalinux.org")
    def test_send_emails_renders_system_context_vars(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            queue_mock.return_value = type("_QueuedEmail", (), {"id": 1})()
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "jim@example.com",
                    "subject": "Join {{ register_url }}",
                    "text_content": "Hi",
                    "html_content": "<p>Hi</p>",
                    "action": "send",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        queue_mock.assert_called_once()
        kwargs = queue_mock.call_args.kwargs
        self.assertEqual(kwargs["context"]["register_url"], "https://astra.almalinux.org/register/")

    def test_send_mail_delegates_inline_image_semantics_to_ssot_helper(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
        ):
            queue_mock.return_value = type("_QueuedEmail", (), {"id": 1})()
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
        queue_mock.assert_called_once()
        self.assertIn("<p>HTML</p>", queue_mock.call_args.kwargs["html_source"])

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
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.views_send_mail.queue_composed_email") as queue_mock,
            patch("django.core.files.storage.default_storage.open", return_value=io.BytesIO(png_bytes)),
        ):
            queue_mock.return_value = type("_QueuedEmail", (), {"id": 1})()
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

    def test_send_uses_ssot_queue_helper_with_best_effort_partial_failure(self) -> None:
        from post_office.models import Email

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        first_error = ValueError("render failed")
        queued_email = Email.objects.create(
            from_email=settings.DEFAULT_FROM_EMAIL,
            to="bob@example.com",
            cc="",
            bcc="",
            subject="Hello Bob",
            message="Hi Bob",
            html_message="<p>Hi Bob</p>",
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch(
                "core.views_send_mail.queue_composed_email",
                side_effect=[first_error, queued_email],
            ) as queue_mock,
        ):
            resp = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "alice@example.com, bob@example.com",
                    "subject": "Hello {{ email }}",
                    "text_content": "Hi {{ email }}",
                    "html_content": "<p>Hi {{ email }}</p>",
                    "action": "send",
                },
                follow=True,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(queue_mock.call_count, 2)
        self.assertContains(resp, "Queued 1 email")
        self.assertContains(resp, "Failed to queue 1 email")

    def test_send_mail_org_claim_action_creates_account_invitation_on_send(self) -> None:
        from types import SimpleNamespace

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})
        organization = Organization.objects.create(name="Org Claim", business_contact_email="contact@example.com")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_composed_mock,
            patch("core.views_account_invitations.queue_templated_email", return_value=SimpleNamespace(id=777)),
        ):
            response = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "contact@example.com",
                    "subject": "Claim invitation",
                    "text_content": "Claim {{ organization_name }}",
                    "html_content": "<p>Claim {{ organization_name }}</p>",
                    "action": "send",
                    "invitation_action": "org_claim",
                    "invitation_org_id": str(organization.pk),
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Queued 1 email")
        queue_composed_mock.assert_not_called()

        invitation = AccountInvitation.objects.get(email="contact@example.com")
        self.assertEqual(invitation.organization_id, organization.pk)
        self.assertEqual(invitation.email_template_name, settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME)
        self.assertEqual(invitation.invited_by_username, "reviewer")
        self.assertEqual(invitation.send_count, 1)
        self.assertIsNotNone(invitation.last_sent_at)

        send_record = AccountInvitationSend.objects.get(invitation=invitation)
        self.assertEqual(send_record.result, AccountInvitationSend.Result.queued)
        self.assertEqual(send_record.template_name, settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME)
        self.assertEqual(send_record.sent_by_username, "reviewer")
        self.assertEqual(send_record.post_office_email_id, 777)

    def test_send_mail_org_claim_action_applies_rate_limit(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})
        organization = Organization.objects.create(name="Rate Limited Org", business_contact_email="contact@example.com")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.views_send_mail.allow_request", return_value=False),
            patch("core.views_send_mail.queue_composed_email") as queue_composed_mock,
            patch("core.views_account_invitations.queue_templated_email") as queue_templated_mock,
        ):
            response = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "contact@example.com",
                    "subject": "Claim invitation",
                    "text_content": "Claim {{ organization_name }}",
                    "html_content": "<p>Claim {{ organization_name }}</p>",
                    "action": "send",
                    "invitation_action": "org_claim",
                    "invitation_org_id": str(organization.pk),
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many invitation send attempts")
        queue_composed_mock.assert_not_called()
        queue_templated_mock.assert_not_called()
        self.assertFalse(AccountInvitation.objects.filter(email="contact@example.com").exists())

    def test_send_mail_org_claim_action_rejects_conflict_with_other_organization(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})
        target_organization = Organization.objects.create(name="Target Org", business_contact_email="contact@example.com")
        other_organization = Organization.objects.create(name="Other Org", business_contact_email="other@example.com")
        invitation = AccountInvitation.objects.create(
            email="contact@example.com",
            invited_by_username="committee",
            organization=other_organization,
            email_template_name=settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_composed_mock,
            patch("core.views_account_invitations.queue_templated_email") as queue_templated_mock,
        ):
            response = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "contact@example.com",
                    "subject": "Claim invitation",
                    "text_content": "Claim {{ organization_name }}",
                    "html_content": "<p>Claim {{ organization_name }}</p>",
                    "action": "send",
                    "invitation_action": "org_claim",
                    "invitation_org_id": str(target_organization.pk),
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "linked to a different organization")
        queue_composed_mock.assert_not_called()
        queue_templated_mock.assert_not_called()

        invitation.refresh_from_db()
        self.assertEqual(invitation.organization_id, other_organization.pk)
        self.assertEqual(invitation.send_count, 0)

    def test_send_mail_org_claim_action_rejects_invalid_manual_email(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})
        organization = Organization.objects.create(name="Invalid Email Org", business_contact_email="contact@example.com")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_composed_mock,
            patch("core.views_account_invitations.queue_templated_email") as queue_templated_mock,
        ):
            response = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "manual",
                    "manual_to": "invalid-email",
                    "subject": "Claim invitation",
                    "text_content": "Claim {{ organization_name }}",
                    "html_content": "<p>Claim {{ organization_name }}</p>",
                    "action": "send",
                    "invitation_action": "org_claim",
                    "invitation_org_id": str(organization.pk),
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fix the form errors and try again")
        queue_composed_mock.assert_not_called()
        queue_templated_mock.assert_not_called()
        self.assertFalse(AccountInvitation.objects.filter(email="invalid-email").exists())

    def test_send_mail_org_claim_action_requires_manual_recipient_mode(self) -> None:
        import io

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})
        organization = Organization.objects.create(name="Non-Manual Mode Org", business_contact_email="contact@example.com")

        csv_bytes = b"Email,Display Name\ncontact@example.com,Contact User\n"
        csv_file = io.BytesIO(csv_bytes)
        csv_file.name = "recipients.csv"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
            patch("core.views_send_mail.queue_composed_email") as queue_composed_mock,
            patch("core.views_account_invitations.queue_templated_email") as queue_templated_mock,
        ):
            response = self.client.post(
                reverse("send-mail"),
                data={
                    "recipient_mode": "csv",
                    "csv_file": csv_file,
                    "subject": "Claim invitation",
                    "text_content": "Claim {{ organization_name }}",
                    "html_content": "<p>Claim {{ organization_name }}</p>",
                    "action": "send",
                    "invitation_action": "org_claim",
                    "invitation_org_id": str(organization.pk),
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Organization claim invitations require manual recipients")
        queue_composed_mock.assert_not_called()
        queue_templated_mock.assert_not_called()
        self.assertFalse(AccountInvitation.objects.filter(email="contact@example.com").exists())

    def test_get_prefills_org_claim_invitation_hidden_fields_from_query_params(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        url = (
            reverse("send-mail")
            + "?type=manual&to=contact@example.com&invitation_action=org_claim&invitation_org_id=123"
        )
        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.freeipa.group.FreeIPAGroup.all", return_value=[]),
            patch("core.freeipa.user.FreeIPAUser.all", return_value=[]),
        ):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="invitation_action"')
        self.assertContains(response, 'value="org_claim"')
        self.assertContains(response, 'name="invitation_org_id"')
        self.assertContains(response, 'value="123"')


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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
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


class SendMailFormTests(SimpleTestCase):
    def test_reply_to_rejects_invalid_addresses(self) -> None:
        form = SendMailForm(data={"reply_to": "not-an-email"})
        self.assertFalse(form.is_valid())
        self.assertIn("reply_to", form.errors)
