from django.test import SimpleTestCase

from core.apps import _apply_jazzmin_model_groups


class JazzminModelGroupTests(SimpleTestCase):
    def test_apply_jazzmin_model_groups_creates_requested_sections(self) -> None:
        menu = [
            {
                "name": "Authentication and Authorization",
                "app_label": "auth",
                "icon": "fas fa-users-cog",
                "models": [
                    {"name": "Users", "model_str": "auth.ipauser", "url": "/admin/auth/ipauser/", "icon": "fas fa-user"},
                    {"name": "Groups", "model_str": "auth.ipagroup", "url": "/admin/auth/ipagroup/", "icon": "fas fa-users"},
                    {
                        "name": "Agreements",
                        "model_str": "auth.ipafasagreement",
                        "url": "/admin/auth/ipafasagreement/",
                        "icon": "fas fa-file-signature",
                    },
                ],
            },
            {
                "name": "Core",
                "app_label": "core",
                "icon": "fas fa-cube",
                "models": [
                    {
                        "name": "Organization",
                        "model_str": "core.organization",
                        "url": "/admin/core/organization/",
                        "icon": "fas fa-building",
                    },
                    {
                        "name": "Membership type categories",
                        "model_str": "core.membershiptypecategory",
                        "url": "/admin/core/membershiptypecategory/",
                        "icon": "fas fa-tags",
                    },
                    {
                        "name": "Membership types",
                        "model_str": "core.membershiptype",
                        "url": "/admin/core/membershiptype/",
                        "icon": "fas fa-tag",
                    },
                    {
                        "name": "Elections",
                        "model_str": "core.election",
                        "url": "/admin/core/election/",
                        "icon": "fas fa-vote-yea",
                    },
                    {
                        "name": "Candidates",
                        "model_str": "core.candidate",
                        "url": "/admin/core/candidate/",
                        "icon": "fas fa-user-check",
                    },
                    {
                        "name": "Exclusion groups",
                        "model_str": "core.exclusiongroup",
                        "url": "/admin/core/exclusiongroup/",
                        "icon": "fas fa-user-slash",
                    },
                    {
                        "name": "Audit log entries",
                        "model_str": "core.auditlogentry",
                        "url": "/admin/core/auditlogentry/",
                        "icon": "fas fa-clipboard-list",
                    },
                    {
                        "name": "User membership import (CSV)",
                        "model_str": "core.membershipcsvimportlink",
                        "url": "/admin/core/membershipcsvimportlink/",
                        "icon": "fas fa-file-csv",
                    },
                    {
                        "name": "Organization import (CSV)",
                        "model_str": "core.organizationcsvimportlink",
                        "url": "/admin/core/organizationcsvimportlink/",
                        "icon": "fas fa-file-import",
                    },
                    {
                        "name": "Organization membership import (CSV)",
                        "model_str": "core.organizationmembershipcsvimportlink",
                        "url": "/admin/core/organizationmembershipcsvimportlink/",
                        "icon": "fas fa-file-upload",
                    },
                    {
                        "name": "Permission Grants",
                        "model_str": "core.freeipapermissiongrant",
                        "url": "/admin/core/freeipapermissiongrant/",
                        "icon": "fas fa-key",
                    },
                ],
            },
        ]

        groups = [
            {"name": "Users & Groups", "models": ["auth.ipauser", "auth.ipagroup", "core.organization"]},
            {
                "name": "Elections",
                "models": ["core.election", "core.candidate", "core.exclusiongroup", "core.auditlogentry"],
            },
            {
                "name": "Importers",
                "models": [
                    "core.membershipcsvimportlink",
                    "core.organizationcsvimportlink",
                    "core.organizationmembershipcsvimportlink",
                ],
            },
            {"name": "Membership Config", "models": ["core.membershiptypecategory", "core.membershiptype"]},
            {"name": "Settings", "models": ["auth.ipafasagreement", "core.freeipapermissiongrant"]},
        ]

        grouped_menu = _apply_jazzmin_model_groups(menu=menu, groups=groups, default_icon="fas fa-cog")

        self.assertEqual([section["name"] for section in grouped_menu[:5]], [group["name"] for group in groups])

        users_and_groups_models = [model["model_str"] for model in grouped_menu[0]["models"]]
        self.assertEqual(users_and_groups_models, ["auth.ipauser", "auth.ipagroup", "core.organization"])

        elections_models = [model["model_str"] for model in grouped_menu[1]["models"]]
        self.assertEqual(
            elections_models,
            ["core.election", "core.candidate", "core.exclusiongroup", "core.auditlogentry"],
        )

        imported_model_strs: list[str] = []
        for section in grouped_menu:
            for model in section.get("models", []):
                imported_model_strs.append(model.get("model_str", ""))
        self.assertEqual(len(imported_model_strs), len(set(imported_model_strs)))

    def test_apply_jazzmin_model_groups_backfills_missing_models_from_fallback_menu(self) -> None:
        menu = [
            {
                "name": "Settings",
                "app_label": "settings",
                "icon": "fas fa-cog",
                "models": [
                    {
                        "name": "Permission Grants",
                        "model_str": "core.freeipapermissiongrant",
                        "url": "/admin/core/freeipapermissiongrant/",
                        "icon": "fas fa-key",
                    }
                ],
            }
        ]

        fallback_menu = [
            {
                "name": "Authentication and Authorization",
                "app_label": "auth",
                "icon": "fas fa-users-cog",
                "models": [
                    {
                        "name": "Agreements",
                        "model_str": "auth.ipafasagreement",
                        "url": "/admin/auth/ipafasagreement/",
                        "icon": "fas fa-file-signature",
                    }
                ],
            },
            {
                "name": "Core",
                "app_label": "core",
                "icon": "fas fa-cube",
                "models": [
                    {
                        "name": "Permission Grants",
                        "model_str": "core.freeipapermissiongrant",
                        "url": "/admin/core/freeipapermissiongrant/",
                        "icon": "fas fa-key",
                    }
                ],
            },
        ]

        groups = [
            {
                "name": "Settings",
                "models": ["auth.ipafasagreement", "core.freeipapermissiongrant"],
            }
        ]

        grouped_menu = _apply_jazzmin_model_groups(
            menu=menu,
            groups=groups,
            default_icon="fas fa-cog",
            fallback_menu=fallback_menu,
        )

        settings_models = [model["model_str"] for model in grouped_menu[0]["models"]]
        self.assertEqual(settings_models, ["auth.ipafasagreement", "core.freeipapermissiongrant"])
