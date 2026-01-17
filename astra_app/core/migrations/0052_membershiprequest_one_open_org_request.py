from __future__ import annotations

from django.db import migrations, models
from django.db.models import Count, Q
from django.utils import timezone


def _rescind_duplicate_open_org_requests(apps, schema_editor) -> None:
    """Ensure applying the new per-organization uniqueness constraint is safe.

    Historically, the schema allowed multiple open (pending/on-hold) requests for the
    same org as long as the requested membership type differed. This caused confusing
    UI behavior where the newest request hid the older one.

    Keep the newest request per org and rescind the rest.
    """

    MembershipRequest = apps.get_model("core", "MembershipRequest")

    open_requests = MembershipRequest.objects.filter(
        requested_organization_id__isnull=False,
        status__in=["pending", "on_hold"],
    )

    dup_org_ids = (
        open_requests.values("requested_organization_id")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
        .values_list("requested_organization_id", flat=True)
    )

    now = timezone.now()
    for org_id in dup_org_ids:
        keep = (
            open_requests.filter(requested_organization_id=org_id)
            .order_by("-requested_at", "-pk")
            .first()
        )
        if keep is None:
            continue

        MembershipRequest.objects.filter(
            requested_organization_id=org_id,
            status__in=["pending", "on_hold"],
        ).exclude(pk=keep.pk).update(
            status="rescinded",
            decided_at=now,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0051_seed_membership_type_groups_and_election_committee"),
    ]

    operations = [
        migrations.RunPython(_rescind_duplicate_open_org_requests, reverse_code=migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="membershiprequest",
            name="uniq_membershiprequest_open_org_type",
        ),
        migrations.AddConstraint(
            model_name="membershiprequest",
            constraint=models.UniqueConstraint(
                fields=("requested_organization",),
                condition=Q(status__in=["pending", "on_hold"], requested_organization__isnull=False),
                name="uniq_membershiprequest_open_org_type",
            ),
        ),
    ]
