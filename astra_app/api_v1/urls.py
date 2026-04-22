from django.urls import path

from core import views_membership

urlpatterns = [
    path(
        "membership/requests/pending",
        views_membership.membership_requests_pending_api,
        name="api-membership-requests-pending",
    ),
    path(
        "membership/requests/on-hold",
        views_membership.membership_requests_on_hold_api,
        name="api-membership-requests-on-hold",
    ),
    path(
        "membership/notes/<int:pk>/summary",
        views_membership.membership_request_notes_summary_api,
        name="api-membership-request-notes-summary",
    ),
    path(
        "membership/notes/<int:pk>",
        views_membership.membership_request_notes_api,
        name="api-membership-request-notes",
    ),
    path(
        "membership/notes/aggregate/summary",
        views_membership.membership_notes_aggregate_summary_api,
        name="api-membership-notes-aggregate-summary",
    ),
    path(
        "membership/notes/aggregate",
        views_membership.membership_notes_aggregate_api,
        name="api-membership-notes-aggregate",
    ),
    path(
        "membership/requests/<int:pk>/notes/add",
        views_membership.membership_request_notes_add_api,
        name="api-membership-request-notes-add",
    ),
    path(
        "membership/notes/aggregate/add",
        views_membership.membership_notes_aggregate_add_api,
        name="api-membership-notes-aggregate-add",
    ),
]
