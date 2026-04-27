"""REST API endpoints for account invitations."""

import json
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.conf import settings
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.timezone import localtime
from django.views.decorators.http import require_http_methods

from core.account_invitations import (
    find_account_invitation_matches,
    refresh_account_invitations,
)
from core.logging_extras import current_exception_log_fields
from core.models import AccountInvitation, AccountInvitationSend
from core.permissions import ASTRA_ADD_MEMBERSHIP
from core.rate_limit import allow_request
from core.templated_email import queue_templated_email
from core.views_account_invitations import _build_invitation_email_context
from core.views_utils import get_username

logger = logging.getLogger(__name__)


def _display_datetime(value: timezone.datetime | None) -> str | None:
    """Render datetimes using Django display formatting for UI parity."""
    if value is None:
        return None
    return date_format(localtime(value), format="DATETIME_FORMAT", use_l10n=True)


def _raw_datetime(value: timezone.datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _permission_denied_response() -> JsonResponse:
    """Return 403 JSON response for permission denied."""
    return JsonResponse({"ok": False, "error": "Permission denied"}, status=403)


def _json_membership_permission_required[**P, R: HttpResponse](
    view_func: Callable[P, R],
) -> Callable[P, HttpResponse]:
    """Return JSON 403 responses instead of redirects for invitation APIs."""

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
        if not request.user.is_authenticated or not request.user.has_perm(ASTRA_ADD_MEMBERSHIP):
            return _permission_denied_response()
        return view_func(request, *args, **kwargs)

    return wrapper


def _not_found_response() -> JsonResponse:
    """Return 404 JSON response for not found."""
    return JsonResponse({"ok": False, "error": "Not found"}, status=404)


def _bad_request_response(error: str) -> JsonResponse:
    """Return 400 JSON response with error message."""
    return JsonResponse({"ok": False, "error": error}, status=400)


def _format_invitation_row(invitation: AccountInvitation, *, data_only: bool) -> dict[str, Any]:
    """Format an invitation for DataTables response."""
    date_formatter = _raw_datetime if data_only else _display_datetime
    row: dict[str, Any] = {
        "invitation_id": invitation.pk,
        "email": invitation.email,
        "full_name": invitation.full_name or "",
        "note": invitation.note or "",
        "invited_by_username": invitation.invited_by_username,
        "invited_at": date_formatter(invitation.invited_at),
        "send_count": invitation.send_count,
        "last_sent_at": date_formatter(invitation.last_sent_at),
    }

    # Pending-specific fields
    if not invitation.accepted_at and not invitation.dismissed_at:
        row["status"] = "pending"

    # Accepted-specific fields
    if invitation.accepted_at and not invitation.dismissed_at:
        row["status"] = "accepted"
        row["accepted_at"] = date_formatter(invitation.accepted_at)
        row["accepted_username"] = invitation.accepted_username
        row["freeipa_matched_usernames"] = invitation.freeipa_matched_usernames or []

    # Dismissed
    if invitation.dismissed_at:
        row["status"] = "dismissed"
        row["dismissed_at"] = invitation.dismissed_at.isoformat()
        row["dismissed_by_username"] = invitation.dismissed_by_username

    # Organization info if linked
    if invitation.organization_id:
        row["organization_id"] = invitation.organization_id
        row["organization_name"] = invitation.organization.name

    return row


def _build_datatables_response(
    request: HttpRequest,
    queryset: QuerySet[AccountInvitation],
    *,
    data_only: bool,
) -> dict[str, Any]:
    """Build a DataTables-compatible response envelope."""
    try:
        draw = int(request.GET.get("draw", 1))
    except (ValueError, TypeError):
        draw = 1

    try:
        start = int(request.GET.get("start", 0))
    except (ValueError, TypeError):
        start = 0

    try:
        length = int(request.GET.get("length", 50))
    except (ValueError, TypeError):
        length = 50

    # Clamp length to reasonable values
    length = max(1, min(length, 500))

    records_total = queryset.count()

    # Apply search filter if provided
    search_value = request.GET.get("search[value]", "").strip()
    if search_value:
        queryset = queryset.filter(
            Q(email__icontains=search_value) | Q(full_name__icontains=search_value)
        )

    records_filtered = queryset.count()

    # Apply pagination
    end = start + length
    paginated_queryset = queryset[start:end]

    # Format rows
    data = [_format_invitation_row(inv, data_only=data_only) for inv in paginated_queryset]

    return {
        "draw": draw,
        "recordsTotal": records_total,
        "recordsFiltered": records_filtered,
        "data": data,
    }


@require_http_methods(["GET"])
@_json_membership_permission_required
def account_invitations_pending_api(request: HttpRequest) -> JsonResponse:
    """
    List pending invitations in DataTables format.

    Query Parameters:
        - draw: DataTables draw counter
        - start: Pagination offset
        - length: Pagination size
        - search[value]: Search term
    """
    if not request.user.is_authenticated:
        return _permission_denied_response()

    # Filter for pending: not accepted and not dismissed
    queryset = AccountInvitation.objects.filter(
        accepted_at__isnull=True,
        dismissed_at__isnull=True,
    ).order_by("-invited_at")

    response = _build_datatables_response(request, queryset, data_only=False)
    return JsonResponse(response)


@require_http_methods(["GET"])
@_json_membership_permission_required
def account_invitations_pending_detail_api(request: HttpRequest) -> JsonResponse:
    queryset = AccountInvitation.objects.filter(
        accepted_at__isnull=True,
        dismissed_at__isnull=True,
    ).order_by("-invited_at")

    response = _build_datatables_response(request, queryset, data_only=True)
    return JsonResponse(response)


@require_http_methods(["GET"])
@_json_membership_permission_required
def account_invitations_accepted_api(request: HttpRequest) -> JsonResponse:
    """
    List accepted invitations in DataTables format.

    Query Parameters:
        - draw: DataTables draw counter
        - start: Pagination offset
        - length: Pagination size
        - search[value]: Search term
    """
    if not request.user.is_authenticated:
        return _permission_denied_response()

    # Filter for accepted: has accepted_at and not dismissed
    queryset = AccountInvitation.objects.filter(
        accepted_at__isnull=False,
        dismissed_at__isnull=True,
    ).order_by("-accepted_at")

    response = _build_datatables_response(request, queryset, data_only=False)
    return JsonResponse(response)


@require_http_methods(["GET"])
@_json_membership_permission_required
def account_invitations_accepted_detail_api(request: HttpRequest) -> JsonResponse:
    queryset = AccountInvitation.objects.filter(
        accepted_at__isnull=False,
        dismissed_at__isnull=True,
    ).order_by("-accepted_at")

    response = _build_datatables_response(request, queryset, data_only=True)
    return JsonResponse(response)


@require_http_methods(["POST"])
@_json_membership_permission_required
def account_invitations_refresh_api(request: HttpRequest) -> JsonResponse:
    """
    Refresh invitation statuses by checking FreeIPA for matches.

    This endpoint checks all pending invitations against FreeIPA to detect
    if emails have been registered and automatically marks them as accepted.
    """
    try:
        username = get_username(request)
        now = timezone.now()
        refresh_account_invitations(actor_username=username, now=now)
        return JsonResponse({
            "ok": True,
            "message": "Invitations refreshed successfully",
        })
    except Exception:
        logger.exception(
            "Failed to refresh invitations",
            extra=current_exception_log_fields(),
        )
        return JsonResponse({
            "ok": False,
            "error": "Failed to refresh invitations",
        }, status=500)


@require_http_methods(["POST"])
@_json_membership_permission_required
def account_invitations_resend_api(request: HttpRequest, pk: int) -> JsonResponse:
    """
    Resend an invitation email.

    Path Parameters:
        - pk: Invitation ID
    """
    try:
        invitation = AccountInvitation.objects.get(pk=pk)
    except AccountInvitation.DoesNotExist:
        return _not_found_response()

    try:
        username = get_username(request)

        # Check if email now matches a FreeIPA user (transition to accepted)
        _mark_invitation_accepted_from_email_match(invitation)
        invitation.refresh_from_db()

        # If already accepted, indicate that
        if invitation.accepted_at:
            return JsonResponse({
                "ok": True,
                "message": f"Invitation already accepted by {invitation.accepted_username}",
            })

        # Check rate limits
        window_seconds = settings.ACCOUNT_INVITATION_RESEND_WINDOW_SECONDS
        allowed = allow_request(
            scope="account_invitation_resend",
            key_parts=[username, str(invitation.pk)],
            limit=settings.ACCOUNT_INVITATION_RESEND_LIMIT,
            window_seconds=window_seconds,
        )
        if not allowed:
            return JsonResponse({
                "ok": False,
                "error": "Too many resend attempts. Try again shortly.",
            }, status=429)

        # Queue email send
        queued_email = queue_templated_email(
            recipients=[invitation.email],
            sender=settings.DEFAULT_FROM_EMAIL,
            template_name=invitation.email_template_name,
            context=_build_invitation_email_context(
                invitation=invitation,
                actor_username=username,
            ),
        )

        invitation.last_sent_at = timezone.now()
        invitation.send_count += 1
        invitation.save(update_fields=["last_sent_at", "send_count"])

        # Record send attempt
        AccountInvitationSend.objects.create(
            invitation=invitation,
            sent_by_username=username,
            template_name=invitation.email_template_name,
            post_office_email_id=queued_email.id if queued_email else None,
            result=AccountInvitationSend.Result.queued,
        )

        return JsonResponse({
            "ok": True,
            "message": f"Invitation resent to {invitation.email}",
        })

    except Exception:
        logger.exception(
            f"Failed to resend invitation {pk}",
            extra=current_exception_log_fields(),
        )
        return JsonResponse({
            "ok": False,
            "error": "Failed to resend invitation",
        }, status=500)


def _mark_invitation_accepted_from_email_match(invitation: AccountInvitation) -> None:
    """
    Check if invitation email matches a FreeIPA user and mark accepted if so.
    """
    if invitation.accepted_at:
        return

    # Check for email match in FreeIPA
    matched_usernames = find_account_invitation_matches(invitation.email)
    if matched_usernames:
        invitation.accepted_at = timezone.now()
        invitation.accepted_username = matched_usernames[0]
        invitation.freeipa_matched_usernames = matched_usernames
        invitation.freeipa_last_checked_at = timezone.now()
        invitation.save()


@require_http_methods(["POST"])
@_json_membership_permission_required
def account_invitations_dismiss_api(request: HttpRequest, pk: int) -> JsonResponse:
    """
    Dismiss an invitation.

    Path Parameters:
        - pk: Invitation ID
    """
    try:
        invitation = AccountInvitation.objects.get(pk=pk)
    except AccountInvitation.DoesNotExist:
        return _not_found_response()

    try:
        username = get_username(request)
        invitation.dismissed_at = timezone.now()
        invitation.dismissed_by_username = username
        invitation.save(update_fields=["dismissed_at", "dismissed_by_username"])

        return JsonResponse({
            "ok": True,
            "message": "Invitation dismissed",
        })
    except Exception:
        logger.exception(
            f"Failed to dismiss invitation {pk}",
            extra=current_exception_log_fields(),
        )
        return JsonResponse({
            "ok": False,
            "error": "Failed to dismiss invitation",
        }, status=500)


@require_http_methods(["POST"])
@_json_membership_permission_required
def account_invitations_bulk_api(request: HttpRequest) -> JsonResponse:
    """
    Perform bulk actions on invitations.

    POST Parameters:
        - bulk_action: "resend" or "dismiss"
        - bulk_scope: "pending" or "accepted"
        - selected: JSON list of invitation IDs
    """
    try:
        # Accept both JSON payloads (Vue fetch) and form payloads (legacy).
        if request.content_type and request.content_type.startswith("application/json"):
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return _bad_request_response("Invalid JSON payload")

            if not isinstance(payload, dict):
                return _bad_request_response("Invalid JSON payload")

            bulk_action = str(payload.get("bulk_action", "")).strip()
            bulk_scope = str(payload.get("bulk_scope", "")).strip()
            selected_raw = payload.get("selected", [])
        else:
            bulk_action = request.POST.get("bulk_action", "").strip()
            bulk_scope = request.POST.get("bulk_scope", "").strip()
            selected_list = request.POST.getlist("selected")
            selected_raw = selected_list if selected_list else request.POST.get("selected", "[]")

        # Parse selected IDs
        try:
            if isinstance(selected_raw, list):
                selected_ids = selected_raw
            elif isinstance(selected_raw, str):
                parsed = json.loads(selected_raw)
                selected_ids = parsed if isinstance(parsed, list) else [parsed]
            else:
                selected_ids = [selected_raw]
        except (json.JSONDecodeError, TypeError):
            return _bad_request_response("Invalid selected IDs format")

        # Validate inputs
        if not bulk_action or bulk_action not in ("resend", "dismiss"):
            return _bad_request_response("Invalid bulk_action")

        if not bulk_scope or bulk_scope not in ("pending", "accepted"):
            return _bad_request_response("Invalid bulk_scope")

        # Convert to integers
        try:
            selected_ids = [int(id_) for id_ in selected_ids]
        except (ValueError, TypeError):
            return _bad_request_response("Invalid invitation IDs")

        username = get_username(request)

        # Apply action based on bulk_action and scope
        if bulk_action == "dismiss":
            queryset = AccountInvitation.objects.filter(pk__in=selected_ids)
            if bulk_scope == "pending":
                queryset = queryset.filter(accepted_at__isnull=True, dismissed_at__isnull=True)
            elif bulk_scope == "accepted":
                queryset = queryset.filter(accepted_at__isnull=False, dismissed_at__isnull=True)

            updated_count = queryset.update(
                dismissed_at=timezone.now(),
                dismissed_by_username=username,
            )
            return JsonResponse({
                "ok": True,
                "message": f"Dismissed {updated_count} invitation(s)",
            })

        elif bulk_action == "resend":
            # Resend only works on pending invitations
            if bulk_scope != "pending":
                return _bad_request_response("Resend only works on pending invitations")

            queryset = AccountInvitation.objects.filter(
                pk__in=selected_ids,
                accepted_at__isnull=True,
                dismissed_at__isnull=True,
            )

            # Check rate limit for bulk resend
            window_seconds = settings.ACCOUNT_INVITATION_BULK_SEND_WINDOW_SECONDS
            allowed = allow_request(
                scope="account_invitation_bulk_resend",
                key_parts=[username],
                limit=settings.ACCOUNT_INVITATION_RESEND_LIMIT,
                window_seconds=window_seconds,
            )
            if not allowed:
                return JsonResponse({
                    "ok": False,
                    "error": "Too many resend attempts. Try again shortly.",
                }, status=429)

            resent_count = 0
            for invitation in queryset:
                try:
                    queued_email = queue_templated_email(
                        recipients=[invitation.email],
                        sender=settings.DEFAULT_FROM_EMAIL,
                        template_name=invitation.email_template_name,
                        context=_build_invitation_email_context(
                            invitation=invitation,
                            actor_username=username,
                        ),
                    )
                    invitation.last_sent_at = timezone.now()
                    invitation.send_count += 1
                    invitation.save(update_fields=["last_sent_at", "send_count"])

                    AccountInvitationSend.objects.create(
                        invitation=invitation,
                        sent_by_username=username,
                        template_name=invitation.email_template_name,
                        post_office_email_id=queued_email.id if queued_email else None,
                        result=AccountInvitationSend.Result.queued,
                    )
                    resent_count += 1
                except Exception:
                    logger.exception(
                        f"Failed to resend invitation {invitation.pk} in bulk",
                        extra=current_exception_log_fields(),
                    )

            return JsonResponse({
                "ok": True,
                "message": f"Resent {resent_count} invitation(s)",
            })

    except Exception:
        logger.exception(
            "Bulk invitation action failed",
            extra=current_exception_log_fields(),
        )
        return JsonResponse({
            "ok": False,
            "error": "Bulk action failed",
        }, status=500)
