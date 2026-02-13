"""Data migration: create MembershipTypeCategory rows, assign categories to
MembershipType records using the legacy isIndividual/isOrganization booleans,
denormalize category onto existing Membership rows, and migrate
OrganizationSponsorship data into the unified Membership table.
"""

from django.db import migrations


def populate_categories(apps, schema_editor):
    MembershipTypeCategory = apps.get_model("core", "MembershipTypeCategory")
    MembershipType = apps.get_model("core", "MembershipType")
    Membership = apps.get_model("core", "Membership")
    OrganizationSponsorship = apps.get_model("core", "OrganizationSponsorship")

    # Step 1: Create category rows.
    individual_cat = MembershipTypeCategory.objects.create(
        name="individual",
        is_individual=True,
        is_organization=False,
    )
    mirror_cat = MembershipTypeCategory.objects.create(
        name="mirror",
        is_individual=True,
        is_organization=True,
    )
    sponsorship_cat = MembershipTypeCategory.objects.create(
        name="sponsorship",
        is_individual=False,
        is_organization=True,
    )

    # Step 2: Assign categories to existing MembershipType rows using the
    # legacy boolean flags (still present in the DB at this point).
    for mt in MembershipType.objects.all():
        if mt.code == "mirror":
            mt.category = mirror_cat
        elif mt.isIndividual:
            mt.category = individual_cat
        elif mt.isOrganization:
            mt.category = sponsorship_cat
        else:
            # Fallback: types that are neither individual nor org default to
            # individual so nothing is silently dropped.
            mt.category = individual_cat
        mt.save(update_fields=["category"])

    # Step 3: Denormalize category onto existing Membership rows (individual
    # user memberships only at this point). Use update() to avoid bumping
    # auto_now timestamps.
    for m in Membership.objects.select_related("membership_type").all():
        Membership.objects.filter(pk=m.pk).update(category_id=m.membership_type.category_id)

    # Step 4: Migrate OrganizationSponsorship → Membership rows.
    sponsorship_fields = {field.name for field in OrganizationSponsorship._meta.fields}

    def sponsorship_rank(sp):
        expires_score = sp.expires_at.timestamp() if sp.expires_at is not None else float("-inf")
        created_score = sp.created_at.timestamp() if sp.created_at is not None else float("-inf")
        return (
            1 if sp.expires_at is None else 0,
            expires_score,
            created_score,
            sp.pk,
        )

    best_by_org_category = {}
    for sp in OrganizationSponsorship.objects.select_related(
        "organization", "membership_type"
    ).all():
        category_id = sp.membership_type.category_id
        key = (sp.organization_id, category_id)
        current = best_by_org_category.get(key)
        if current is None or sponsorship_rank(sp) > sponsorship_rank(current):
            best_by_org_category[key] = sp

    for sp in best_by_org_category.values():
        org = sp.organization
        new_membership = Membership.objects.create(
            target_username="",
            target_organization=org,
            target_organization_code=str(org.pk),
            target_organization_name=org.name,
            membership_type=sp.membership_type,
            category=sp.membership_type.category,
            expires_at=sp.expires_at,
        )
        timestamp_updates: dict[str, object] = {}
        if sp.created_at is not None:
            timestamp_updates["created_at"] = sp.created_at
        if "updated_at" in sponsorship_fields and sp.updated_at is not None:
            timestamp_updates["updated_at"] = sp.updated_at
        if timestamp_updates:
            Membership.objects.filter(pk=new_membership.pk).update(**timestamp_updates)


def reverse_categories(apps, schema_editor):
    """Reverse: drop org-target Membership rows and clear category FKs."""
    Membership = apps.get_model("core", "Membership")
    MembershipType = apps.get_model("core", "MembershipType")
    MembershipTypeCategory = apps.get_model("core", "MembershipTypeCategory")

    # Remove org-targeted memberships that were migrated from OrganizationSponsorship.
    Membership.objects.exclude(target_username="").update(category=None)
    Membership.objects.filter(target_username="").delete()

    MembershipType.objects.all().update(category=None)
    MembershipTypeCategory.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0068_membership_type_category"),
    ]

    operations = [
        migrations.RunPython(populate_categories, reverse_categories),
    ]
