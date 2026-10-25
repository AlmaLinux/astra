import datetime
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from post_office.models import Email, EmailTemplate

from core.freeipa.user import FreeIPAUser
from core.models import Membership, MembershipLog, MembershipRequest, MembershipType, Note
from core.sponsorship_invoices import InvoiceTerm
from core.tests.test_sponsorship_invoices import SponsorshipInvoiceTestMixin


class MembershipDuesTestMixin(SponsorshipInvoiceTestMixin):
    def setUp(self) -> None:
        super().setUp()
        self.ruby, _ = MembershipType.objects.update_or_create(
            code="ruby",
            defaults={
                "name": "Ruby Sponsor Member",
                "group_cn": "almalinux-ruby",
                "category_id": "sponsorship",
                "sort_order": 1,
                "enabled": True,
                "annual_dues_usd": 5000,
            },
        )
        self.rep = FreeIPAUser("rep1", {"uid": ["rep1"], "mail": ["rep1@example.com"], "memberof_group": []})
        for target, kwargs in (
            ("core.freeipa.user.FreeIPAUser.get", {"return_value": self.rep}),
            ("core.freeipa.user.FreeIPAUser.add_to_group", {"autospec": True}),
            ("core.freeipa.user.FreeIPAUser.remove_from_group", {"autospec": True}),
            ("core.sponsorship_invoices.render_invoice_pdf", {"return_value": b"%PDF-test"}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _active_gold(self, *, dues_usd: int | None = None, days: int = 30) -> Membership:
        from core.membership_request_workflow import create_approved_membership_request

        # Like every real membership, this one comes from an approved request.
        membership_request = create_approved_membership_request(
            actor_username="committee",
            membership_type=self.gold,
            decided_at=timezone.now(),
            target_organization=self.organization,
        )
        MembershipLog.objects.create(
            membership_request=membership_request,
            actor_username="committee",
            target_organization=self.organization,
            membership_type=self.gold,
            action=MembershipLog.Action.approved,
            expires_at=timezone.now() + datetime.timedelta(days=days),
            dues_usd=dues_usd,
        )
        from core.membership_log_side_effects import apply_membership_log_side_effects

        apply_membership_log_side_effects(log=MembershipLog.objects.latest("pk"))
        return Membership.objects.get(target_organization=self.organization, membership_type=self.gold)

    def _request(self, membership_type: MembershipType) -> MembershipRequest:
        return MembershipRequest.objects.create(
            requested_organization=self.organization,
            membership_type=membership_type,
        )

    def _approve(self, membership_request: MembershipRequest, **kwargs) -> Membership:
        from core.membership_request_workflow import approve_membership_request

        with self.captureOnCommitCallbacks(execute=True):
            approve_membership_request(
                membership_request=membership_request,
                actor_username="committee",
                send_approved_email=kwargs.pop("send_approved_email", False),
                **kwargs,
            )
        return Membership.objects.get(
            target_organization=self.organization,
            membership_type=membership_request.membership_type,
        )

    def _dues_notes(self) -> list[Note]:
        return [note for note in Note.objects.order_by("pk") if (note.action or {}).get("type") == "dues_changed"]


class DuesRulesTests(MembershipDuesTestMixin, TestCase):
    def test_dues_must_stay_between_zero_and_the_tier_default(self) -> None:
        from core.membership_dues import normalize_dues_usd

        self.assertEqual(normalize_dues_usd(self.gold, 0), 0)
        self.assertEqual(normalize_dues_usd(self.gold, 5000), 5000)
        # Charging the default is no waiver at all.
        self.assertIsNone(normalize_dues_usd(self.gold, 20000))
        for invalid in (-1, 20001):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                normalize_dues_usd(self.gold, invalid)
        free_tier = MembershipType.objects.create(
            code="community", name="Community", category_id="sponsorship", annual_dues_usd=0,
        )
        with self.assertRaises(ValidationError):
            normalize_dues_usd(free_tier, 0)

    def test_discount_percent_never_rounds_a_partial_discount_to_all_or_nothing(self) -> None:
        from core.membership_dues import discount_percent

        for default, due, expected in (
            (20000, 5000, "75"),
            (30000, 20000, "33.33"),
            (100000, 10, "99.99"),
            (100000, 99999, "0.01"),
            (20000, 0, "100"),
            (20000, 20000, "0"),
        ):
            with self.subTest(default=default, due=due):
                self.assertEqual(discount_percent(default, due), expected)

    def test_effective_dues_fall_back_to_the_tier_default(self) -> None:
        from core.membership_dues import effective_dues_usd

        self.assertEqual(effective_dues_usd(self._active_gold()), 20000)
        Membership.objects.all().delete()
        self.assertEqual(effective_dues_usd(self._active_gold(dues_usd=5000)), 5000)


class ChangeDuesTests(MembershipDuesTestMixin, TestCase):
    def test_changing_dues_requires_a_reason(self) -> None:
        from core.membership_dues import change_membership_dues

        membership = self._active_gold()
        for reason in ("", "   "):
            with self.subTest(reason=reason), self.assertRaises(ValidationError):
                change_membership_dues(membership=membership, dues_usd=0, reason=reason, actor_username="committee")

    def test_changing_dues_is_logged_noted_and_keeps_the_term(self) -> None:
        from core.membership_dues import change_membership_dues

        membership = self._active_gold()
        term_request = MembershipRequest.objects.get(requested_organization=self.organization)
        # A pending renewal is not the term being changed.
        self._request(self.gold)

        change_membership_dues(
            membership=membership,
            dues_usd=5000,
            reason="In-kind: CI hardware worth $15,000 a year",
            actor_username="committee",
        )

        updated = Membership.objects.get(target_organization=self.organization, membership_type=self.gold)
        self.assertEqual(updated.dues_usd, 5000)
        self.assertEqual(updated.expires_at, membership.expires_at)
        log = MembershipLog.objects.filter(action=MembershipLog.Action.dues_changed).get()
        self.assertEqual((log.actor_username, log.dues_usd, log.expires_at), ("committee", 5000, membership.expires_at))
        self.assertEqual(log.membership_request, term_request)
        [note] = self._dues_notes()
        self.assertEqual(note.membership_request, term_request)
        self.assertEqual(note.username, "committee")
        self.assertEqual(note.content, "In-kind: CI hardware worth $15,000 a year")
        self.assertEqual(
            note.action,
            {
                "type": "dues_changed",
                "membership_type": "gold",
                "default_dues_usd": 20000,
                "old_dues_usd": 20000,
                "new_dues_usd": 5000,
                "discount_percent": "75",
            },
        )

    def test_a_sponsorship_granted_in_the_admin_gets_its_dues_note_on_the_grant_request(self) -> None:
        from core.membership_dues import change_membership_dues
        from core.membership_request_workflow import grant_direct_membership

        now = timezone.now()
        with self.captureOnCommitCallbacks(execute=True):
            membership = grant_direct_membership(
                actor_username="admin",
                membership_type=self.gold,
                starts_at=now,
                expires_at=now + datetime.timedelta(days=365),
                target_organization=self.organization,
            )

        change_membership_dues(
            membership=membership,
            dues_usd=0,
            reason="Sponsor with an in-kind agreement",
            actor_username="committee",
        )

        [note] = self._dues_notes()
        self.assertEqual(note.membership_request, MembershipRequest.objects.get(requested_organization=self.organization))
        self.assertEqual(note.action["new_dues_usd"], 0)

    def test_saving_unchanged_dues_is_rejected_instead_of_noted(self) -> None:
        from core.membership_dues import change_membership_dues

        membership = self._active_gold(dues_usd=5000)

        with self.assertRaisesMessage(ValidationError, "already $5,000"):
            change_membership_dues(membership=membership, dues_usd=5000, reason="No change", actor_username="committee")
        self.assertFalse(MembershipLog.objects.filter(action=MembershipLog.Action.dues_changed).exists())
        self.assertEqual(self._dues_notes(), [])

    def test_expiry_changes_keep_the_waiver(self) -> None:
        membership = self._active_gold(dues_usd=0)

        MembershipLog.create_for_expiry_change(
            actor_username="committee",
            membership_type=self.gold,
            expires_at=membership.expires_at + datetime.timedelta(days=10),
            target_organization=self.organization,
        )

        self.assertEqual(Membership.objects.get(target_organization=self.organization).dues_usd, 0)
        log = MembershipLog.objects.latest("pk")
        self.assertEqual(log.dues_usd, 0)
        # The audit log shows what the expiry was changed from.
        self.assertEqual(log.previous_expires_at, membership.expires_at)

    def test_dues_change_log_records_what_the_sponsor_paid_before(self) -> None:
        from core.membership_dues import change_membership_dues

        membership = self._active_gold(dues_usd=10000)

        change_membership_dues(membership=membership, dues_usd=5000, reason="Bigger in-kind deal", actor_username="committee")

        log = MembershipLog.objects.filter(action=MembershipLog.Action.dues_changed).get()
        self.assertEqual((log.previous_dues_usd, log.dues_usd), (10000, 5000))


class ApprovalDuesTests(MembershipDuesTestMixin, TestCase):
    def test_new_sponsor_approved_with_waived_dues_gets_a_welcome_email_without_invoice(self) -> None:
        welcome, _ = EmailTemplate.objects.update_or_create(
            name="membership-request-approved-gold",
            defaults={
                "subject": "Welcome",
                "content": "Welcome{% if invoice_url %} {{ invoice_url }}{% endif %}",
                "html_content": "<p>Welcome</p>",
            },
        )
        self.gold.acceptance_template = welcome
        self.gold.save(update_fields=["acceptance_template"])

        membership = self._approve(
            self._request(self.gold),
            dues_usd=0,
            dues_reason="Provides mirror bandwidth in kind",
            send_approved_email=True,
        )

        self.assertEqual(membership.dues_usd, 0)
        email = Email.objects.get(context__template_name=welcome.name)
        self.assertEqual(email.attachments.count(), 0)
        self.assertNotIn("invoice_url", email.context)
        self.assertNotIn("billing@example.com", email.cc or [])
        [note] = self._dues_notes()
        self.assertEqual((note.action["old_dues_usd"], note.action["new_dues_usd"]), (20000, 0))
        self.assertEqual(note.content, "Provides mirror bandwidth in kind")

    def test_setting_a_waiver_at_approval_requires_a_reason(self) -> None:
        with self.assertRaises(ValidationError):
            self._approve(self._request(self.gold), dues_usd=0)
        self.assertFalse(Membership.objects.exists())

    def test_renewal_carries_the_waiver_unless_the_committee_changes_it(self) -> None:
        self._active_gold(dues_usd=5000)

        self.assertEqual(self._approve(self._request(self.gold)).dues_usd, 5000)
        self.assertEqual(self._dues_notes(), [])

        # Approving at the default amount ends the waiver, and that is a change worth recording.
        renewed = self._approve(self._request(self.gold), dues_usd=20000, dues_reason="In-kind agreement ended")
        self.assertIsNone(renewed.dues_usd)
        [note] = self._dues_notes()
        self.assertEqual((note.action["old_dues_usd"], note.action["new_dues_usd"]), (5000, 20000))

    def test_a_tier_change_drops_the_waiver_and_says_so(self) -> None:
        self._active_gold(dues_usd=0)

        ruby = self._approve(self._request(self.ruby))

        self.assertIsNone(ruby.dues_usd)
        [note] = self._dues_notes()
        self.assertEqual(note.action["membership_type"], "ruby")
        self.assertEqual(note.action["old_dues_usd"], 0)
        self.assertEqual(note.action["new_dues_usd"], 5000)
        self.assertIn("Gold Sponsor Member", note.content)

    def test_a_tier_change_says_so_even_when_the_new_standard_price_is_given_explicitly(self) -> None:
        self._active_gold(dues_usd=0)

        ruby = self._approve(self._request(self.ruby), dues_usd=5000)

        self.assertIsNone(ruby.dues_usd)
        [note] = self._dues_notes()
        self.assertEqual(note.action["membership_type"], "ruby")
        self.assertEqual(note.action["old_dues_usd"], 0)
        self.assertEqual(note.action["new_dues_usd"], 5000)
        self.assertIn("Gold Sponsor Member", note.content)


class DuesInvoiceTests(MembershipDuesTestMixin, TestCase):
    def test_partial_discount_invoices_show_list_price_discount_and_amount_due(self) -> None:
        from core.sponsorship_invoices import render_invoice_html

        html = render_invoice_html(self._active_gold(dues_usd=5000), InvoiceTerm.renewal)

        list_price = html.index("List price")
        discount = html.index("In-kind discount (75%)")
        amount_due = html.index("Amount due")
        self.assertLess(list_price, discount)
        self.assertLess(discount, amount_due)
        self.assertIn("$20,000", html[list_price:discount])
        self.assertIn("-$15,000", html[discount:amount_due])
        self.assertIn("$5,000", html[amount_due:])

    def test_undiscounted_invoices_show_only_the_amount_due(self) -> None:
        from core.sponsorship_invoices import render_invoice_html

        html = render_invoice_html(self._active_gold(), InvoiceTerm.renewal)

        self.assertIn("$20,000", html[html.index("Amount due"):])
        self.assertNotIn("List price", html)
        self.assertNotIn("In-kind discount", html)

    def test_a_dues_change_issues_a_fresh_invoice_instead_of_reusing_the_stored_one(self) -> None:
        from core.membership_dues import change_membership_dues

        membership = self._active_gold()
        stored_key = self._invoice_key(membership, InvoiceTerm.renewal)

        change_membership_dues(membership=membership, dues_usd=5000, reason="In-kind CI hardware", actor_username="committee")

        discounted = Membership.objects.get(target_organization=self.organization)
        self.assertNotEqual(self._invoice_key(discounted, InvoiceTerm.renewal), stored_key)

    def test_fully_waived_memberships_are_never_invoiced(self) -> None:
        from core.sponsorship_invoices import invoice_eligible

        self.assertFalse(invoice_eligible(self._active_gold(dues_usd=0)))


class DuesRequestNoteTests(MembershipDuesTestMixin, TestCase):
    def _record_created(self, membership_type: MembershipType) -> MembershipRequest:
        from core.membership_request_workflow import record_membership_request_created

        membership_request = self._request(membership_type)
        record_membership_request_created(
            membership_request=membership_request,
            actor_username="rep1",
            send_submitted_email=False,
        )
        return membership_request

    def _system_note(self, membership_request: MembershipRequest) -> Note:
        from core.membership_notes import CUSTOS

        return Note.objects.get(
            membership_request=membership_request,
            username=CUSTOS,
            action__type="dues_waiver_review",
        )

    def test_renewing_a_discounted_sponsorship_asks_the_committee_to_confirm_the_agreement(self) -> None:
        self._active_gold(dues_usd=5000)

        note = self._system_note(self._record_created(self.gold))

        self.assertIn("pays $5,000 of $20,000", note.content)
        self.assertIn("75%", note.content)
        self.assertIn("Confirm the in-kind agreement", note.content)

    def test_changing_tier_records_the_discount_the_sponsor_had(self) -> None:
        self._active_gold(dues_usd=0)

        note = self._system_note(self._record_created(self.ruby))

        self.assertIn("pays $0 of $20,000", note.content)
        self.assertIn("100%", note.content)
        self.assertIn("Ruby Sponsor Member", note.content)

    def test_reapplying_after_a_lapsed_discounted_term_mentions_it(self) -> None:
        self._active_gold(dues_usd=0)
        Membership.objects.all().delete()

        note = self._system_note(self._record_created(self.gold))

        self.assertIn("previous", note.content.lower())

    def test_no_note_without_a_waiver(self) -> None:
        self._active_gold()

        membership_request = self._record_created(self.gold)

        self.assertFalse(Note.objects.filter(membership_request=membership_request, action__type="dues_waiver_review").exists())
