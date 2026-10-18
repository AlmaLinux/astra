import datetime

from django.db import migrations

# Actions that set or end a membership term; the latest one per category tells
# whether a term was running at a given moment.
_TERM_ACTIONS = ("approved", "expiry_changed", "dues_changed", "terminated")
# The edit view logged open requests just before writing the audit entry, in the same request.
_SAME_EDIT_WINDOW = datetime.timedelta(minutes=1)


def _targets(entry) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """(new log fields, log filter, request filter) for the entry's live or deleted organization."""
    if entry.organization_id is not None:
        live = {"target_organization_id": entry.organization_id}
        return live, live, {"requested_organization_id": entry.organization_id}
    deleted = {"deleted_organization_id": entry.deleted_organization_id}
    return (
        {**deleted, "deleted_organization_name": entry.deleted_organization_name},
        deleted,
        deleted,
    )


def _create_log(MembershipLog, *, created_at: datetime.datetime, **fields: object) -> None:
    log = MembershipLog.objects.create(**fields)
    # created_at is auto_now_add, so the original time can only be restored after creation.
    MembershipLog.objects.filter(pk=log.pk).update(created_at=created_at)


def _move_invoice(apps, entry) -> None:
    MembershipLog = apps.get_model("core", "MembershipLog")
    membership_type = apps.get_model("core", "MembershipType").objects.filter(pk=entry.payload.get("membership_type")).first()
    if membership_type is None:
        return
    new_log, _, request_filter = _targets(entry)
    term_request = (
        apps.get_model("core", "MembershipRequest")
        .objects.filter(
            **request_filter,
            membership_type__category_id=membership_type.category_id,
            status="approved",
            decided_at__lte=entry.timestamp,
        )
        .order_by("-decided_at", "-pk")
        .first()
    )
    _create_log(
        MembershipLog,
        created_at=entry.timestamp,
        **new_log,
        actor_username=entry.payload.get("actor_username", ""),
        membership_type=membership_type,
        membership_request=term_request,
        action="invoice_regenerated" if entry.payload.get("regenerated") else "invoice_generated",
        invoice_number=entry.payload.get("invoice_number", ""),
    )


def _move_representative_change(apps, entry) -> None:
    MembershipLog = apps.get_model("core", "MembershipLog")
    new_log, log_filter, _ = _targets(entry)
    actor_username = entry.payload.get("actor_username", "")
    old = entry.payload.get("old", "")
    new = entry.payload.get("new", "")

    logged_open_requests = MembershipLog.objects.filter(
        **log_filter,
        action="representative_changed",
        actor_username=actor_username,
        old_representative="",
        new_representative="",
        created_at__gte=entry.timestamp - _SAME_EDIT_WINDOW,
        created_at__lte=entry.timestamp,
    )
    covered_request_ids = set(logged_open_requests.values_list("membership_request_id", flat=True))
    logged_open_requests.update(old_representative=old, new_representative=new)

    # Today's code also logs the running sponsorships; give history the same shape.
    latest_by_category = {}
    for log in (
        MembershipLog.objects.filter(**log_filter, action__in=_TERM_ACTIONS, created_at__lt=entry.timestamp)
        .select_related("membership_type")
        .order_by("created_at", "pk")
    ):
        latest_by_category[log.membership_type.category_id] = log
    for log in latest_by_category.values():
        running = log.action != "terminated" and log.expires_at is not None and log.expires_at > entry.timestamp
        if not running or log.membership_request_id in covered_request_ids:
            continue
        _create_log(
            MembershipLog,
            created_at=entry.timestamp,
            **new_log,
            actor_username=actor_username,
            membership_type_id=log.membership_type_id,
            membership_request_id=log.membership_request_id,
            action="representative_changed",
            old_representative=old,
            new_representative=new,
        )


def move_organization_entries(apps, schema_editor) -> None:
    """Move organization history into the membership log; AuditLogEntry becomes election-only.

    Organization edits are dropped: they are not membership events.
    """
    AuditLogEntry = apps.get_model("core", "AuditLogEntry")
    organization_entries = AuditLogEntry.objects.filter(election__isnull=True)
    for entry in organization_entries.order_by("timestamp", "pk"):
        if entry.event_type == "sponsorship_invoice_generated":
            _move_invoice(apps, entry)
        elif entry.event_type == "organization_representative_changed":
            _move_representative_change(apps, entry)
    organization_entries.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0116_membershiplog_representative_change"),
    ]

    operations = [
        # Irreversible in substance (organization rows are gone), but kept reversible so
        # older migration tests can still step back past it.
        migrations.RunPython(move_organization_entries, migrations.RunPython.noop),
    ]
