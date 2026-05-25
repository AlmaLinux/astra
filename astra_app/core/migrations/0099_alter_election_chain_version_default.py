from django.db import migrations, models


CHAIN_VERSION_IMMUTABLE_SQL = """
CREATE OR REPLACE FUNCTION core_election_restrict_chain_version_update() RETURNS trigger AS $$
BEGIN
    IF NEW.chain_version IS DISTINCT FROM OLD.chain_version THEN
        RAISE EXCEPTION 'election chain_version cannot change after insert';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS core_election_restrict_chain_version_update_trg ON core_election;
CREATE TRIGGER core_election_restrict_chain_version_update_trg
BEFORE UPDATE ON core_election
FOR EACH ROW
EXECUTE FUNCTION core_election_restrict_chain_version_update();
"""


CHAIN_VERSION_IMMUTABLE_SQL_REVERSE = """
DROP TRIGGER IF EXISTS core_election_restrict_chain_version_update_trg ON core_election;
DROP FUNCTION IF EXISTS core_election_restrict_chain_version_update();
"""


def _create_chain_version_trigger(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(CHAIN_VERSION_IMMUTABLE_SQL)


def _drop_chain_version_trigger(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(CHAIN_VERSION_IMMUTABLE_SQL_REVERSE)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0098_update_election_vote_receipt_template_manifest_digest_copy"),
    ]

    operations = [
        migrations.AlterField(
            model_name="election",
            name="chain_version",
            field=models.PositiveSmallIntegerField(default=2),
        ),
        migrations.RunPython(
            code=_create_chain_version_trigger,
            reverse_code=_drop_chain_version_trigger,
        ),
    ]