from __future__ import annotations

import uuid

from django.db import migrations, models

from core.tokens import make_signed_token


def _backfill_invitation_tokens(apps, schema_editor) -> None:
    AccountInvitation = apps.get_model("core", "AccountInvitation")
    seen: set[str] = set()
    for invitation in AccountInvitation.objects.order_by("pk"):
        token = str(invitation.invitation_token or "").strip()
        if token and token not in seen:
            seen.add(token)
            continue

        payload = {"invitation_id": invitation.pk}
        if token:
            payload["nonce"] = uuid.uuid4().hex

        new_token = make_signed_token(payload)
        while new_token in seen:
            payload["nonce"] = uuid.uuid4().hex
            new_token = make_signed_token(payload)

        invitation.invitation_token = new_token
        invitation.save(update_fields=["invitation_token"])
        seen.add(new_token)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0061_account_invitations"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    "ALTER TABLE core_accountinvitation "
                    "ADD COLUMN IF NOT EXISTS invitation_token varchar(512);"
                ),
                migrations.RunSQL(
                    "ALTER TABLE core_accountinvitation "
                    "ALTER COLUMN invitation_token TYPE varchar(512) USING invitation_token::text;"
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="accountinvitation",
                    name="invitation_token",
                    field=models.CharField(max_length=512, null=True, editable=False),
                ),
            ],
        ),
        migrations.RunPython(
            code=_backfill_invitation_tokens,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS ("
                    "SELECT 1 FROM pg_constraint "
                    "WHERE conname = 'core_accountinvitation_invitation_token_key'"
                    ") THEN "
                    "ALTER TABLE core_accountinvitation "
                    "ADD CONSTRAINT core_accountinvitation_invitation_token_key "
                    "UNIQUE (invitation_token); "
                    "END IF; "
                    "END $$;"
                ),
                migrations.RunSQL(
                    "ALTER TABLE core_accountinvitation "
                    "ALTER COLUMN invitation_token SET NOT NULL;"
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="accountinvitation",
                    name="invitation_token",
                    field=models.CharField(max_length=512, editable=False, unique=True),
                ),
            ],
        ),
    ]
