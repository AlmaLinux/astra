from typing import override

from core.operations import OperationJobs, OperationsCommand


class Command(OperationsCommand):
    help = (
        "Run the weekly operations: committee pending-request notifications "
        "and embargoed-members notifications."
    )

    @override
    def jobs(self, *, force: bool, dry_run: bool) -> OperationJobs:
        return (
            ("membership_pending_requests", {"force": force, "dry_run": dry_run}),
            ("membership_embargoed_members", {"force": force, "dry_run": dry_run}),
        )
