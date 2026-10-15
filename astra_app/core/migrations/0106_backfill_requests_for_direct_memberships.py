from django.db import migrations

# CUSTOS in core.membership_notes; migrations must not import app code.
SYSTEM_USERNAME = "-"
NOTE = (
    "This membership was granted directly in the admin, without a request. "
    "This approved request was added afterwards so its notes have a place to live."
)


def backfill_requests(apps, schema_editor) -> None:
    """Give every membership granted directly in the admin an approved request.

    Committee notes (such as dues changes) attach to a request, so a membership
    without one had nowhere visible to record them.
    """
    Membership = apps.get_model("core", "Membership")
    MembershipRequest = apps.get_model("core", "MembershipRequest")
    MembershipLog = apps.get_model("core", "MembershipLog")
    Note = apps.get_model("core", "Note")

    for membership in Membership.objects.select_related("target_organization").iterator():
        organization = membership.target_organization
        if organization is not None:
            target = {"requested_organization": organization}
            log_target = {"target_organization": organization}
        else:
            target = {"requested_username": membership.target_username}
            log_target = {"target_username": membership.target_username}
        if MembershipRequest.objects.filter(**target, membership_type_id=membership.membership_type_id).exists():
            continue

        approval = (
            MembershipLog.objects.filter(**log_target, membership_type_id=membership.membership_type_id, action="approved")
            .order_by("-created_at")
            .first()
        )
        membership_request = MembershipRequest.objects.create(
            **target,
            # Historical models skip MembershipRequest.save(), which fills these in.
            requested_organization_code=str(organization.pk) if organization is not None else "",
            requested_organization_name=organization.name if organization is not None else "",
            membership_type_id=membership.membership_type_id,
            status="approved",
            decided_at=membership.created_at,
            decided_by_username=approval.actor_username if approval is not None else "",
        )
        MembershipRequest.objects.filter(pk=membership_request.pk).update(requested_at=membership.created_at)
        Note.objects.create(membership_request=membership_request, username=SYSTEM_USERNAME, content=NOTE)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0105_membership_dues_usd"),
    ]

    operations = [
        migrations.RunPython(backfill_requests, migrations.RunPython.noop),
    ]
