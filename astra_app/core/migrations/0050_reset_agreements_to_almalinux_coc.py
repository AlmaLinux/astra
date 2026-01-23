from __future__ import annotations

import os
import sys
from pathlib import Path

from django.conf import settings
from django.db import migrations


def _load_coc_text() -> str:
    text_path = (
        Path(__file__).resolve().parent.parent
        / "migration_helpers"
        / "almalinux_community_code_of_conduct.txt"
    )

    try:
        text = text_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Missing CoC text file: {text_path}. "
            "Create it (or restore it) and paste the Code of Conduct text before running this migration."
        ) from exc

    cleaned = text.strip()
    if not cleaned or cleaned.startswith("TODO:"):
        raise RuntimeError(
            f"CoC text file {text_path} is still a placeholder. "
            "Edit it to contain the Code of Conduct text before running this migration."
        )

    return cleaned


def reset_fas_agreements_to_almalinux_coc(apps, schema_editor) -> None:
    if "test" in sys.argv and os.environ.get("ASTRA_HERMETIC_MIGRATIONS", "1") != "0":
        # Hermetic tests must avoid FreeIPA and file IO side effects.
        return
    # Agreements are stored in FreeIPA (freeipa-fas plugin). We intentionally use
    # the FreeIPA-backed backend here rather than Django models.
    from core.backends import FreeIPAFASAgreement

    for agreement in FreeIPAFASAgreement.all():
        agreement.delete()

    FreeIPAFASAgreement.create(
        settings.COMMUNITY_CODE_OF_CONDUCT_AGREEMENT_CN,
        description=_load_coc_text(),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0049_fix_membership_reason_email_escaping"),
    ]

    operations = [
        migrations.RunPython(
            reset_fas_agreements_to_almalinux_coc,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
