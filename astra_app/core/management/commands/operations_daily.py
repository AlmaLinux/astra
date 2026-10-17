from typing import override

from core.operations import OperationJobs, OperationsCommand


class Command(OperationsCommand):
    help = (
        "Run the daily operations: expiration warnings, expired cleanup, "
        "team-leads sync, lifecycle cleanup, and invitation refresh."
    )

    @override
    def jobs(self, *, force: bool, dry_run: bool) -> OperationJobs:
        return (
            ("sponsorship_invoice_payment_details_check", {"dry_run": dry_run}),
            ("membership_expired_cleanup", {"force": force, "dry_run": dry_run}),
            ("membership_expiration_notifications", {"force": force, "dry_run": dry_run}),
            ("freeipa_membership_reconcile", {"report": True, "dry_run": dry_run}),
            ("freeipa_team_leads_sync", {"dry_run": dry_run}),
            ("selfservice_lifecycle_cleanup", {"dry_run": dry_run}),
            ("account_invitations_refresh", {}),
            ("send_mail_attachments_cleanup", {"dry_run": dry_run}),
        )
