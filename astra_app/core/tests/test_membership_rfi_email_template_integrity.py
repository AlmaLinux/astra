
from django.core.mail import EmailMessage
from django.test import TestCase


class MembershipRfiEmailTemplateIntegrityTests(TestCase):
    def test_rfi_template_has_subject_and_plaintext_is_not_html(self) -> None:
        from post_office.models import EmailTemplate

        tpl = EmailTemplate.objects.get(name="membership-request-rfi")

        self.assertTrue(str(tpl.subject or "").strip())

        plaintext = str(tpl.content or "")
        self.assertTrue(plaintext.strip())

        # Regression: some installs ended up with HTML copied into the plaintext part.
        self.assertNotIn("<p", plaintext)
        self.assertNotIn("</", plaintext)

    def test_subject_header_folding_threshold_is_70_chars(self) -> None:
        # The Python stdlib email generator folds header lines at 78 characters.
        # Since the prefix "Subject: " is 9 chars, the fold threshold for an ASCII
        # subject is 69 (no fold) vs 70+ (fold).
        ok_subject = "Action required: more information needed for your membership applicat"
        bad_subject = "Action required: more information needed for your membership application"

        ok_msg = EmailMessage(subject=ok_subject, body="body", from_email="from@example.com", to=["to@example.com"])
        ok_headers = ok_msg.message().as_string().split("\n\n", 1)[0].splitlines()
        ok_idx = next(i for i, line in enumerate(ok_headers) if line.startswith("Subject:"))
        self.assertFalse(
            ok_idx + 1 < len(ok_headers) and ok_headers[ok_idx + 1].startswith((" ", "\t"))
        )

        bad_msg = EmailMessage(subject=bad_subject, body="body", from_email="from@example.com", to=["to@example.com"])
        bad_headers = bad_msg.message().as_string().split("\n\n", 1)[0].splitlines()
        bad_idx = next(i for i, line in enumerate(bad_headers) if line.startswith("Subject:"))
        self.assertTrue(bad_idx + 1 < len(bad_headers) and bad_headers[bad_idx + 1].startswith((" ", "\t")))

    def test_rfi_template_subject_is_not_folded(self) -> None:
        from post_office.models import EmailTemplate

        tpl = EmailTemplate.objects.get(name="membership-request-rfi")
        msg = EmailMessage(subject=tpl.subject, body="body", from_email="from@example.com", to=["to@example.com"])
        headers = msg.message().as_string().split("\n\n", 1)[0].splitlines()
        idx = next(i for i, line in enumerate(headers) if line.startswith("Subject:"))

        # Some downstream tooling appears to treat folded Subject headers as malformed.
        self.assertFalse(idx + 1 < len(headers) and headers[idx + 1].startswith((" ", "\t")))
