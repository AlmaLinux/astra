import re
from typing import Any

from django.db import migrations, models

ANNUAL_DUES_USD_BY_CODE: dict[str, int] = {
    "individual": 0,
    "mirror": 0,
    "silver": 2500,
    "ruby": 5000,
    "gold": 20000,
    "platinum": 100000,
}

# Matches the dues text migration 0020 embedded in descriptions, e.g.
# " (Annual dues: $100,000 USD)".
DUES_TEXT_PATTERN = re.compile(r"\s*\(Annual dues:[^)]*\)")


def move_annual_dues_out_of_descriptions(apps: Any, schema_editor: Any) -> None:
    MembershipType = apps.get_model("core", "MembershipType")

    for code, annual_dues_usd in ANNUAL_DUES_USD_BY_CODE.items():
        MembershipType.objects.filter(code=code).update(annual_dues_usd=annual_dues_usd)

    # Not limited to the known codes: any tier carrying the dues text would
    # otherwise keep showing a price that is no longer maintained there.
    for membership_type in MembershipType.objects.filter(description__contains="(Annual dues:"):
        membership_type.description = DUES_TEXT_PATTERN.sub("", membership_type.description).strip()
        membership_type.save(update_fields=["description"])


class Migration(migrations.Migration):
    dependencies = [("core", "0100_election_lifecycle_automation")]

    operations = [
        migrations.AddField(
            model_name="membershiptype",
            name="annual_dues_usd",
            field=models.PositiveIntegerField(
                blank=True,
                default=0,
                help_text="Yearly dues in whole US dollars.",
                verbose_name="Annual dues (USD)",
            ),
        ),
        migrations.RunPython(
            move_annual_dues_out_of_descriptions,
            migrations.RunPython.noop,
        ),
    ]
