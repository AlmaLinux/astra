
from datetime import UTC, datetime
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import clear_url_caches, reverse, set_script_prefix

from core.freeipa.user import FreeIPAUser
from core.models import FreeIPAPermissionGrant
from core.permissions import ASTRA_ADD_SEND_MAIL


class MailImagesUiTests(TestCase):
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

    def _reviewer(self) -> FreeIPAUser:
        return FreeIPAUser(
            "reviewer",
            {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]},
        )

    def test_requires_permission(self) -> None:
        self._login_as_freeipa_user("alice")
        alice = FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": []})

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=alice):
            resp = self.client.get(reverse("email-images"))

        self.assertEqual(resp.status_code, 302)

    def test_lists_images_with_preview_and_url(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer()

        dt = datetime(2026, 1, 1, tzinfo=UTC)

        def _listdir(path: str) -> tuple[list[str], list[str]]:
            if path.rstrip("/") == "mail-images":
                return (["sub"], ["a.png"])
            if path.rstrip("/") == "mail-images/sub":
                return ([], ["b.jpg"])
            raise AssertionError(f"Unexpected listdir path {path!r}")

        def _url(key: str) -> str:
            return f"https://cdn.example/{key}"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.views_mail_images.default_storage.listdir", side_effect=_listdir),
            patch("core.views_mail_images.default_storage.url", side_effect=_url),
            patch("core.views_mail_images.default_storage.size", return_value=123),
            patch("core.views_mail_images.default_storage.get_modified_time", return_value=dt),
        ):
            resp = self.client.get(reverse("email-images"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Images")
        self.assertContains(resp, "a.png")
        self.assertContains(resp, "sub/b.jpg")
        self.assertContains(resp, "https://cdn.example/mail-images/a.png")
        self.assertNotContains(resp, "{% include 'core/_modal_confirm.html'")
        self.assertNotContains(resp, "{% with modal_id=")

    def test_images_page_get_renders_vue_shell_contract(self) -> None:
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            resp = self.client.get(reverse("email-images"))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-mail-images-root=""')
        self.assertContains(resp, f'data-mail-images-api-url="{reverse("api-email-images-detail")}"')
        self.assertContains(resp, f'data-mail-images-submit-url="{reverse("email-images")}"')
        self.assertNotContains(resp, "How To Use Images in Email Templates")
        self.assertContains(resp, "Loading images...")

    def test_images_page_get_renders_prefixed_named_api_route(self) -> None:
        self._login_as_freeipa_user("reviewer")
        original_force_script_name = settings.FORCE_SCRIPT_NAME

        clear_url_caches()
        settings.FORCE_SCRIPT_NAME = "/prefix"
        set_script_prefix("/prefix/")
        self.addCleanup(set_script_prefix, "/")

        def restore_force_script_name() -> None:
            settings.FORCE_SCRIPT_NAME = original_force_script_name
            clear_url_caches()

        self.addCleanup(restore_force_script_name)

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()):
            resp = self.client.get("/email-tools/images/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'data-mail-images-api-url="{reverse("api-email-images-detail")}"')

    def test_images_detail_api_returns_data_only_payload(self) -> None:
        self._login_as_freeipa_user("reviewer")
        dt = datetime(2026, 1, 1, tzinfo=UTC)

        def _listdir(path: str) -> tuple[list[str], list[str]]:
            if path.rstrip("/") == "mail-images":
                return (["sub"], ["a.png"])
            if path.rstrip("/") == "mail-images/sub":
                return ([], ["b.jpg"])
            raise AssertionError(f"Unexpected listdir path {path!r}")

        def _url(key: str) -> str:
            return f"https://cdn.example/{key}"

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer()),
            patch("core.views_mail_images.default_storage.listdir", side_effect=_listdir),
            patch("core.views_mail_images.default_storage.url", side_effect=_url),
            patch("core.views_mail_images.default_storage.size", return_value=123),
            patch("core.views_mail_images.default_storage.get_modified_time", return_value=dt),
        ):
            resp = self.client.get("/api/v1/email-tools/images/detail")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["mail_images_prefix"], "mail-images/")
        self.assertEqual(payload["example_image_url"], "https://cdn.example/mail-images/path/to/image.png")
        self.assertEqual(
            payload["images"],
            [
                {
                    "key": "mail-images/a.png",
                    "relative_key": "a.png",
                    "url": "https://cdn.example/mail-images/a.png",
                    "size_bytes": 123,
                    "modified_at": "2026-01-01T00:00:00+00:00",
                },
                {
                    "key": "mail-images/sub/b.jpg",
                    "relative_key": "sub/b.jpg",
                    "url": "https://cdn.example/mail-images/sub/b.jpg",
                    "size_bytes": 123,
                    "modified_at": "2026-01-01T00:00:00+00:00",
                },
            ],
        )
        self.assertNotIn("delete_url", payload)
        self.assertNotIn("upload_url", payload)
        self.assertNotIn("modified_display", payload)

    def test_upload_and_delete(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser("reviewer", {"uid": ["reviewer"], "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP]})

        upload = SimpleUploadedFile("logo.png", b"pngbytes", content_type="image/png")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch("core.views_mail_images.default_storage.exists", return_value=False),
            patch("core.views_mail_images.default_storage.save", return_value="mail-images/logo.png") as save,
            patch("core.views_mail_images.default_storage.delete") as delete,
            patch("core.views_mail_images.default_storage.listdir", return_value=([], [])),
        ):
            upload_resp = self.client.post(
                reverse("email-images"),
                data={"action": "upload", "upload_path": "", "files": upload},
                follow=True,
            )
            delete_resp = self.client.post(
                reverse("email-images"),
                data={"action": "delete", "key": "mail-images/logo.png"},
                follow=True,
            )

        self.assertEqual(upload_resp.status_code, 200)
        save.assert_called_once()
        saved_key = save.call_args.args[0]
        self.assertTrue(saved_key.endswith("mail-images/logo.png"))

        self.assertEqual(delete_resp.status_code, 200)
        delete.assert_called_once_with("mail-images/logo.png")
