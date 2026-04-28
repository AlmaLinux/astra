from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

EXPECTED_DESCRIPTION = "Approval for a membership renewal"
EXPECTED_SUBJECT = "Membership Renewal Approved!"
EXPECTED_CONTENT = (
    "Hi there, {{ full_name }}!\n\n"
    "We are happy to share that your membership renewal request has been approved! Thank you for your continued support of the AlmaLinux OS Foundation.\n\n"
    "If you ever have any questions about AlmaLinux, or want to find out more ways you can get involved don't hesitate to ask them.\n\n"
    "If you are looking to increase your involvement in the community, there are a few different ways to do so! You can start by taking a look at the [Contribute](https://almalinux.org/contribute/) page on the website. We have a [number of SIGs](https://wiki.almalinux.org/sigs/) that are actively working on all kinds of fun stuff, or you can help by answering questions on the [forums](https://forums.almalinux.org) or [reddit](https://www.reddit.com/r/AlmaLinux/).\n\n"
    "**Get connected**\n\n"
    "If you're not already, please join our chat server at [chat.almalinux.org](https://chat.almalinux.org/). If you want to sport your very own AlmaLinux gear, head over to [shop.almalinux.org](https://shop.almalinux.org) to pick up some goodies! We've got hoodies, shirts, mugs, phone cases and much more.\n\n"
    "Thanks again, and welcome to the AlmaLinux OS Foundation.\n\n"
    "-- The AlmaLinux Team\n"
)
EXPECTED_HTML_CONTENT = (
    "<p>Hi there, {{ full_name }}!</p>\n"
    "<p>We are happy to share that your membership renewal request has been approved! Thank you for your continued support of the AlmaLinux OS Foundation.</p>\n"
    "<p>If you ever have any questions about AlmaLinux, or want to find out more ways you can get involved don't hesitate to ask them.</p>\n"
    '<p>If you are looking to increase your involvement in the community, there are a few different ways to do so! You can start by taking a look at the <a href="https://almalinux.org/contribute/">Contribute</a> page on the website. We have a <a href="https://wiki.almalinux.org/sigs/">number of SIGs</a> that are actively working on all kinds of fun stuff, or you can help by answering questions  on the <a href="https://forums.almalinux.org">forums</a> or <a href="https://www.reddit.com/r/AlmaLinux/">reddit</a>.</p>\n'
    "<p><strong>Get connected</strong></p>\n"
    '<p>If you\'re not already, please join our chat server at <a href="https://chat.almalinux.org/">chat.almalinux.org</a>. If you want to sport your very own AlmaLinux gear, head over to <a href="https://shop.almalinux.org">shop.almalinux.org</a> to pick up some goodies! We\'ve got hoodies, shirts, mugs, phone cases and much more.</p>\n'
    "<p>Thanks again, and welcome to the AlmaLinux OS Foundation.</p>\n"
    "<p><em>The AlmaLinux Team</em></p>"
)


class MembershipRenewalApprovedEmailTemplateSeedTests(TransactionTestCase):
    migrate_from = [("core", "0094_update_membership_committee_pending_requests_email_template")]
    migrate_to = [("core", "0095_create_membership_renewal_approved_email_template")]

    def setUp(self) -> None:
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        old_apps = executor.loader.project_state(self.migrate_from).apps
        old_email_template = old_apps.get_model("post_office", "EmailTemplate")
        old_email_template.objects.filter(name="membership-renewal-approved").delete()
        old_email_template.objects.create(
            name="membership-renewal-approved",
            description="",
            subject="stale subject",
            content="stale content",
            html_content="stale html",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_membership_renewal_approved_template_is_seeded(self) -> None:
        email_template = self.apps.get_model("post_office", "EmailTemplate")
        template = email_template.objects.filter(name="membership-renewal-approved").first()

        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(template.description, EXPECTED_DESCRIPTION)
        self.assertEqual(template.subject, EXPECTED_SUBJECT)
        self.assertEqual(template.content, EXPECTED_CONTENT)
        self.assertEqual(template.html_content, EXPECTED_HTML_CONTENT)
