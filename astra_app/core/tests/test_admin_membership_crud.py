import datetime
import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from core.freeipa.user import FreeIPAUser
from core.tests.utils_test_data import ensure_core_categories


class AdminMembershipCRUDTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

    def _login_as_freeipa_admin(self, username: str = "alice") -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _freeipa_user_get(self, username: str, *args, **kwargs) -> FreeIPAUser | None:
        normalized_username = str(username or "").strip()
        if normalized_username == "alice":
            return FreeIPAUser("alice", {"uid": ["alice"], "memberof_group": ["admins"]})
        if normalized_username == "bob":
            return FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "mail": ["bob@example.org"],
                    "cn": ["Bob Example"],
                    "memberof_group": [],
                },
            )
        if normalized_username == "carol":
            return FreeIPAUser(
                "carol",
                {
                    "uid": ["carol"],
                    "mail": ["carol@example.org"],
                    "cn": ["Carol Example"],
                    "memberof_group": [],
                },
            )
        return None

    def _create_membership_type(
        self,
        *,
        code: str,
        name: str,
        group_cn: str,
        category_id: str,
        sort_order: int,
    ):
        from core.models import MembershipType

        membership_type, _created = MembershipType.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "group_cn": group_cn,
                "category_id": category_id,
                "sort_order": sort_order,
                "enabled": True,
            },
        )
        return membership_type

    def _create_organization(self, *, name: str = "Example Org", representative: str = "carol"):
        from core.models import Organization

        return Organization.objects.create(
            name=name,
            representative=representative,
            country_code="US",
            business_contact_name="Business Contact",
            business_contact_email="biz@example.org",
            pr_marketing_contact_name="PR Contact",
            pr_marketing_contact_email="pr@example.org",
            technical_contact_name="Tech Contact",
            technical_contact_email="tech@example.org",
            website="https://example.org/",
            website_logo="https://example.org/logo.svg",
        )

    def test_membership_admin_is_registered_and_added_to_membership_config_group(self) -> None:
        from core.models import Membership

        membership_config = next(
            group for group in settings.JAZZMIN_SETTINGS["model_groups"] if group["name"] == "Membership Config"
        )

        self.assertIn(Membership, admin.site._registry)
        self.assertIn("core.membership", membership_config["models"])

    def test_membership_admin_changelist_is_reachable_for_authorized_admin(self) -> None:
        membership_type = self._create_membership_type(
            code="gold-sponsor",
            name="Gold Sponsor",
            group_cn="alma-gold-sponsor",
            category_id="sponsorship",
            sort_order=5,
        )
        organization = self._create_organization(name="Reachable Org")

        from core.models import MembershipLog

        MembershipLog.create_for_approval_at(
            actor_username="reviewer",
            membership_type=membership_type,
            approved_at=datetime.datetime(2026, 1, 2, 0, 0, tzinfo=datetime.UTC),
            target_organization=organization,
        )

        self._login_as_freeipa_admin("alice")

        with patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get):
            response = self.client.get(reverse("admin:core_membership_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reachable Org")
        self.assertContains(response, "Gold Sponsor")

    def test_admin_can_create_user_membership_and_logs_side_effects(self) -> None:
        from core.models import Membership, MembershipLog, MembershipType

        MembershipType.objects.update_or_create(
            code="individual-basic",
            defaults={
                "name": "Individual Basic",
                "group_cn": "alma-individual-basic",
                "category_id": "individual",
                "sort_order": 10,
                "enabled": True,
            },
        )

        self._login_as_freeipa_admin("alice")

        with (
            patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get),
            patch.object(FreeIPAUser, "add_to_group", autospec=True) as add_to_group,
            patch("core.membership_request_workflow.missing_required_agreements_for_user_in_group", return_value=[]),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("admin:core_membership_add"),
                    data={
                        "target_kind": "user",
                        "target_username": "bob",
                        "target_organization": "",
                        "membership_type": "individual-basic",
                        "starts_on": "2026-01-10",
                        "expires_on": "2026-04-15",
                        "_save": "Save",
                    },
                    follow=False,
                )

        self.assertEqual(response.status_code, 302)

        membership = Membership.objects.get(target_username="bob", membership_type_id="individual-basic")
        self.assertEqual(membership.created_at, datetime.datetime(2026, 1, 10, 0, 0, tzinfo=datetime.UTC))
        self.assertEqual(membership.expires_at, datetime.datetime(2026, 4, 15, 23, 59, 59, tzinfo=datetime.UTC))

        approved_log = MembershipLog.objects.get(
            target_username="bob",
            membership_type_id="individual-basic",
            action=MembershipLog.Action.approved,
        )
        self.assertEqual(approved_log.created_at, datetime.datetime(2026, 1, 10, 0, 0, tzinfo=datetime.UTC))

        expiry_log = MembershipLog.objects.get(
            target_username="bob",
            membership_type_id="individual-basic",
            action=MembershipLog.Action.expiry_changed,
        )
        self.assertEqual(expiry_log.expires_at, datetime.datetime(2026, 4, 15, 23, 59, 59, tzinfo=datetime.UTC))

        add_to_group.assert_called_once()

        ContentType.objects.clear_cache()
        ContentType.objects.get_for_model(Membership)

        from django.contrib.auth import get_user_model

        shadow_user = get_user_model().objects.get(username="alice")
        entry = LogEntry.objects.order_by("-action_time").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.user_id, shadow_user.pk)
        self.assertEqual(entry.action_flag, ADDITION)
        self.assertEqual(entry.object_id, str(membership.pk))

    def test_membership_admin_add_renders_searchable_target_username_control(self) -> None:
        self._create_membership_type(
            code="individual-basic",
            name="Individual Basic",
            group_cn="alma-individual-basic",
            category_id="individual",
            sort_order=10,
        )
        self._login_as_freeipa_admin("alice")

        with patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get):
            response = self.client.get(reverse("admin:core_membership_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="target_username"')
        self.assertContains(response, 'alx-select2')
        self.assertContains(response, 'data-ajax-url="../target-user-search/"')
        self.assertContains(response, "admin/js/vendor/select2/select2.full.js")
        self.assertContains(response, "core/js/admin_select2_ajax_init.js")

        html = response.content.decode("utf-8")
        self.assertLess(
            html.index("admin/js/vendor/select2/select2.full.js"),
            html.index("core/js/admin_select2_ajax_init.js"),
        )

    def test_membership_admin_select2_init_script_uses_django_admin_jquery_namespace(self) -> None:
        static_path = finders.find("core/js/admin_select2_ajax_init.js")

        self.assertIsNotNone(static_path)
        script = Path(static_path).read_text(encoding="utf-8")

        self.assertIn("window.django && window.django.jQuery", script)

    def test_membership_admin_target_user_search_returns_matching_freeipa_users(self) -> None:
        self._login_as_freeipa_admin("alice")
        bob = FreeIPAUser("bob", {"uid": ["bob"], "displayname": ["Bob Example"], "memberof_group": []})
        bobby = FreeIPAUser("bobby", {"uid": ["bobby"], "memberof_group": []})
        helper_calls: list[dict[str, object]] = []

        def fake_search_freeipa_users(
            *,
            query: str,
            limit: int,
            exclude_usernames=None,
        ) -> list[FreeIPAUser]:
            helper_calls.append(
                {
                    "query": query,
                    "limit": limit,
                    "exclude_usernames": exclude_usernames,
                }
            )
            return [bob, bobby]

        with patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get):
            with patch("core.admin.search_freeipa_users", side_effect=fake_search_freeipa_users):
                response = self.client.get(reverse("admin:core_membership_target_user_search"), {"q": "bo"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "results": [
                    {"id": "bob", "text": "Bob Example (bob)"},
                    {"id": "bobby", "text": "bobby"},
                ]
            },
        )
        self.assertEqual(
            helper_calls,
            [
                {
                    "query": "bo",
                    "limit": 20,
                    "exclude_usernames": None,
                }
            ],
        )

    def test_membership_admin_target_user_search_requires_add_permission(self) -> None:
        from core.models import Membership

        self._login_as_freeipa_admin("alice")
        membership_admin = admin.site._registry[Membership]

        with (
            patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get),
            patch.object(membership_admin, "has_add_permission", return_value=False),
            patch.object(membership_admin, "has_change_permission", return_value=True),
        ):
            response = self.client.get(reverse("admin:core_membership_target_user_search"), {"q": "bo"})

        self.assertEqual(response.status_code, 403)

    def test_membership_admin_change_keeps_target_username_as_disabled_non_search_input(self) -> None:
        from core.models import Membership, MembershipLog

        membership_type = self._create_membership_type(
            code="individual-basic",
            name="Individual Basic",
            group_cn="alma-individual-basic",
            category_id="individual",
            sort_order=10,
        )
        MembershipLog.create_for_approval_at(
            actor_username="reviewer",
            membership_type=membership_type,
            approved_at=datetime.datetime(2026, 1, 10, 0, 0, tzinfo=datetime.UTC),
            target_username="bob",
        )
        membership = Membership.objects.get(target_username="bob", membership_type=membership_type)
        self._login_as_freeipa_admin("alice")

        with patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get):
            response = self.client.get(reverse("admin:core_membership_change", args=[membership.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="target_username"')
        self.assertContains(response, 'value="bob"')
        self.assertRegex(
            response.content.decode("utf-8"),
            re.compile(r'<input[^>]*name="target_username"[^>]*disabled[^>]*>'),
        )
        self.assertNotContains(response, 'name="target_username"></select>')
        self.assertNotContains(response, 'name="target_username" class="alx-select2"')

    def test_admin_can_edit_membership_dates_and_logs_audit_entries(self) -> None:
        from core.models import Membership, MembershipLog, MembershipType

        membership_type, _created = MembershipType.objects.update_or_create(
            code="individual-basic",
            defaults={
                "name": "Individual Basic",
                "group_cn": "alma-individual-basic",
                "category_id": "individual",
                "sort_order": 10,
                "enabled": True,
            },
        )

        original_start = datetime.datetime(2025, 1, 1, 0, 0, tzinfo=datetime.UTC)
        original_end = datetime.datetime(2026, 7, 1, 23, 59, 59, tzinfo=datetime.UTC)
        approved_log = MembershipLog.create_for_approval_at(
            actor_username="reviewer",
            membership_type=membership_type,
            approved_at=original_start,
            target_username="bob",
        )
        MembershipLog.objects.filter(pk=approved_log.pk).update(created_at=original_start, expires_at=original_end)
        membership = Membership.objects.get(target_username="bob", membership_type=membership_type)
        Membership.objects.filter(pk=membership.pk).update(created_at=original_start, expires_at=original_end)
        membership.refresh_from_db()

        self._login_as_freeipa_admin("alice")

        with patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get):
            response = self.client.post(
                reverse("admin:core_membership_change", args=[membership.pk]),
                data={
                    "target_kind": "user",
                    "target_username": "bob",
                    "target_organization": "",
                    "membership_type": "individual-basic",
                    "starts_on": "2025-02-05",
                    "expires_on": "2026-06-20",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)

        membership.refresh_from_db()
        self.assertEqual(membership.created_at, datetime.datetime(2025, 2, 5, 0, 0, tzinfo=datetime.UTC))
        self.assertEqual(membership.expires_at, datetime.datetime(2026, 6, 20, 23, 59, 59, tzinfo=datetime.UTC))

        approved_log.refresh_from_db()
        self.assertEqual(approved_log.created_at, datetime.datetime(2025, 2, 5, 0, 0, tzinfo=datetime.UTC))

        expiry_log = MembershipLog.objects.filter(
            target_username="bob",
            membership_type=membership_type,
            action=MembershipLog.Action.expiry_changed,
        ).latest("created_at")
        self.assertEqual(expiry_log.expires_at, datetime.datetime(2026, 6, 20, 23, 59, 59, tzinfo=datetime.UTC))

        ContentType.objects.clear_cache()
        ContentType.objects.get_for_model(Membership)
        entry = LogEntry.objects.order_by("-action_time").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.action_flag, CHANGE)
        self.assertEqual(entry.object_id, str(membership.pk))

    def test_admin_can_create_sponsorship_membership_and_syncs_representative(self) -> None:
        from core.models import Membership, MembershipLog, MembershipType, Organization

        membership_type, _created = MembershipType.objects.update_or_create(
            code="gold-sponsor",
            defaults={
                "name": "Gold Sponsor",
                "group_cn": "alma-gold-sponsor",
                "category_id": "sponsorship",
                "sort_order": 5,
                "enabled": True,
            },
        )
        organization = Organization.objects.create(
            name="Example Org",
            representative="carol",
            country_code="US",
            business_contact_name="Business Contact",
            business_contact_email="biz@example.org",
            pr_marketing_contact_name="PR Contact",
            pr_marketing_contact_email="pr@example.org",
            technical_contact_name="Tech Contact",
            technical_contact_email="tech@example.org",
            website="https://example.org/",
            website_logo="https://example.org/logo.svg",
        )

        self._login_as_freeipa_admin("alice")

        with (
            patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get),
            patch(
                "core.membership_request_workflow.sync_organization_representative_membership_groups"
            ) as sync_groups,
            patch("core.membership_request_workflow.missing_required_agreements_for_user_in_group", return_value=[]),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("admin:core_membership_add"),
                    data={
                        "target_kind": "organization",
                        "target_username": "",
                        "target_organization": str(organization.pk),
                        "membership_type": membership_type.pk,
                        "starts_on": "2026-02-01",
                        "expires_on": "2026-08-31",
                        "_save": "Save",
                    },
                    follow=False,
                )

        self.assertEqual(response.status_code, 302)

        membership = Membership.objects.get(target_organization=organization, membership_type=membership_type)
        self.assertEqual(membership.created_at, datetime.datetime(2026, 2, 1, 0, 0, tzinfo=datetime.UTC))
        self.assertEqual(membership.expires_at, datetime.datetime(2026, 8, 31, 23, 59, 59, tzinfo=datetime.UTC))

        self.assertTrue(
            MembershipLog.objects.filter(
                target_organization=organization,
                membership_type=membership_type,
                action=MembershipLog.Action.approved,
            ).exists()
        )
        self.assertTrue(
            MembershipLog.objects.filter(
                target_organization=organization,
                membership_type=membership_type,
                action=MembershipLog.Action.expiry_changed,
            ).exists()
        )

        sync_groups.assert_called_once()

    def test_admin_can_edit_sponsorship_membership_dates_and_logs_audit_entries(self) -> None:
        from core.models import Membership, MembershipLog

        membership_type = self._create_membership_type(
            code="gold-sponsor",
            name="Gold Sponsor",
            group_cn="alma-gold-sponsor",
            category_id="sponsorship",
            sort_order=5,
        )
        organization = self._create_organization(name="Date Edit Org")

        original_start = datetime.datetime(2025, 3, 1, 0, 0, tzinfo=datetime.UTC)
        original_end = datetime.datetime(2026, 9, 30, 23, 59, 59, tzinfo=datetime.UTC)
        approved_log = MembershipLog.create_for_approval_at(
            actor_username="reviewer",
            membership_type=membership_type,
            approved_at=original_start,
            target_organization=organization,
        )
        MembershipLog.objects.filter(pk=approved_log.pk).update(created_at=original_start, expires_at=original_end)
        membership = Membership.objects.get(target_organization=organization, membership_type=membership_type)
        original_membership_pk = membership.pk
        Membership.objects.filter(pk=membership.pk).update(created_at=original_start, expires_at=original_end)
        membership.refresh_from_db()

        self._login_as_freeipa_admin("alice")

        with patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get):
            response = self.client.post(
                reverse("admin:core_membership_change", args=[membership.pk]),
                data={
                    "target_kind": "organization",
                    "target_username": "",
                    "target_organization": str(organization.pk),
                    "membership_type": membership_type.pk,
                    "starts_on": "2025-04-10",
                    "expires_on": "2026-10-20",
                    "_save": "Save",
                },
                follow=False,
            )

        self.assertEqual(response.status_code, 302)

        membership = Membership.objects.get(target_organization=organization, membership_type=membership_type)
        self.assertEqual(membership.created_at, datetime.datetime(2025, 4, 10, 0, 0, tzinfo=datetime.UTC))
        self.assertEqual(membership.expires_at, datetime.datetime(2026, 10, 20, 23, 59, 59, tzinfo=datetime.UTC))
        self.assertFalse(Membership.objects.filter(pk=original_membership_pk).exists())

        approved_log.refresh_from_db()
        self.assertEqual(approved_log.created_at, datetime.datetime(2025, 4, 10, 0, 0, tzinfo=datetime.UTC))

        expiry_log = MembershipLog.objects.filter(
            target_organization=organization,
            membership_type=membership_type,
            action=MembershipLog.Action.expiry_changed,
        ).latest("created_at")
        self.assertEqual(expiry_log.expires_at, datetime.datetime(2026, 10, 20, 23, 59, 59, tzinfo=datetime.UTC))

        ContentType.objects.clear_cache()
        ContentType.objects.get_for_model(Membership)
        entry = LogEntry.objects.order_by("-action_time").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.action_flag, CHANGE)
        self.assertEqual(entry.object_id, str(membership.pk))

    def test_admin_add_surfaces_workflow_validation_error_as_non_field_error(self) -> None:
        membership_type = self._create_membership_type(
            code="gold-sponsor",
            name="Gold Sponsor",
            group_cn="alma-gold-sponsor",
            category_id="sponsorship",
            sort_order=5,
        )
        organization = self._create_organization()

        self._login_as_freeipa_admin("alice")

        with (
            patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get),
            patch(
                "core.membership_request_workflow.missing_required_agreements_for_user_in_group",
                return_value=["coc"],
            ),
        ):
            response = self.client.post(
                reverse("admin:core_membership_add"),
                data={
                    "target_kind": "organization",
                    "target_username": "",
                    "target_organization": str(organization.pk),
                    "membership_type": membership_type.pk,
                    "starts_on": "2026-02-01",
                    "expires_on": "2026-08-31",
                    "_save": "Save",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Representative must sign required agreements before approval: coc")
        admin_form = response.context["adminform"].form
        self.assertEqual(
            admin_form.non_field_errors(),
            ["Representative must sign required agreements before approval: coc"],
        )

    def test_admin_change_surfaces_workflow_validation_error_as_non_field_error(self) -> None:
        from core.models import Membership, MembershipLog

        membership_type = self._create_membership_type(
            code="individual-basic",
            name="Individual Basic",
            group_cn="alma-individual-basic",
            category_id="individual",
            sort_order=10,
        )

        starts_at = datetime.datetime(2025, 1, 1, 0, 0, tzinfo=datetime.UTC)
        approved_log = MembershipLog.create_for_approval_at(
            actor_username="reviewer",
            membership_type=membership_type,
            approved_at=starts_at,
            target_username="bob",
        )
        membership = Membership.objects.get(target_username="bob", membership_type=membership_type)
        MembershipLog.objects.filter(pk=approved_log.pk).delete()

        self._login_as_freeipa_admin("alice")

        with patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get):
            response = self.client.post(
                reverse("admin:core_membership_change", args=[membership.pk]),
                data={
                    "target_kind": "user",
                    "target_username": "bob",
                    "target_organization": "",
                    "membership_type": membership_type.pk,
                    "starts_on": "2025-02-05",
                    "expires_on": "2026-06-20",
                    "_save": "Save",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unable to locate the approval log for the current membership term")
        admin_form = response.context["adminform"].form
        self.assertEqual(
            admin_form.non_field_errors(),
            ["Unable to locate the approval log for the current membership term"],
        )

    def test_admin_add_allows_same_category_org_replacement_through_workflow(self) -> None:
        from core.models import Membership, MembershipLog

        old_membership_type = self._create_membership_type(
            code="silver-sponsor",
            name="Silver Sponsor",
            group_cn="alma-silver-sponsor",
            category_id="sponsorship",
            sort_order=4,
        )
        new_membership_type = self._create_membership_type(
            code="gold-sponsor",
            name="Gold Sponsor",
            group_cn="alma-gold-sponsor",
            category_id="sponsorship",
            sort_order=5,
        )
        organization = self._create_organization(name="Replacement Org")
        MembershipLog.create_for_approval_at(
            actor_username="reviewer",
            membership_type=old_membership_type,
            approved_at=datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.UTC),
            target_organization=organization,
        )

        self._login_as_freeipa_admin("alice")

        with (
            patch.object(FreeIPAUser, "get", side_effect=self._freeipa_user_get),
            patch(
                "core.membership_request_workflow.sync_organization_representative_membership_groups"
            ) as sync_groups,
            patch("core.membership_request_workflow.missing_required_agreements_for_user_in_group", return_value=[]),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("admin:core_membership_add"),
                    data={
                        "target_kind": "organization",
                        "target_username": "",
                        "target_organization": str(organization.pk),
                        "membership_type": new_membership_type.pk,
                        "starts_on": "2026-02-01",
                        "expires_on": "2026-08-31",
                        "_save": "Save",
                    },
                )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Membership.objects.filter(target_organization=organization, membership_type=old_membership_type).exists()
        )
        replacement = Membership.objects.get(target_organization=organization, membership_type=new_membership_type)
        self.assertEqual(replacement.created_at, datetime.datetime(2026, 2, 1, 0, 0, tzinfo=datetime.UTC))
        self.assertEqual(replacement.expires_at, datetime.datetime(2026, 8, 31, 23, 59, 59, tzinfo=datetime.UTC))
        self.assertTrue(
            MembershipLog.objects.filter(
                target_organization=organization,
                membership_type=new_membership_type,
                action=MembershipLog.Action.approved,
            ).exists()
        )
        sync_groups.assert_called_once()