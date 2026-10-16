from django.db import migrations

# Events that belong to the membership term rather than to a request's own workflow.
LIFECYCLE_ACTIONS = ("expiry_changed", "terminated", "dues_changed")


def backfill_lifecycle_log_requests(apps, schema_editor) -> None:
    """Link lifecycle log rows to the request that granted the term they changed.

    These rows used to be logged without a request, so the audit log could not tie
    them to a membership. The term in force at the time is the latest approved
    request decided before the row, not today's latest request.
    """
    MembershipLog = apps.get_model("core", "MembershipLog")
    MembershipRequest = apps.get_model("core", "MembershipRequest")

    unlinked = MembershipLog.objects.filter(
        membership_request__isnull=True,
        action__in=LIFECYCLE_ACTIONS,
    ).select_related("membership_type")
    for log in unlinked.iterator():
        if log.target_organization_id is not None:
            target = {"requested_organization_id": log.target_organization_id}
        elif log.target_organization_code:
            # Deleted organization: its requests keep the code once the FK is nulled.
            target = {"requested_organization_code": log.target_organization_code}
        else:
            target = {"requested_username": log.target_username}
        request_id = (
            MembershipRequest.objects.filter(
                **target,
                membership_type__category_id=log.membership_type.category_id,
                status="approved",
                decided_at__lte=log.created_at,
            )
            .order_by("-decided_at", "-pk")
            .values_list("pk", flat=True)
            .first()
        )
        if request_id is not None:
            MembershipLog.objects.filter(pk=log.pk).update(membership_request_id=request_id)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0106_backfill_requests_for_direct_memberships"),
    ]

    operations = [
        migrations.RunPython(backfill_lifecycle_log_requests, migrations.RunPython.noop),
    ]
