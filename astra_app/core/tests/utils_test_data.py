import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from post_office.models import EmailTemplate

from core.models import MembershipTypeCategory
from core.templated_email import configured_email_template_names

# Expiry for fixtures that only need an active membership that never nears renewal.
FAR_FUTURE = datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)


def migrate_to_latest() -> None:
    """Restore the full schema after a test pinned the database to an older migration.

    Register with `addCleanup` in migration tests: the test database is shared, so
    leaving it mid-history breaks later tests that use newer columns.
    """
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def ensure_core_categories() -> None:
    """Ensure core membership categories exist for tests that create membership types."""
    category_specs = [
        ("individual", True, False),
        ("mirror", True, True),
        ("sponsorship", False, True),
        ("contributor", True, False),
        ("emeritus", True, False),
    ]

    for sort_order, (name, is_individual, is_organization) in enumerate(category_specs):
        MembershipTypeCategory.objects.update_or_create(
            name=name,
            defaults={
                "is_individual": is_individual,
                "is_organization": is_organization,
                "sort_order": sort_order,
            },
        )


def ensure_email_templates() -> None:
    """Ensure configured runtime email templates exist in the test database.

    Keep tests aligned with runtime SSOT (`configured_email_template_names`) so
    setting changes do not silently drift from test fixtures.
    """
    for name in sorted(configured_email_template_names()):
        EmailTemplate.objects.get_or_create(
            name=name,
            defaults={
                "subject": f"[{name}] Subject",
                "content": f"[{name}] Body",
                "html_content": f"<p>[{name}] Body</p>",
            },
        )
