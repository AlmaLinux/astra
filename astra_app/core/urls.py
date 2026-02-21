from django.contrib.staticfiles.storage import staticfiles_storage
from django.urls import path
from django.views.generic import RedirectView

from core import (
    views_account_invitations,
    views_elections,
    views_groups,
    views_health,
    views_mail_images,
    views_membership,
    views_organizations,
    views_search,
    views_send_mail,
    views_settings,
    views_static,
    views_templated_email,
    views_users,
)

urlpatterns = [
    path("", views_users.home, name="home"),
    path("favicon.ico", RedirectView.as_view(url=staticfiles_storage.url("core/images/fav/favicon.ico"), permanent=True)),
    path("users/", views_users.users, name="users"),
    path("user/<str:username>/", views_users.user_profile, name="user-profile"),
    path("groups/", views_groups.groups, name="groups"),
    path("groups/search/", views_groups.group_search, name="group-search"),
    path("group/<str:name>/", views_groups.group_detail, name="group-detail"),
    path("group/<str:name>/edit/", views_groups.group_edit, name="group-edit"),

    path("organizations/", views_organizations.organizations, name="organizations"),
    path("organizations/create/", views_organizations.organization_create, name="organization-create"),
    path("organizations/claim/<str:token>/", views_organizations.organization_claim, name="organization-claim"),
    path(
        "organizations/representatives/search/",
        views_organizations.organization_representatives_search,
        name="organization-representatives-search",
    ),
    path("organization/<int:organization_id>/", views_organizations.organization_detail, name="organization-detail"),
    path("organization/<int:organization_id>/delete/", views_organizations.organization_delete, name="organization-delete"),
    path(
        "organization/<int:organization_id>/sponsorship/extend/",
        views_organizations.organization_sponsorship_extend,
        name="organization-sponsorship-extend",
    ),
    path(
        "organization/<int:organization_id>/membership/request/",
        views_membership.membership_request,
        name="organization-membership-request",
    ),
    path(
        "organization/<int:organization_id>/sponsorship/<str:membership_type_code>/expiry/",
        views_membership.membership_set_expiry,
        name="organization-sponsorship-set-expiry",
    ),
    path(
        "organization/<int:organization_id>/sponsorship/<str:membership_type_code>/terminate/",
        views_membership.membership_terminate,
        name="organization-sponsorship-terminate",
    ),
    path("organization/<int:organization_id>/edit/", views_organizations.organization_edit, name="organization-edit"),

    path("search/", views_search.global_search, name="global-search"),

    path("privacy-policy/", views_static.privacy_policy, name="privacy-policy"),
    path("robots.txt", views_static.robots_txt, name="robots-txt"),

    path("elections/", views_elections.elections_list, name="elections"),
    path("elections/algorithm/", views_elections.election_algorithm, name="election-algorithm"),
    path("elections/ballot/verify/", views_elections.ballot_verify, name="ballot-verify"),
    path("elections/<int:election_id>/edit/", views_elections.election_edit, name="election-edit"),
    path(
        "elections/<int:election_id>/eligible-users/search/",
        views_elections.election_eligible_users_search,
        name="election-eligible-users-search",
    ),
    path(
        "elections/<int:election_id>/nomination-users/search/",
        views_elections.election_nomination_users_search,
        name="election-nomination-users-search",
    ),
    path(
        "elections/<int:election_id>/email/render-preview/",
        views_elections.election_email_render_preview,
        name="election-email-render-preview",
    ),
    path("elections/<int:election_id>/", views_elections.election_detail, name="election-detail"),
    path("elections/<int:election_id>/vote/", views_elections.election_vote, name="election-vote"),

    path(
        "elections/<int:election_id>/send-mail-credentials/",
        views_elections.election_send_mail_credentials,
        name="election-send-mail-credentials",
    ),

    path("elections/<int:election_id>/extend-end/", views_elections.election_extend_end, name="election-extend-end"),

    path(
        "elections/<int:election_id>/conclude/",
        views_elections.election_conclude,
        name="election-conclude",
    ),

    path(
        "elections/<int:election_id>/public/ballots.json",
        views_elections.election_public_ballots,
        name="election-public-ballots",
    ),
    path(
        "elections/<int:election_id>/public/audit.json",
        views_elections.election_public_audit,
        name="election-public-audit",
    ),
    path(
        "elections/<int:election_id>/audit/",
        views_elections.election_audit_log,
        name="election-audit-log",
    ),
    path(
        "elections/<int:election_id>/vote/submit.json",
        views_elections.election_vote_submit,
        name="election-vote-submit",
    ),

    path("email-tools/send-mail/", views_send_mail.send_mail, name="send-mail"),
    path(
        "email-tools/send-mail/render-preview/",
        views_send_mail.send_mail_render_preview,
        name="send-mail-render-preview",
    ),

    path(
        "email-tools/templates/<int:template_id>/json/",
        views_templated_email.email_template_json,
        name="email-template-json",
    ),
    path(
        "email-tools/templates/render-preview/",
        views_templated_email.email_template_render_preview,
        name="email-template-render-preview",
    ),
    path(
        "email-tools/templates/save/",
        views_templated_email.email_template_save,
        name="email-template-save",
    ),
    path(
        "email-tools/templates/save-as/",
        views_templated_email.email_template_save_as,
        name="email-template-save-as",
    ),

    path(
        "email-tools/templates/",
        views_templated_email.email_templates,
        name="email-templates",
    ),
    path(
        "email-tools/templates/new/",
        views_templated_email.email_template_create,
        name="email-template-create",
    ),
    path(
        "email-tools/templates/<int:template_id>/",
        views_templated_email.email_template_edit,
        name="email-template-edit",
    ),
    path(
        "email-tools/templates/<int:template_id>/delete/",
        views_templated_email.email_template_delete,
        name="email-template-delete",
    ),

    path("email-tools/images/", views_mail_images.email_images, name="email-images"),

    path("membership/request/", views_membership.membership_request, name="membership-request"),
    path("membership/mirror-badge.svg", views_membership.mirror_badge_svg, name="mirror-badge-svg"),
    path("membership/mirror-badge.json", views_membership.mirror_badge_status, name="mirror-badge-status"),
    path(
        "membership/request/<int:pk>/",
        views_membership.membership_request_self,
        name="membership-request-self",
    ),
    path(
        "membership/request/<int:pk>/rescind/",
        views_membership.membership_request_rescind,
        name="membership-request-rescind",
    ),
    path("membership/requests/", views_membership.membership_requests, name="membership-requests"),
    path(
        "membership/requests/<int:pk>/",
        views_membership.membership_request_detail,
        name="membership-request-detail",
    ),
    path(
        "membership/requests/<int:pk>/notes/add/",
        views_membership.membership_request_note_add,
        name="membership-request-note-add",
    ),
    path(
        "membership/notes/aggregate/add/",
        views_membership.membership_notes_aggregate_note_add,
        name="membership-notes-aggregate-note-add",
    ),
    path(
        "membership/requests/bulk/",
        views_membership.membership_requests_bulk,
        name="membership-requests-bulk",
    ),
    path(
        "membership/requests/<int:pk>/approve/",
        views_membership.membership_request_approve,
        name="membership-request-approve",
    ),
    path(
        "membership/requests/<int:pk>/reject/",
        views_membership.membership_request_reject,
        name="membership-request-reject",
    ),
    path(
        "membership/requests/<int:pk>/rfi/",
        views_membership.membership_request_rfi,
        name="membership-request-rfi",
    ),
    path(
        "membership/requests/<int:pk>/ignore/",
        views_membership.membership_request_ignore,
        name="membership-request-ignore",
    ),
    path(
        "membership/account-invitations/",
        views_account_invitations.account_invitations,
        name="account-invitations",
    ),
    path(
        "membership/account-invitations/upload/",
        views_account_invitations.account_invitations_upload,
        name="account-invitations-upload",
    ),
    path(
        "membership/account-invitations/send/",
        views_account_invitations.account_invitations_send,
        name="account-invitations-send",
    ),
    path(
        "membership/account-invitations/bulk/",
        views_account_invitations.account_invitations_bulk,
        name="account-invitations-bulk",
    ),
    path(
        "membership/account-invitations/<int:invitation_id>/resend/",
        views_account_invitations.account_invitation_resend,
        name="account-invitation-resend",
    ),
    path(
        "membership/account-invitations/<int:invitation_id>/dismiss/",
        views_account_invitations.account_invitation_dismiss,
        name="account-invitation-dismiss",
    ),

    path("membership/log/", views_membership.membership_audit_log, name="membership-audit-log"),
    path(
        "membership/log/org/<int:organization_id>/",
        views_membership.membership_audit_log_organization,
        name="membership-audit-log-organization",
    ),
    path(
        "membership/log/<str:username>/",
        views_membership.membership_audit_log_user,
        name="membership-audit-log-user",
    ),

    path("membership/stats/", views_membership.membership_stats, name="membership-stats"),
    path(
        "membership/stats/data/",
        views_membership.membership_stats_data,
        name="membership-stats-data",
    ),
    path("membership/sponsors/", views_membership.membership_sponsors_list, name="membership-sponsors"),

    path(
        "membership/manage/<str:username>/<str:membership_type_code>/expiry/",
        views_membership.membership_set_expiry,
        name="membership-set-expiry",
    ),
    path(
        "membership/manage/<str:username>/<str:membership_type_code>/terminate/",
        views_membership.membership_terminate,
        name="membership-terminate",
    ),

    path("settings/", views_settings.settings_root, name="settings"),
    path("settings/avatar/", views_settings.avatar_manage, name="avatar-manage"),
    path("settings/avatar/upload/", views_settings.avatar_upload, name="settings-avatar-upload"),
    path("settings/avatar/delete/", views_settings.avatar_delete, name="settings-avatar-delete"),
    path("settings/emails/validate/", views_settings.settings_email_validate, name="settings-email-validate"),
    path("settings/security/otp/enable/", views_settings.security_otp_enable, name="security-otp-enable"),
    path("settings/security/otp/disable/", views_settings.security_otp_disable, name="security-otp-disable"),
    path("settings/security/otp/delete/", views_settings.security_otp_delete, name="security-otp-delete"),
    path("settings/security/otp/rename/", views_settings.security_otp_rename, name="security-otp-rename"),
    path("healthz", views_health.healthz, name="healthz"),
    path("readyz", views_health.readyz, name="readyz"),
]
