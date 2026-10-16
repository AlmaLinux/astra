from django.db import migrations, models
from django.db.models import Count, Max, OuterRef, Subquery
from django.db.models.functions import Coalesce

# Same column checks as 0037_ballot_soft_supersede; only the documented updatable columns change.
_BALLOT_RESTRICT_UPDATE_SQL = """
CREATE OR REPLACE FUNCTION core_ballot_restrict_update() RETURNS trigger AS $$
BEGIN
    -- Append-only: forbid updates to any field other than {updatable}.
    --
    -- Supersession validity (self-reference, forward-only, same election/credential, cycles)
    -- is enforced by the DEFERRABLE constraint trigger `core_ballot_validate_supersession_trg`.
    IF NEW.election_id IS DISTINCT FROM OLD.election_id
        OR NEW.credential_public_id IS DISTINCT FROM OLD.credential_public_id
        OR NEW.ranking IS DISTINCT FROM OLD.ranking
        OR NEW.weight IS DISTINCT FROM OLD.weight
        OR NEW.ballot_hash IS DISTINCT FROM OLD.ballot_hash
        OR NEW.previous_chain_hash IS DISTINCT FROM OLD.previous_chain_hash
        OR NEW.chain_hash IS DISTINCT FROM OLD.chain_hash
        OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'ballot rows are append-only (only {updatable} may be updated)';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def rebuild_derived_columns(apps, schema_editor) -> None:
    """Reverse only: recompute every dropped column from the data it was derived from."""
    organization_model = apps.get_model("core", "Organization")
    organization_model.objects.exclude(representative="").update(status="active")

    apps.get_model("core", "Ballot").objects.filter(superseded_by__isnull=False).update(is_counted=False)

    queued_sends = apps.get_model("core", "AccountInvitationSend").objects.filter(
        invitation=OuterRef("pk"), result="queued",
    ).order_by().values("invitation")
    apps.get_model("core", "AccountInvitation").objects.update(
        # An invitation without queued sends has no group row, so the count subquery is NULL.
        send_count=Coalesce(
            Subquery(queued_sends.annotate(n=Count("pk")).values("n")[:1], output_field=models.IntegerField()),
            0,
        ),
        last_sent_at=Subquery(queued_sends.annotate(latest=Max("sent_at")).values("latest")[:1]),
    )

    membership_type_model = apps.get_model("core", "MembershipType")
    apps.get_model("core", "MembershipLog").objects.update(
        requested_group_cn=Subquery(
            membership_type_model.objects.filter(code=OuterRef("membership_type_id")).values("group_cn")[:1]
        ),
    )

    log_model = apps.get_model("core", "MembershipLog")
    log = log_model.objects.filter(pk=OuterRef("membership_log_id"))
    apps.get_model("core", "MembershipTerminationFeedback").objects.update(
        username=Subquery(log.values("target_username")[:1]),
        membership_type_id=Subquery(log.values("membership_type_id")[:1]),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0108_deleted_organization_snapshot'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='ballot',
            name='uniq_ballot_counted_election_credential',
        ),
        migrations.RemoveConstraint(
            model_name='membership',
            name='chk_membership_exactly_one_target',
        ),
        migrations.RemoveConstraint(
            model_name='membershiplog',
            name='chk_membershiplog_exactly_one_target',
        ),
        migrations.RemoveConstraint(
            model_name='membershiprequest',
            name='chk_membershiprequest_exactly_one_target',
        ),
        migrations.RemoveConstraint(
            model_name='organization',
            name='core_organization_status_matches_representative',
        ),
        migrations.RemoveConstraint(
            model_name='organization',
            name='core_organization_unclaimed_requires_claim_secret',
        ),
        migrations.RemoveIndex(
            model_name='membershipterminationfeedback',
            name='mtf_user_created',
        ),
        migrations.RemoveIndex(
            model_name='membershipterminationfeedback',
            name='mtf_type_created',
        ),
        # Nullable first, so reversing can re-add these columns before rebuild_derived_columns fills them.
        migrations.AlterField(
            model_name='membershipterminationfeedback',
            name='membership_type',
            field=models.ForeignKey(null=True, on_delete=models.PROTECT, related_name='+', to='core.membershiptype'),
        ),
        migrations.AlterField(
            model_name='membershipterminationfeedback',
            name='username',
            field=models.CharField(db_index=True, max_length=255, null=True),
        ),
        migrations.RunPython(migrations.RunPython.noop, rebuild_derived_columns),
        migrations.RunSQL(
            _BALLOT_RESTRICT_UPDATE_SQL.format(updatable="superseded_by_id"),
            _BALLOT_RESTRICT_UPDATE_SQL.format(updatable="superseded_by_id and is_counted"),
        ),
        migrations.RemoveField(
            model_name='accountinvitation',
            name='last_sent_at',
        ),
        migrations.RemoveField(
            model_name='accountinvitation',
            name='send_count',
        ),
        migrations.RemoveField(
            model_name='ballot',
            name='is_counted',
        ),
        migrations.RemoveField(
            model_name='membershiplog',
            name='requested_group_cn',
        ),
        migrations.RemoveField(
            model_name='organization',
            name='status',
        ),
        migrations.RemoveField(
            model_name='membershipterminationfeedback',
            name='membership_type',
        ),
        migrations.RemoveField(
            model_name='membershipterminationfeedback',
            name='username',
        ),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(models.Q(('target_username', ''), _negated=True), models.Q(('target_organization__isnull', False), _negated=True)), models.Q(('target_organization__isnull', False), ('target_username', '')), _connector='OR'), name='chk_membership_exactly_one_target'),
        ),
        migrations.AddConstraint(
            model_name='membershiplog',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(models.Q(('target_username', ''), _negated=True), models.Q(('target_organization__isnull', False), _negated=True), models.Q(('deleted_organization_id__isnull', False), _negated=True)), models.Q(('target_organization__isnull', False), ('target_username', ''), models.Q(('deleted_organization_id__isnull', False), _negated=True)), models.Q(('deleted_organization_id__isnull', False), ('target_username', ''), models.Q(('target_organization__isnull', False), _negated=True)), _connector='OR'), name='chk_membershiplog_exactly_one_target'),
        ),
        migrations.AddConstraint(
            model_name='membershiprequest',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(models.Q(('requested_username', ''), _negated=True), models.Q(('requested_organization__isnull', False), _negated=True), models.Q(('deleted_organization_id__isnull', False), _negated=True)), models.Q(('requested_organization__isnull', False), ('requested_username', ''), models.Q(('deleted_organization_id__isnull', False), _negated=True)), models.Q(('deleted_organization_id__isnull', False), ('requested_username', ''), models.Q(('requested_organization__isnull', False), _negated=True)), _connector='OR'), name='chk_membershiprequest_exactly_one_target'),
        ),
        migrations.AddConstraint(
            model_name='organization',
            constraint=models.CheckConstraint(condition=models.Q(('representative', ''), ('claim_secret', ''), _negated=True), name='core_organization_unclaimed_requires_claim_secret'),
        ),
    ]
