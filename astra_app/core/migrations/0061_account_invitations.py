from __future__ import annotations

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0059_create_account_invite_email_template"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountInvitation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("full_name", models.CharField(blank=True, default="", max_length=255)),
                ("note", models.TextField(blank=True, default="")),
                ("email_template_name", models.CharField(blank=True, default="", max_length=255)),
                ("invited_by_username", models.CharField(max_length=255)),
                ("invited_at", models.DateTimeField(auto_now_add=True)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
                ("send_count", models.PositiveIntegerField(default=0)),
                ("dismissed_at", models.DateTimeField(blank=True, null=True)),
                ("dismissed_by_username", models.CharField(blank=True, default="", max_length=255)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("freeipa_matched_usernames", models.JSONField(blank=True, default=list)),
                ("freeipa_last_checked_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ("-invited_at", "email"),
            },
        ),
        migrations.CreateModel(
            name="AccountInvitationSend",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sent_by_username", models.CharField(max_length=255)),
                ("sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("template_name", models.CharField(max_length=255)),
                ("post_office_email_id", models.BigIntegerField(blank=True, null=True)),
                (
                    "result",
                    models.CharField(
                        choices=[("queued", "Queued"), ("failed", "Failed")],
                        max_length=16,
                    ),
                ),
                ("error_category", models.CharField(blank=True, default="", max_length=64)),
                (
                    "invitation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sends",
                        to="core.accountinvitation",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="accountinvitation",
            index=models.Index(fields=["accepted_at"], name="acct_inv_accept_at"),
        ),
        migrations.AddIndex(
            model_name="accountinvitation",
            index=models.Index(fields=["dismissed_at"], name="acct_inv_dismiss_at"),
        ),
        migrations.AddIndex(
            model_name="accountinvitationsend",
            index=models.Index(fields=["sent_at"], name="acct_inv_send_at"),
        ),
    ]
