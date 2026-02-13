from post_office.models import EmailTemplate

from core.models import MembershipTypeCategory
from core.templated_email import configured_email_template_names


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
