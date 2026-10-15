import datetime
import json
import re
from unittest.mock import patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from post_office.models import Email

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_SEND_MAIL
from core.tests.test_send_mail import _deliver_send_mail_inline
from core.tests.test_sponsorship_invoices import IN_MEMORY_STORAGES


@override_settings(STORAGES=IN_MEMORY_STORAGES)
class SendMailAttachmentTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_SEND_MAIL,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="reviewer",
        )
        _deliver_send_mail_inline(self)
        session = self.client.session
        session["_freeipa_username"] = "reviewer"
        session.save()
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": []})
        for target, kwargs in (
            ("core.freeipa.user.FreeIPAUser.get", {"return_value": reviewer}),
            ("core.freeipa.group.FreeIPAGroup.all", {"return_value": []}),
            ("core.freeipa.user.FreeIPAUser.all", {"return_value": []}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _payload(self, response) -> dict[str, object]:
        match = re.search(
            r'<script id="send-mail-initial-payload" type="application/json">(?P<payload>.*?)</script>',
            response.content.decode("utf-8"),
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        return json.loads(match.group("payload"))

    def _post(self, **data):
        return self.client.post(
            reverse("send-mail"),
            data={
                "recipient_mode": "manual",
                "manual_to": "jim@example.com, bob@example.com",
                "subject": "Hello",
                "text_content": "Hello {{ email }}",
                "html_content": "<p>Hello</p>",
                **data,
            },
        )

    def _upload(self, *files: SimpleUploadedFile, attached: list[str] | None = None):
        return self.client.post(
            reverse("send-mail-attachments-upload"),
            data={"files": list(files), "attachment": attached or []},
        )

    def test_picked_files_upload_immediately_and_are_attached_to_every_email(self) -> None:
        from core.mail_attachments import mail_attachments_from_tokens

        first = self._upload(SimpleUploadedFile("agenda.pdf", b"%PDF-agenda", content_type="application/pdf"))
        self.assertEqual(first.status_code, 200)
        [agenda] = first.json()["attachments"]
        self.assertEqual((agenda["name"], agenda["size"]), ("agenda.pdf", len(b"%PDF-agenda")))
        # A second pick adds to what is already attached.
        [notes] = self._upload(
            SimpleUploadedFile("notes.txt", b"notes", content_type="text/plain"),
            attached=[agenda["token"]],
        ).json()["attachments"]

        self._post(action="send", attachment=[agenda["token"], notes["token"]])

        emails = list(Email.objects.filter(subject="Hello"))
        self.assertEqual(len(emails), 2)
        for email in emails:
            sent = {attachment.name: attachment for attachment in email.attachments.all()}
            self.assertEqual(sorted(sent), ["agenda.pdf", "notes.txt"])
            self.assertEqual(sent["agenda.pdf"].mimetype, "application/pdf")
            with sent["agenda.pdf"].file.open("rb") as handle:
                self.assertEqual(handle.read(), b"%PDF-agenda")
        # The temporary uploads are no longer needed once post_office has its own copies.
        for upload in mail_attachments_from_tokens([agenda["token"], notes["token"]]):
            self.assertFalse(default_storage.exists(upload.key))

    def test_uploads_are_checked_against_files_already_attached(self) -> None:
        [agenda] = self._upload(SimpleUploadedFile("agenda.pdf", b"one")).json()["attachments"]

        response = self._upload(SimpleUploadedFile("agenda.pdf", b"two"), attached=[agenda["token"]])

        self.assertEqual(response.status_code, 400)
        self.assertIn("same file name", response.json()["error"])

    @patch("core.mail_attachments.MAIL_ATTACHMENTS_MAX_TOTAL_BYTES", 10)
    def test_uploads_over_the_size_limit_are_refused_without_storing(self) -> None:
        with patch("core.mail_attachments.store_uploaded_mail_attachment") as store_mock:
            response = self._upload(SimpleUploadedFile("big.bin", b"x" * 11))

        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", response.json()["error"])
        store_mock.assert_not_called()

    def test_upload_requires_send_mail_permission(self) -> None:
        session = self.client.session
        session["_freeipa_username"] = "stranger"
        session.save()
        stranger = FreeIPAUser("stranger", {"uid": ["stranger"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=stranger):
            response = self._upload(SimpleUploadedFile("agenda.pdf", b"one"))

        self.assertEqual(response.status_code, 403)

    def test_attachments_removed_by_the_cleanup_are_reported_instead_of_crashing(self) -> None:
        from core.mail_attachments import mail_attachments_from_tokens

        [agenda] = self._upload(SimpleUploadedFile("agenda.pdf", b"one")).json()["attachments"]
        [notes] = self._upload(SimpleUploadedFile("notes.txt", b"two")).json()["attachments"]
        # The page re-signs references on every render, so one can outlive its file.
        [expired] = mail_attachments_from_tokens([agenda["token"]])
        default_storage.delete(expired.key)

        response = self._post(action="send", attachment=[agenda["token"], notes["token"]])

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Email.objects.filter(subject="Hello").exists())
        self.assertEqual([attachment["name"] for attachment in self._payload(response)["attachments"]], ["notes.txt"])
        self.assertContains(response, "no longer available")

    def test_tampered_attachment_references_are_rejected(self) -> None:
        response = self._post(action="send", attachment=["not-a-valid-token"])

        self.assertFalse(Email.objects.filter(subject="Hello").exists())
        self.assertEqual(self._payload(response)["attachments"], [])
        self.assertContains(response, "attachment")

    def test_deep_link_pre_attaches_a_stored_file_without_leaking_it_into_the_template_context(self) -> None:
        from core.mail_attachments import mail_attachment_from_storage
        from core.views_utils import send_mail_url

        default_storage.save("invoices/tok/invoice.pdf", ContentFile(b"%PDF-invoice"))
        token = mail_attachment_from_storage(
            key="invoices/tok/invoice.pdf",
            name="invoice.pdf",
            mimetype="application/pdf",
        ).to_token()

        url = send_mail_url(
            to_type="manual",
            to="jim@example.com",
            template_name="",
            extra_context={"organization_name": "Acme"},
            cc="billing@example.com",
            attachment_tokens=[token],
        )
        response = self.client.get(url)

        payload = self._payload(response)
        self.assertEqual([attachment["name"] for attachment in payload["attachments"]], ["invoice.pdf"])
        cc_field = next(field for field in payload["form"]["fields"] if field["name"] == "cc")
        self.assertEqual(cc_field["value"], "billing@example.com")
        extra_context = next(field for field in payload["form"]["fields"] if field["name"] == "extra_context_json")
        self.assertNotIn("attachment", json.loads(extra_context["value"]))

    def test_daily_cleanup_removes_stale_uploads_only(self) -> None:
        from core.mail_attachments import store_uploaded_mail_attachment

        # Uploads past the reference lifetime can no longer be sent, so they are garbage.
        long_ago = timezone.now() - datetime.timedelta(seconds=settings.MAIL_ATTACHMENT_TOKEN_TTL_SECONDS + 60)
        with patch("django.core.files.storage.memory.now", return_value=long_ago):
            stale = store_uploaded_mail_attachment(SimpleUploadedFile("old.txt", b"old"))
        fresh = store_uploaded_mail_attachment(SimpleUploadedFile("new.txt", b"new"))

        call_command("send_mail_attachments_cleanup", dry_run=True)
        self.assertTrue(default_storage.exists(stale.key))

        call_command("send_mail_attachments_cleanup")

        self.assertFalse(default_storage.exists(stale.key))
        self.assertTrue(default_storage.exists(fresh.key))
