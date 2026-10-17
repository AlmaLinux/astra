from typing import override

from core.operations import OperationJobs, OperationsCommand


class Command(OperationsCommand):
    help = "Run hourly membership mirror validation and election lifecycle automation."

    @override
    def jobs(self, *, force: bool, dry_run: bool) -> OperationJobs:
        return (
            ("membership_mirror_validation", {"force": force, "dry_run": dry_run}),
            ("election_lifecycle_automation", {"force": force, "dry_run": dry_run}),
        )
