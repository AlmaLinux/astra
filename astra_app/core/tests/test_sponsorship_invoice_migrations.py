from importlib import import_module
from typing import override

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.template import Context, Template
from django.test import TransactionTestCase

from core.tests.utils_test_data import migrate_to_latest


class OrganizationInvoiceTokenBackfillMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0101_membershiptype_annual_dues_usd")]
    migrate_to = [("core", "0102_organization_billing_notes_invoice_token")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        old_apps = executor.loader.project_state(self.migrate_from).apps
        organization_model = old_apps.get_model("core", "Organization")
        for index in range(3):
            organization_model.objects.create(name=f"Org {index}", status="active", representative=f"rep{index}")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_existing_organizations_get_distinct_tokens(self) -> None:
        tokens = list(self.apps.get_model("core", "Organization").objects.values_list("invoice_token", flat=True))

        self.assertEqual(len(tokens), 3)
        self.assertTrue(all(tokens))
        self.assertEqual(len(set(tokens)), 3)


class SponsorshipReminderInvoiceBlockMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0102_organization_billing_notes_invoice_token")]
    migrate_to = [("core", "0103_sponsorship_invoice_email_blocks")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        old_apps = executor.loader.project_state(self.migrate_from).apps
        email_template = old_apps.get_model("post_office", "EmailTemplate")
        email_template.objects.filter(
            name__in=["organization-sponsorship-expiring-soon", "organization-sponsorship-expired"]
        ).delete()
        # Seeded wording: the block goes in before the "already extended" paragraph.
        email_template.objects.create(
            name="organization-sponsorship-expiring-soon",
            subject="Expiring",
            content="Extend here.\n\nIf you have already extended, you can safely ignore this email.\n",
            html_content="<p>Extend here.</p><p>If you have already extended, you can safely ignore this email.</p>",
        )
        # Operator-edited wording without the anchor: the block is appended.
        email_template.objects.create(
            name="organization-sponsorship-expired",
            subject="Expired",
            content="Custom text.",
            html_content="<p>Custom html.</p>",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_invoice_block_is_added_to_both_reminder_templates(self) -> None:
        email_template = self.apps.get_model("post_office", "EmailTemplate")
        expiring = email_template.objects.get(name="organization-sponsorship-expiring-soon")
        expired = email_template.objects.get(name="organization-sponsorship-expired")

        for template in (expiring, expired):
            with self.subTest(template=template.name):
                self.assertIn("{% if invoice_url %}", template.content)
                self.assertIn("{{ invoice_url }}", template.content)
                self.assertIn("{% if invoice_url %}", template.html_content)
                self.assertIn('href="{{ invoice_url }}"', template.html_content)

        self.assertLess(expiring.html_content.index("invoice_url"), expiring.html_content.index("If you have already"))
        self.assertLess(expiring.content.index("invoice_url"), expiring.content.index("If you have already"))
        self.assertTrue(expired.html_content.startswith("<p>Custom html.</p>"))
        self.assertTrue(expired.content.startswith("Custom text."))


class SponsorshipWelcomeInvoiceBlockMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0102_organization_billing_notes_invoice_token")]
    migrate_to = [("core", "0103_sponsorship_invoice_email_blocks")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        old_apps = executor.loader.project_state(self.migrate_from).apps
        email_template = old_apps.get_model("post_office", "EmailTemplate")
        category_model = old_apps.get_model("core", "MembershipTypeCategory")
        membership_type = old_apps.get_model("core", "MembershipType")
        email_template.objects.filter(name__in=["welcome-sponsor", "welcome-sponsor-edited", "welcome-individual"]).delete()

        sponsorship, _ = category_model.objects.update_or_create(name="sponsorship", defaults={"is_organization": True})
        individual, _ = category_model.objects.update_or_create(name="individual", defaults={"is_individual": True})
        for code, category, name, content, html in (
            ("gold", sponsorship, "welcome-sponsor", "Welcome!\n\nThanks again.\n", "<p>Welcome!</p>\n<p>Thanks again.</p>"),
            ("ruby", sponsorship, "welcome-sponsor-edited", "Custom text.", "<p>Custom html.</p>"),
            ("individual", individual, "welcome-individual", "Hi\n\nThanks again.\n", "<p>Hi</p>\n<p>Thanks again.</p>"),
        ):
            template = email_template.objects.create(name=name, subject="Welcome", content=content, html_content=html)
            membership_type.objects.update_or_create(
                code=code,
                defaults={"name": code, "category": category, "acceptance_template": template},
            )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_invoice_block_is_added_to_organization_welcome_templates_only(self) -> None:
        email_template = self.apps.get_model("post_office", "EmailTemplate")
        sponsor = email_template.objects.get(name="welcome-sponsor")
        edited = email_template.objects.get(name="welcome-sponsor-edited")
        individual = email_template.objects.get(name="welcome-individual")

        for template in (sponsor, edited):
            with self.subTest(template=template.name):
                self.assertIn("{% if invoice_url %}", template.content)
                self.assertIn('href="{{ invoice_url }}"', template.html_content)
        self.assertLess(sponsor.content.index("invoice_url"), sponsor.content.index("Thanks again"))
        self.assertLess(sponsor.html_content.index("invoice_url"), sponsor.html_content.index("Thanks again"))
        self.assertTrue(edited.html_content.startswith("<p>Custom html.</p>"))
        self.assertNotIn("invoice_url", individual.content + individual.html_content)


class RenewalApprovedInvoiceBlockMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0112_membership_created_at_default")]
    migrate_to = [("core", "0113_renewal_approved_email_invoice_block")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        old_apps = executor.loader.project_state(self.migrate_from).apps
        email_template = old_apps.get_model("post_office", "EmailTemplate")
        email_template.objects.filter(name="membership-renewal-approved").delete()
        email_template.objects.create(
            name="membership-renewal-approved",
            subject="Renewed",
            content="Renewed!\n\nThanks again.\n",
            html_content="<p>Renewed!</p>\n<p>Thanks again.</p>",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_invoice_block_is_added_before_the_closing_note(self) -> None:
        template = self.apps.get_model("post_office", "EmailTemplate").objects.get(name="membership-renewal-approved")

        # Individual renewals share the template; the block only shows when an invoice is attached.
        self.assertIn("{% if invoice_url %}", template.content)
        self.assertIn('href="{{ invoice_url }}"', template.html_content)
        self.assertLess(template.content.index("invoice_url"), template.content.index("Thanks again"))
        self.assertLess(template.html_content.index("invoice_url"), template.html_content.index("Thanks again"))


class SponsorshipRenewalApprovedTemplateMigrationTests(TransactionTestCase):
    migrate_from = [("core", "0123_email_layout_custom_footers")]
    migrate_to = [("core", "0124_organization_sponsorship_renewal_approved_email_template")]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(migrate_to_latest)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        old_apps = executor.loader.project_state(self.migrate_from).apps
        email_template = old_apps.get_model("post_office", "EmailTemplate")
        email_template.objects.filter(name="membership-renewal-approved").delete()
        invoice_block = import_module("core.migrations.0113_renewal_approved_email_invoice_block")
        email_template.objects.create(
            name="membership-renewal-approved",
            subject="Renewed",
            content=f"Renewed, edited by staff!\n\n{invoice_block.TEXT_BLOCK}Thanks again.\n",
            # Staff reworded the block in the HTML part.
            html_content=(
                "<p>Renewed, edited by staff!</p>\n"
                '{% if invoice_url %}<p>Your invoice: <a href="{{ invoice_url }}">{{ invoice_number }}</a></p>\n'
                "{% endif %}<p>Thanks again.</p>"
            ),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_individual_renewal_template_drops_the_invoice_block_and_keeps_edits(self) -> None:
        template = self.apps.get_model("post_office", "EmailTemplate").objects.get(name="membership-renewal-approved")

        # Sponsors now get their own renewal email, so this one never carries an invoice.
        self.assertEqual(template.content, "Renewed, edited by staff!\n\nThanks again.\n")
        self.assertEqual(template.html_content, "<p>Renewed, edited by staff!</p>\n<p>Thanks again.</p>")

    def test_sponsorship_renewal_template_is_seeded_for_organizations(self) -> None:
        template = self.apps.get_model("post_office", "EmailTemplate").objects.get(
            name="organization-sponsorship-renewal-approved"
        )
        context = Context({"organization_name": "CERN", "membership_type": "Gold Sponsor"})

        self.assertIn("CERN", Template(template.subject).render(context))
        self.assertIn("CERN", Template(template.content).render(context))
        self.assertTrue(template.html_content.startswith('{% extends "email-layout" %}'))
        self.assertIn("You're receiving this as a contact for", template.html_content)
        self.assertNotIn("invoice", (template.content + template.html_content).casefold())
