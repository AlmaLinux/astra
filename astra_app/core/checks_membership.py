from django.core.checks import Warning, register
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q

from core.models import MembershipType
from core.sponsorship_invoices import payment_details_problems, read_payment_details


@register()
def check_enabled_membership_types_have_group_cn(_app_configs=None, **_kwargs) -> list[Warning]:
    issues: list[Warning] = []
    for membership_type in MembershipType.objects.filter(enabled=True).filter(Q(group_cn="") | Q(group_cn__isnull=True)):
        issues.append(
            Warning(
                f"Enabled membership type {membership_type.code!r} has no group_cn and is excluded from requestable lists.",
                obj=membership_type.code,
                id="core.W001",
            )
        )
    return issues


@register()
def check_sponsorship_invoice_payment_details_file(_app_configs=None, **_kwargs) -> list[Warning]:
    # A warning rather than an error: reminders still go out without the invoice,
    # and a hard error would block migrate/runserver wherever the file isn't provisioned.
    try:
        problems = payment_details_problems(read_payment_details())
    except ImproperlyConfigured as exc:
        problems = [str(exc)]
    return [
        Warning(
            f"Sponsorship invoice payment details look wrong: {problem}",
            hint="Point SPONSORSHIP_INVOICE_PAYMENT_DETAILS_FILE at a root-owned text file with the payment instructions.",
            id="core.W002",
        )
        for problem in problems
    ]
