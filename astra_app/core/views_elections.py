from __future__ import annotations

import datetime
import json
import random
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Prefetch, Sum
from django.db.models.functions import TruncDate
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from post_office.models import EmailTemplate

from core import elections_services
from core.backends import FreeIPAUser
from core.elections_services import (
    ElectionError,
    ElectionNotOpenError,
    InvalidBallotError,
    InvalidCredentialError,
    election_genesis_chain_hash,
    election_quorum_status,
    eligible_voters_from_memberships,
    issue_voting_credentials_from_memberships_detailed,
    submit_ballot,
)
from core.email_context import user_email_context
from core.forms_elections import (
    CandidateWizardFormSet,
    ElectionDetailsForm,
    ElectionEndDateForm,
    ElectionVotingEmailForm,
    ExclusionGroupWizardFormSet,
)
from core.models import (
    AuditLogEntry,
    Ballot,
    Candidate,
    Election,
    ExclusionGroup,
    ExclusionGroupCandidate,
    Membership,
    OrganizationSponsorship,
    VotingCredential,
)
from core.permissions import ASTRA_ADD_ELECTION, json_permission_required
from core.rate_limit import allow_request
from core.templated_email import (
    placeholderize_empty_values,
    render_templated_email_preview,
    render_templated_email_preview_response,
)
from core.user_labels import user_choice_from_freeipa, user_label
from core.views_send_mail import _CSV_SESSION_KEY

_RECEIPT_RE = re.compile(r"^[0-9a-f]{64}$")


def _get_freeipa_timezone_name(user: FreeIPAUser) -> str | None:
    """Return the user's configured FreeIPA timezone (IANA name), if set."""

    raw = user._user_data.get("fasTimezone")
    if raw is None:
        raw = user._user_data.get("fastimezone")

    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw or "").strip() or None


@require_GET
def ballot_verify(request):
    receipt_raw = str(request.GET.get("receipt") or "").strip()
    receipt = receipt_raw.lower()

    has_query = bool(receipt_raw)
    is_valid_receipt = bool(_RECEIPT_RE.fullmatch(receipt)) if receipt else False

    client_ip = str(request.META.get("REMOTE_ADDR") or "").strip() or "unknown"

    if has_query and not allow_request(
        scope="elections.ballot_verify",
        key_parts=[client_ip],
        limit=settings.ELECTION_RATE_LIMIT_BALLOT_VERIFY_LIMIT,
        window_seconds=settings.ELECTION_RATE_LIMIT_BALLOT_VERIFY_WINDOW_SECONDS,
    ):
        return render(
            request,
            "core/ballot_verify.html",
            {
                "receipt": receipt_raw,
                "has_query": has_query,
                "is_valid_receipt": is_valid_receipt,
                "found": False,
                "election": None,
                "election_status": "",
                "submitted_date": "",
                "is_superseded": False,
                "is_final_ballot": False,
                "public_ballots_url": "",
                "audit_log_url": "",
                "rate_limited": True,
            },
            status=429,
        )

    ballot: Ballot | None = None
    if is_valid_receipt:
        ballot = (
            Ballot.objects.select_related("election", "superseded_by")
            .only(
                "ballot_hash",
                "credential_public_id",
                "created_at",
                "is_counted",
                "superseded_by_id",
                "election__id",
                "election__name",
                "election__status",
                "election__public_ballots_file",
                "election__public_audit_file",
            )
            .filter(ballot_hash=receipt)
            .first()
        )

    found = ballot is not None
    election: Election | None = ballot.election if ballot is not None else None
    election_status = str(election.status) if election is not None else ""

    is_superseded = bool(ballot is not None and ballot.superseded_by_id)
    is_final_ballot = bool(found and not is_superseded)

    # Privacy guardrail: never reveal ranking, IPs, or precise timestamps.
    submitted_date = ballot.created_at.date().isoformat() if ballot is not None else ""

    public_ballots_url = ""
    if election is not None and election.status == Election.Status.tallied:
        public_ballots_url = election.public_ballots_file.url if election.public_ballots_file else reverse(
            "election-public-ballots", args=[election.id]
        )
    audit_log_url = (
        reverse("election-audit-log", args=[election.id])
        if election is not None and election.status == Election.Status.tallied
        else ""
    )

    verification_snippet = ""
    if found and election is not None and ballot is not None:
        candidate_rows = Candidate.objects.filter(election=election).values_list("freeipa_username", "id")
        candidate_ids_by_username = {str(username): int(cid) for username, cid in candidate_rows}

        lines: list[str] = [
            "# Copy/paste these values into verify-ballot-hash.py",
            f"election_id = {election.id}",
            f'credential_public_id = "{ballot.credential_public_id}"',
            "candidate_ids_by_username = {",
        ]
        for username in sorted(candidate_ids_by_username.keys(), key=str.lower):
            cid = candidate_ids_by_username[username]
            lines.append(f'    "{username}": {cid},')
        lines.append("}")

        verification_snippet = "\n".join(lines)

    return render(
        request,
        "core/ballot_verify.html",
        {
            "receipt": receipt_raw,
            "has_query": has_query,
            "is_valid_receipt": is_valid_receipt,
            "found": found,
            "election": election,
            "election_status": election_status,
            "submitted_date": submitted_date,
            "is_superseded": is_superseded,
            "is_final_ballot": is_final_ballot,
            "public_ballots_url": public_ballots_url,
            "audit_log_url": audit_log_url,
            "rate_limited": False,
            "verification_snippet": verification_snippet,
        },
    )


@require_GET
def election_algorithm(request):
    return render(
        request,
        "core/election_algorithm.html",
        {},
    )


@require_POST
@json_permission_required(ASTRA_ADD_ELECTION)
def election_email_render_preview(request, election_id: int) -> JsonResponse:
    def _parse_datetime_local(value: str) -> datetime.datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.datetime.fromisoformat(raw)
        except ValueError:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt

    if election_id == 0:
        start_dt = _parse_datetime_local(str(request.POST.get("start_datetime") or "")) or timezone.now()
        end_dt = _parse_datetime_local(str(request.POST.get("end_datetime") or "")) or start_dt
        number_of_seats_raw = str(request.POST.get("number_of_seats") or "").strip()
        try:
            number_of_seats = int(number_of_seats_raw)
        except ValueError:
            number_of_seats = 1

        election = Election(
            id=0,
            name=str(request.POST.get("name") or ""),
            description=str(request.POST.get("description") or ""),
            url=str(request.POST.get("url") or ""),
            start_datetime=start_dt,
            end_datetime=end_dt,
            number_of_seats=number_of_seats,
            status=Election.Status.draft,
            eligible_group_cn=str(request.POST.get("eligible_group_cn") or ""),
        )
    else:
        election = Election.objects.exclude(status=Election.Status.deleted).filter(pk=election_id).first()
        if election is None:
            raise Http404

        # Allow the live preview to reflect unsaved edits on the page.
        name_raw = str(request.POST.get("name") or "")
        if name_raw:
            election.name = name_raw
        description_raw = str(request.POST.get("description") or "")
        if description_raw:
            election.description = description_raw
        url_raw = str(request.POST.get("url") or "")
        if url_raw:
            election.url = url_raw

        start_dt = _parse_datetime_local(str(request.POST.get("start_datetime") or ""))
        if start_dt is not None:
            election.start_datetime = start_dt
        end_dt = _parse_datetime_local(str(request.POST.get("end_datetime") or ""))
        if end_dt is not None:
            election.end_datetime = end_dt

        number_of_seats_raw = str(request.POST.get("number_of_seats") or "").strip()
        if number_of_seats_raw:
            try:
                election.number_of_seats = int(number_of_seats_raw)
            except ValueError:
                pass
        eligible_group_raw = str(request.POST.get("eligible_group_cn") or "")
        if eligible_group_raw or "eligible_group_cn" in request.POST:
            election.eligible_group_cn = eligible_group_raw

    username = str(request.user.get_username() or "").strip() or "preview"
    user_context = user_email_context(username=username)

    context: dict[str, object] = {
        **user_context,
        "election_id": election.id,
        "election_name": election.name,
        "election_description": election.description,
        "election_url": election.url,
        "election_start_datetime": election.start_datetime,
        "election_end_datetime": election.end_datetime,
        "election_number_of_seats": election.number_of_seats,
        "credential_public_id": "PREVIEW",
        "vote_url": elections_services.election_vote_url(request=request, election=election),
        "vote_url_with_credential_fragment": elections_services.election_vote_url_with_credential_fragment(
            request=request,
            election=election,
            credential_public_id="PREVIEW",
        ),
    }

    return render_templated_email_preview_response(request=request, context=context)


@require_GET
def elections_list(request):
    can_manage_elections = request.user.has_perm(ASTRA_ADD_ELECTION)
    qs = (
        Election.objects.exclude(status=Election.Status.deleted)
        .only("id", "name", "status", "start_datetime", "end_datetime")
        .order_by("-start_datetime", "id")
    )
    if not can_manage_elections:
        qs = qs.exclude(status=Election.Status.draft)
    elections = list(qs)

    open_statuses = {Election.Status.open, Election.Status.draft}
    past_statuses = {Election.Status.closed, Election.Status.tallied}

    open_elections: list[Election] = []
    past_elections: list[Election] = []
    for election in elections:
        if election.status in past_statuses:
            past_elections.append(election)
        elif election.status in open_statuses:
            open_elections.append(election)
        else:
            open_elections.append(election)
    return render(
        request,
        "core/elections_list.html",
        {
            "open_elections": open_elections,
            "past_elections": past_elections,
            "can_manage_elections": can_manage_elections,
        },
    )


@require_http_methods(["GET", "POST"])
def election_new(request):
    return election_edit(request, election_id=0)


@require_GET
@json_permission_required(ASTRA_ADD_ELECTION)
def election_eligible_users_search(request, election_id: int):
    eligible_group_cn = str(request.GET.get("eligible_group_cn") or "").strip()

    if election_id == 0:
        start_datetime_raw = str(request.GET.get("start_datetime") or "").strip()
        if not start_datetime_raw:
            # Allow searching before the start datetime is filled in.
            start_dt = timezone.now()
        else:
            try:
                # Accept `datetime-local` values like `YYYY-MM-DDTHH:MM`.
                start_dt = datetime.datetime.fromisoformat(start_datetime_raw)
            except ValueError:
                return JsonResponse({"results": []})
            if timezone.is_naive(start_dt):
                start_dt = timezone.make_aware(start_dt)
        election = Election(
            name="",
            description="",
            url="",
            start_datetime=start_dt,
            end_datetime=start_dt,
            number_of_seats=1,
            status=Election.Status.draft,
        )
    else:
        election = (
            Election.objects.exclude(status=Election.Status.deleted)
            .filter(pk=election_id)
            .only("id", "start_datetime", "eligible_group_cn")
            .first()
        )
        if election is None:
            raise Http404

    if "eligible_group_cn" in request.GET:
        # Allow the edit UI to preview eligibility before the draft is saved.
        # This must also support clearing the field (empty string).
        election.eligible_group_cn = eligible_group_cn

    q = str(request.GET.get("q") or "").strip()
    q_lower = q.lower()

    eligible = eligible_voters_from_memberships(election=election)
    eligible_usernames = {v.username for v in eligible}

    count_only = str(request.GET.get("count_only") or "").strip()
    if count_only in {"1", "true", "True", "yes", "on"}:
        return JsonResponse({"count": len(eligible_usernames)})

    results: list[dict[str, str]] = []
    for username in sorted(eligible_usernames, key=str.lower):
        if q_lower and q_lower not in username.lower():
            continue

        try:
            user = FreeIPAUser.get(username)
        except Exception:
            # FreeIPA may be unavailable during local development or transiently
            # during a request. Eligibility is computed from local DB state, so
            # degrade gracefully by returning usernames without full names.
            user = None

        text = user_label(username, user=user)

        results.append({"id": username, "text": text})
        if len(results) >= 20:
            break

    return JsonResponse({"results": results})


@require_GET
@json_permission_required(ASTRA_ADD_ELECTION)
def election_nomination_users_search(request, election_id: int):
    """Eligible-user search for the nomination UI.

    Nomination eligibility is based on membership age/status only and must not
    be filtered by the election's optional eligible-group voting restriction.
    """

    if election_id == 0:
        start_datetime_raw = str(request.GET.get("start_datetime") or "").strip()
        if not start_datetime_raw:
            start_dt = timezone.now()
        else:
            try:
                start_dt = datetime.datetime.fromisoformat(start_datetime_raw)
            except ValueError:
                return JsonResponse({"results": []})
            if timezone.is_naive(start_dt):
                start_dt = timezone.make_aware(start_dt)
        election = Election(
            name="",
            description="",
            url="",
            start_datetime=start_dt,
            end_datetime=start_dt,
            number_of_seats=1,
            status=Election.Status.draft,
            eligible_group_cn="",
        )
    else:
        election = (
            Election.objects.exclude(status=Election.Status.deleted)
            .filter(pk=election_id)
            .only("id", "start_datetime")
            .first()
        )
        if election is None:
            raise Http404

        # Do not apply the voting restriction when searching for candidates/nominators.
        election.eligible_group_cn = ""

    q = str(request.GET.get("q") or "").strip()
    q_lower = q.lower()

    eligible = eligible_voters_from_memberships(election=election)
    eligible_usernames = {v.username for v in eligible}

    results: list[dict[str, str]] = []
    for username in sorted(eligible_usernames, key=str.lower):
        if q_lower and q_lower not in username.lower():
            continue

        try:
            user = FreeIPAUser.get(username)
        except Exception:
            user = None

        text = user_label(username, user=user)

        results.append({"id": username, "text": text})
        if len(results) >= 20:
            break

    return JsonResponse({"results": results})

@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_edit(request, election_id: int):
    is_create = election_id == 0
    election = (
        None
        if is_create
        else Election.objects.exclude(status=Election.Status.deleted).filter(pk=election_id).first()
    )
    if not is_create and election is None:
        raise Http404

    templates = list(EmailTemplate.objects.all().order_by("name"))
    default_template = EmailTemplate.objects.filter(name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME).first()

    eligible_voter_usernames: set[str] = set()
    nomination_eligible_usernames: set[str] = set()
    if election is not None:
        eligible_voter_usernames = {v.username for v in eligible_voters_from_memberships(election=election)}
        election_for_nomination = Election(
            name="",
            description="",
            url="",
            start_datetime=election.start_datetime,
            end_datetime=election.end_datetime,
            number_of_seats=election.number_of_seats,
            status=election.status,
            eligible_group_cn="",
        )
        nomination_eligible_usernames = {v.username for v in eligible_voters_from_memberships(election=election_for_nomination)}

    def _user_choice(username: str) -> tuple[str, str]:
        return user_choice_from_freeipa(username)

    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip()

        if action == "delete_draft":
            if election is None:
                messages.error(request, "Save the draft first.")
                return redirect("elections")
            if election.status != Election.Status.draft:
                messages.error(request, "Only draft elections can be deleted.")
                return redirect("election-edit", election_id=election.id)

            election.delete()
            messages.success(request, "Draft deleted.")
            return redirect("elections")

        if action in {"end_election", "end_election_and_tally"}:
            return HttpResponseBadRequest("Ending elections is not supported from the edit page.")

        details_form = ElectionDetailsForm(request.POST, instance=election)
        email_form = ElectionVotingEmailForm(request.POST)

        if election is not None and election.status != Election.Status.draft:
            # Once an election is started, configuration is effectively frozen.
            # The only allowed edit is extending the end datetime while open.
            for field_name in (
                "name",
                "description",
                "url",
                "start_datetime",
                "number_of_seats",
                "quorum",
            ):
                details_form.fields[field_name].disabled = True
            if election.status != Election.Status.open:
                details_form.fields["end_datetime"].disabled = True
        if action == "save_draft":
            candidate_formset = CandidateWizardFormSet(
                request.POST,
                queryset=(
                    Candidate.objects.filter(election=election).order_by("id")
                    if election is not None
                    else Candidate.objects.none()
                ),
                prefix="candidates",
            )
            group_formset = ExclusionGroupWizardFormSet(
                request.POST,
                queryset=(
                    ExclusionGroup.objects.filter(election=election).order_by("name", "id")
                    if election is not None
                    else ExclusionGroup.objects.none()
                ),
                prefix="groups",
            )
        elif action == "extend_end":
            candidate_formset = CandidateWizardFormSet(
                queryset=(
                    Candidate.objects.filter(election=election).order_by("id")
                    if election is not None
                    else Candidate.objects.none()
                ),
                prefix="candidates",
            )
            group_formset = ExclusionGroupWizardFormSet(
                queryset=(
                    ExclusionGroup.objects.filter(election=election).order_by("name", "id")
                    if election is not None
                    else ExclusionGroup.objects.none()
                ),
                prefix="groups",
            )
        else:
            candidate_formset = CandidateWizardFormSet(queryset=Candidate.objects.none(), prefix="candidates")
            group_formset = ExclusionGroupWizardFormSet(queryset=ExclusionGroup.objects.none(), prefix="groups")

        if election is not None:
            eligible_voter_usernames = {v.username for v in eligible_voters_from_memberships(election=election)}
            election_for_nomination = Election(
                name="",
                description="",
                url="",
                start_datetime=election.start_datetime,
                end_datetime=election.end_datetime,
                number_of_seats=election.number_of_seats,
                status=election.status,
                eligible_group_cn="",
            )
            nomination_eligible_usernames = {v.username for v in eligible_voters_from_memberships(election=election_for_nomination)}

        def _configure_candidate_form_choices() -> None:
            ajax_election_id = election.id if election is not None else 0
            ajax_url_candidate = request.build_absolute_uri(
                reverse("election-eligible-users-search", args=[ajax_election_id])
            )
            ajax_url_nominator = request.build_absolute_uri(
                reverse("election-nomination-users-search", args=[ajax_election_id])
            )
            for form in candidate_formset.forms:
                freeipa_value = str(
                    form.data.get(form.add_prefix("freeipa_username"))
                    or form.initial.get("freeipa_username")
                    or form.instance.freeipa_username
                    or ""
                ).strip()
                if freeipa_value:
                    form.fields["freeipa_username"].choices = [_user_choice(freeipa_value)]
                form.fields["freeipa_username"].widget.attrs["data-ajax-url"] = ajax_url_candidate
                form.fields["freeipa_username"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"

                nominated_value = str(
                    form.data.get(form.add_prefix("nominated_by"))
                    or form.initial.get("nominated_by")
                    or form.instance.nominated_by
                    or ""
                ).strip()
                if nominated_value:
                    form.fields["nominated_by"].choices = [_user_choice(nominated_value)]
                form.fields["nominated_by"].widget.attrs["data-ajax-url"] = ajax_url_nominator
                form.fields["nominated_by"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"

            candidate_formset.empty_form.fields["freeipa_username"].widget.attrs["data-ajax-url"] = ajax_url_candidate
            candidate_formset.empty_form.fields["nominated_by"].widget.attrs["data-ajax-url"] = ajax_url_nominator
            candidate_formset.empty_form.fields["freeipa_username"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"
            candidate_formset.empty_form.fields["nominated_by"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"

        def _configure_group_choices() -> None:
            if election is None:
                # Create-mode: candidates aren't in the DB yet. Accept selections for candidates
                # that are being submitted in the same POST.
                total_raw = str(request.POST.get("candidates-TOTAL_FORMS") or "0").strip()
                try:
                    total = int(total_raw)
                except ValueError:
                    total = 0
                submitted_usernames: list[str] = []
                for i in range(max(total, 0)):
                    if str(request.POST.get(f"candidates-{i}-DELETE") or "").strip():
                        continue
                    username = str(request.POST.get(f"candidates-{i}-freeipa_username") or "").strip()
                    if username:
                        submitted_usernames.append(username)
                submitted_unique = sorted(set(submitted_usernames), key=str.lower)
                choices = [user_choice_from_freeipa(u) for u in submitted_unique]
            else:
                candidates_qs = Candidate.objects.filter(election=election).only("freeipa_username")
                choices = [
                    user_choice_from_freeipa(c.freeipa_username)
                    for c in candidates_qs
                    if c.freeipa_username
                ]

            # The UI adds new rows by cloning the formset's empty-form template.
            # Populate its choices so the dynamic row has options immediately.
            group_formset.empty_form.fields["candidate_usernames"].choices = choices

            for form in group_formset.forms:
                selected = form.data.getlist(form.add_prefix("candidate_usernames")) if hasattr(form.data, "getlist") else []
                existing = {u for u, _label in choices}
                extra = [user_choice_from_freeipa(u) for u in selected if u and u not in existing]
                form.fields["candidate_usernames"].choices = [*choices, *extra]

        if action == "save_draft":
            _configure_candidate_form_choices()
            _configure_group_choices()

        if election is not None and election.status != Election.Status.draft:
            for form in candidate_formset.forms:
                for field in form.fields.values():
                    field.disabled = True
            for form in group_formset.forms:
                for field in form.fields.values():
                    field.disabled = True

        formsets_ok = True
        if action == "save_draft":
            formsets_ok = bool(candidate_formset.is_valid() and group_formset.is_valid())

        if action == "save_draft" and election is not None and election.status != Election.Status.draft:
            messages.error(request, "This election is no longer in draft; draft changes are locked.")
            formsets_ok = False

        if action == "extend_end":
            if election is None:
                messages.error(request, "Save the draft first.")
            elif election.status != Election.Status.open:
                messages.error(request, "Only open elections can be extended.")
            else:
                end_form = ElectionEndDateForm(request.POST, instance=election)
                if not end_form.is_valid():
                    for msg in end_form.errors.get("end_datetime", []):
                        details_form.add_error("end_datetime", msg)
                    messages.error(request, "Please correct the errors below.")
                    end_form = None
                new_end = end_form.cleaned_data.get("end_datetime") if end_form is not None else None
                if isinstance(new_end, datetime.datetime):
                    actor = str(request.user.username or "").strip() or None
                    try:
                        elections_services.extend_election_end_datetime(
                            election=election,
                            new_end_datetime=new_end,
                            actor=actor,
                        )
                    except ElectionError as exc:
                        details_form.add_error("end_datetime", str(exc))
                    else:
                        messages.success(request, "Election end date extended.")
                        return redirect("election-edit", election_id=election.id)

        email_save_mode = str(request.POST.get("email_save_mode") or "").strip()

        if action == "save_draft" and details_form.is_valid() and email_form.is_valid() and formsets_ok:
            election = details_form.save(commit=False)
            election.status = Election.Status.draft

            if election_id == 0 or email_save_mode != "keep_existing":
                template_id = email_form.cleaned_data.get("email_template_id")
                template = EmailTemplate.objects.filter(pk=int(template_id)).first() if template_id else None
                election.voting_email_template = template
                election.voting_email_subject = str(email_form.cleaned_data.get("subject") or "")
                election.voting_email_html = str(email_form.cleaned_data.get("html_content") or "")
                election.voting_email_text = str(email_form.cleaned_data.get("text_content") or "")

            election.save()

            eligible_voter_usernames = {v.username for v in eligible_voters_from_memberships(election=election)}
            election_for_nomination = Election(
                name="",
                description="",
                url="",
                start_datetime=election.start_datetime,
                end_datetime=election.end_datetime,
                number_of_seats=election.number_of_seats,
                status=election.status,
                eligible_group_cn="",
            )
            nomination_eligible_usernames = {v.username for v in eligible_voters_from_memberships(election=election_for_nomination)}

            # Candidates
            for form in candidate_formset.forms:
                if not hasattr(form, "cleaned_data"):
                    continue
                if form.cleaned_data.get("DELETE"):
                    if form.instance.pk:
                        form.instance.delete()
                    continue

                username = str(form.cleaned_data.get("freeipa_username") or "").strip()
                nominator = str(form.cleaned_data.get("nominated_by") or "").strip()
                if not username:
                    continue
                if username not in eligible_voter_usernames:
                    form.add_error("freeipa_username", "User is not eligible.")
                    continue
                if nominator and nominator not in nomination_eligible_usernames:
                    form.add_error("nominated_by", "User is not eligible.")
                    continue

                candidate = form.save(commit=False)
                candidate.election = election
                candidate.save()

            # Exclusion groups
            for form in group_formset.forms:
                if not hasattr(form, "cleaned_data"):
                    continue
                if form.cleaned_data.get("DELETE"):
                    if form.instance.pk:
                        form.instance.delete()
                    continue

                group_name = str(form.cleaned_data.get("name") or "").strip()
                if not group_name:
                    continue

                group = form.save(commit=False)
                group.election = election
                group.save()

                selected_usernames = [str(u).strip() for u in (form.cleaned_data.get("candidate_usernames") or [])]
                selected_usernames = [u for u in selected_usernames if u]
                candidates = list(
                    Candidate.objects.filter(election=election, freeipa_username__in=selected_usernames).only("id")
                )
                by_username = {c.freeipa_username: c for c in candidates}

                ExclusionGroupCandidate.objects.filter(exclusion_group=group).delete()
                for u in selected_usernames:
                    c = by_username.get(u)
                    if c is None:
                        continue
                    ExclusionGroupCandidate.objects.create(exclusion_group=group, candidate=c)

            messages.success(request, "Draft saved.")
            return redirect("election-edit", election_id=election.id)

        if action == "start_election":
            if election is None:
                messages.error(request, "Save the draft first.")
            elif election.status != Election.Status.draft:
                messages.error(request, "Only draft elections can be started.")
            elif not details_form.is_valid() or not email_form.is_valid():
                messages.error(request, "Please correct the errors below.")
            elif not Candidate.objects.filter(election=election).exists():
                messages.error(request, "Add at least one candidate before starting the election.")
            else:
                election = details_form.save(commit=False)

                # Align the published start timestamp with when the election actually opens.
                # The draft's start_datetime is only a planned window and may drift from real operations.
                election.start_datetime = timezone.now()

                template_id = email_form.cleaned_data.get("email_template_id")
                template = EmailTemplate.objects.filter(pk=int(template_id)).first() if template_id else None
                election.voting_email_template = template
                election.voting_email_subject = str(email_form.cleaned_data.get("subject") or "")
                election.voting_email_html = str(email_form.cleaned_data.get("html_content") or "")
                election.voting_email_text = str(email_form.cleaned_data.get("text_content") or "")

                election.status = Election.Status.open
                election.save()

                credentials = issue_voting_credentials_from_memberships_detailed(election=election)
                emailed = 0
                skipped = 0
                failures = 0

                subject_template = election.voting_email_subject
                html_template = election.voting_email_html
                text_template = election.voting_email_text
                use_snapshot = bool(subject_template.strip() or html_template.strip() or text_template.strip())

                for cred in credentials:
                    username = str(cred.freeipa_username or "").strip()
                    if not username:
                        skipped += 1
                        continue

                    try:
                        user = FreeIPAUser.get(username)
                    except Exception:
                        failures += 1
                        continue
                    if user is None or not user.email:
                        skipped += 1
                        continue

                    tz_name = _get_freeipa_timezone_name(user)

                    try:
                        elections_services.send_voting_credential_email(
                            request=request,
                            election=election,
                            username=username,
                            email=user.email,
                            credential_public_id=str(cred.public_id),
                            tz_name=tz_name,
                            subject_template=subject_template if use_snapshot else None,
                            html_template=html_template if use_snapshot else None,
                            text_template=text_template if use_snapshot else None,
                        )
                    except Exception:
                        failures += 1
                        continue
                    emailed += 1

                AuditLogEntry.objects.create(
                    election=election,
                    event_type="election_started",
                    payload={
                        "eligible_voters": len(credentials),
                        "emailed": emailed,
                        "skipped": skipped,
                        "failures": failures,
                        "genesis_chain_hash": election_genesis_chain_hash(election.id),
                    },
                    is_public=True,
                )

                if emailed:
                    messages.success(request, f"Election started; emailed {emailed} voter(s).")
                if skipped:
                    messages.warning(request, f"Skipped {skipped} voter(s) (missing user/email).")
                if failures:
                    messages.error(request, f"Failed to email {failures} voter(s).")
                return redirect("election-detail", election_id=election.id)

        messages.error(request, "Please correct the errors below.")
    else:
        details_form = ElectionDetailsForm(instance=election)

        if election is not None and election.status != Election.Status.draft:
            for field_name in (
                "name",
                "description",
                "url",
                "start_datetime",
                "number_of_seats",
                "quorum",
            ):
                details_form.fields[field_name].disabled = True
            if election.status != Election.Status.open:
                details_form.fields["end_datetime"].disabled = True

        selected_template = default_template
        if election is not None and election.voting_email_template_id is not None:
            selected_template = election.voting_email_template

        initial_email = {
            "email_template_id": selected_template.pk if selected_template is not None else "",
            "subject": "",
            "html_content": "",
            "text_content": "",
        }

        if election is not None and (
            election.voting_email_subject.strip()
            or election.voting_email_html.strip()
            or election.voting_email_text.strip()
        ):
            initial_email["subject"] = election.voting_email_subject
            initial_email["html_content"] = election.voting_email_html
            initial_email["text_content"] = election.voting_email_text
        elif selected_template is not None:
            initial_email["subject"] = selected_template.subject or ""
            initial_email["html_content"] = selected_template.html_content or ""
            initial_email["text_content"] = selected_template.content or ""

        email_form = ElectionVotingEmailForm(initial=initial_email)

        candidate_formset = CandidateWizardFormSet(
            queryset=Candidate.objects.filter(election=election).order_by("id") if election else Candidate.objects.none(),
            prefix="candidates",
        )
        group_formset = ExclusionGroupWizardFormSet(
            queryset=ExclusionGroup.objects.filter(election=election).order_by("name", "id") if election else ExclusionGroup.objects.none(),
            prefix="groups",
        )

        if election is not None and election.status != Election.Status.draft:
            for form in candidate_formset.forms:
                for field in form.fields.values():
                    field.disabled = True
            for form in group_formset.forms:
                for field in form.fields.values():
                    field.disabled = True

        ajax_election_id = election.id if election is not None else 0
        ajax_url_candidate = request.build_absolute_uri(reverse("election-eligible-users-search", args=[ajax_election_id]))
        ajax_url_nominator = request.build_absolute_uri(reverse("election-nomination-users-search", args=[ajax_election_id]))
        for form in candidate_formset.forms:
            freeipa = str(form.instance.freeipa_username or "").strip()
            if freeipa:
                form.fields["freeipa_username"].choices = [_user_choice(freeipa)]
            form.fields["freeipa_username"].widget.attrs["data-ajax-url"] = ajax_url_candidate
            form.fields["freeipa_username"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"

            nominator = str(form.instance.nominated_by or "").strip()
            if nominator:
                form.fields["nominated_by"].choices = [_user_choice(nominator)]
            form.fields["nominated_by"].widget.attrs["data-ajax-url"] = ajax_url_nominator
            form.fields["nominated_by"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"

        candidate_formset.empty_form.fields["freeipa_username"].widget.attrs["data-ajax-url"] = ajax_url_candidate
        candidate_formset.empty_form.fields["nominated_by"].widget.attrs["data-ajax-url"] = ajax_url_nominator
        candidate_formset.empty_form.fields["freeipa_username"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"
        candidate_formset.empty_form.fields["nominated_by"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"

        if election is not None:
            candidates_qs = Candidate.objects.filter(election=election).only("freeipa_username")
            choices = [
                user_choice_from_freeipa(c.freeipa_username)
                for c in candidates_qs
                if c.freeipa_username
            ]
            for form in group_formset.forms:
                form.fields["candidate_usernames"].choices = choices
                if form.instance.pk:
                    selected = list(
                        form.instance.candidates.order_by("freeipa_username", "id").values_list("freeipa_username", flat=True)
                    )
                    form.initial["candidate_usernames"] = selected

            # Used for the dynamic "Add exclusion group" rows.
            group_formset.empty_form.fields["candidate_usernames"].choices = choices

    rendered_preview: dict[str, str] = {"html": "", "text": "", "subject": ""}
    email_template_variables: list[tuple[str, str]] = []

    preview_election = election if election is not None else Election(id=0)
    username = str(request.user.get_username() or "").strip() or "preview"
    user_context = user_email_context(username=username)

    try:
        preview_context: dict[str, object] = {
            **user_context,
            "election_id": int(preview_election.id or 0),
            "election_name": str(details_form.instance.name or ""),
            "election_description": str(details_form.instance.description or ""),
            "election_url": str(details_form.instance.url or ""),
            "election_start_datetime": details_form.instance.start_datetime or "",
            "election_end_datetime": details_form.instance.end_datetime or "",
            "election_number_of_seats": details_form.instance.number_of_seats or "",
            "credential_public_id": "PREVIEW",
            "vote_url": elections_services.election_vote_url(request=request, election=preview_election),
            "vote_url_with_credential_fragment": elections_services.election_vote_url_with_credential_fragment(
                request=request,
                election=preview_election,
                credential_public_id="PREVIEW",
            ),
        }

        preview_context_for_examples = placeholderize_empty_values(preview_context)

        email_template_variables = [(k, str(v)) for k, v in preview_context_for_examples.items()]
        email_template_variables.sort(key=lambda item: item[0])

        rendered_preview.update(
            render_templated_email_preview(
                subject=str(email_form.data.get("subject") or email_form.initial.get("subject") or ""),
                html_content=str(email_form.data.get("html_content") or email_form.initial.get("html_content") or ""),
                text_content=str(email_form.data.get("text_content") or email_form.initial.get("text_content") or ""),
                context=preview_context,
            )
        )
    except ValueError:
        rendered_preview = {"html": "", "text": "", "subject": ""}

    # The JS adds exclusion-group rows by cloning the rendered empty-form HTML.
    # Ensure that empty form has candidate options, even if the formset recreates
    # `empty_form` instances when accessed from the template.
    group_empty_form = group_formset.empty_form
    if election is not None:
        candidates_qs = Candidate.objects.filter(election=election).only("freeipa_username")
        candidate_choices = [
            user_choice_from_freeipa(c.freeipa_username)
            for c in candidates_qs
            if c.freeipa_username
        ]
        group_empty_form.fields["candidate_usernames"].choices = candidate_choices

    return render(
        request,
        "core/election_edit.html",
        {
            "is_create": is_create,
            "election": election,
            "details_form": details_form,
            "email_form": email_form,
            "candidate_formset": candidate_formset,
            "group_formset": group_formset,
            "group_empty_form": group_empty_form,
            "eligible_voters_count": len(eligible_voter_usernames),
            "nomination_eligible_voters_count": len(nomination_eligible_usernames),
            "templates": templates,
            "rendered_preview": rendered_preview,
            "email_template_variables": email_template_variables,
            "default_template_name": settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
        },
    )


@require_GET
def election_detail(request, election_id: int):
    election = Election.objects.exclude(status=Election.Status.deleted).filter(pk=election_id).first()
    if election is None:
        raise Http404

    can_manage_elections = request.user.has_perm(ASTRA_ADD_ELECTION)

    is_staff = bool(request.user.is_staff)
    if election.status == Election.Status.draft and not (is_staff or can_manage_elections):
        raise Http404

    candidates = list(Candidate.objects.filter(election=election).order_by("freeipa_username", "id"))

    usernames: set[str] = set()
    for c in candidates:
        if c.freeipa_username:
            usernames.add(c.freeipa_username)
        if c.nominated_by:
            usernames.add(c.nominated_by)

    users_by_username: dict[str, FreeIPAUser] = {}
    for username in sorted(usernames):
        user = FreeIPAUser.get(username)
        if user is None:
            # Keep rendering stable even if FreeIPA doesn't return the user.
            users_by_username[username] = FreeIPAUser(username, {"uid": [username], "memberof_group": []})
        else:
            users_by_username[username] = user

    candidate_cards: list[dict[str, object]] = []
    for c in candidates:
        candidate_user = users_by_username.get(c.freeipa_username)
        nominator_user = users_by_username.get(c.nominated_by) if c.nominated_by else None
        candidate_cards.append(
            {
                "candidate": c,
                "candidate_user": candidate_user,
                "nominator_user": nominator_user,
            }
        )

    def _natural_join(items: list[str]) -> str:
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]} and {items[1]}"
        return ", ".join(items[:-1]) + f", and {items[-1]}"

    def _candidate_display_name(username: str) -> str:
        user = users_by_username.get(username)
        full_name = user.full_name if user is not None else ""
        full_name = str(full_name or "").strip()
        if not full_name:
            full_name = username
        return f"{full_name} ({username})"

    exclusion_group_messages: list[str] = []
    exclusion_groups = list(
        ExclusionGroup.objects.filter(election=election)
        .prefetch_related(
            Prefetch(
                "candidates",
                queryset=Candidate.objects.only("id", "freeipa_username").order_by("freeipa_username", "id"),
            )
        )
        .order_by("name", "id")
    )
    for group in exclusion_groups:
        group_candidates = [c for c in group.candidates.all() if c.freeipa_username]
        names = [_candidate_display_name(c.freeipa_username) for c in group_candidates]
        if not names:
            continue

        who = _natural_join(names)
        candidate_word = "candidate" if group.max_elected == 1 else "candidates"
        exclusion_group_messages.append(
            f"{who} belong to the {group.name} exclusion group: only {group.max_elected} {candidate_word} of the group can be elected."
        )

    tally_result = election.tally_result or {}
    elected_ids = [int(x) for x in (tally_result.get("elected") or [])]
    tally_elected: list[Candidate] = []
    if elected_ids:
        candidates_by_id = {c.id: c for c in candidates}
        tally_elected = [candidates_by_id[cid] for cid in elected_ids if cid in candidates_by_id]

    tally_winners: list[dict[str, str]] = []
    for c in tally_elected:
        user = users_by_username.get(c.freeipa_username)
        full_name = user.full_name if user is not None else c.freeipa_username
        tally_winners.append({"username": c.freeipa_username, "full_name": full_name})

    empty_seats = election.number_of_seats - len(tally_elected)

    admin_context = _eligible_voters_context(request=request, election=election, enabled=can_manage_elections)

    username = str(request.session.get("_freeipa_username") or "").strip()
    if not username:
        username = str(request.user.get_username() or "").strip()

    voter_votes: int | None = None
    if election.status == Election.Status.open and username:
        credential = (
            VotingCredential.objects.filter(election=election, freeipa_username=username)
            .only("weight")
            .first()
        )
        voter_votes = int(credential.weight or 0) if credential is not None else 0

    can_vote = election.status == Election.Status.open and bool(voter_votes and voter_votes > 0)

    show_turnout_chart = bool(
        can_manage_elections
        and election.status in {Election.Status.open, Election.Status.closed, Election.Status.tallied}
    )

    turnout_stats: dict[str, object] = {}
    turnout_chart_data: dict[str, object] = {}
    if can_manage_elections or election.status == Election.Status.tallied:
        status = election_quorum_status(election=election)
        eligible_voter_count = int(status.get("eligible_voter_count") or 0)
        eligible_vote_weight_total = int(status.get("eligible_vote_weight_total") or 0)
        required_participating_voter_count = int(status.get("required_participating_voter_count") or 0)
        participating_voter_count = int(status.get("participating_voter_count") or 0)
        participating_vote_weight_total = int(status.get("participating_vote_weight_total") or 0)
        quorum_met = bool(status.get("quorum_met"))
        quorum_percent = int(status.get("quorum_percent") or 0)

        participating_voter_percent = 0
        if eligible_voter_count > 0:
            participating_voter_percent = min(
                100,
                int((participating_voter_count * 100) / eligible_voter_count),
            )

        participating_vote_weight_percent = 0
        if eligible_vote_weight_total > 0:
            participating_vote_weight_percent = min(
                100,
                int((participating_vote_weight_total * 100) / eligible_vote_weight_total),
            )

        turnout_stats = {
            "participating_voter_count": participating_voter_count,
            "participating_vote_weight_total": participating_vote_weight_total,
            "eligible_voter_count": eligible_voter_count,
            "eligible_vote_weight_total": eligible_vote_weight_total,
            "required_participating_voter_count": required_participating_voter_count,
            "quorum_met": quorum_met,
            "quorum_percent": quorum_percent,
            "participating_voter_percent": participating_voter_percent,
            "participating_vote_weight_percent": participating_vote_weight_percent,
        }

        if show_turnout_chart:
            rows = (
                AuditLogEntry.objects.filter(election=election, event_type="ballot_submitted")
                .annotate(day=TruncDate("timestamp"))
                .values("day")
                .annotate(count=Count("id"))
                .order_by("day")
            )

            counts_by_day: dict[datetime.date, int] = {}
            for row in rows:
                day = row.get("day")
                if not isinstance(day, datetime.date):
                    continue
                counts_by_day[day] = int(row.get("count") or 0)

            start_day = timezone.localdate(election.start_datetime)
            if election.status == Election.Status.open:
                end_day = timezone.localdate()
            else:
                end_day = timezone.localdate(election.end_datetime)
            if end_day < start_day:
                end_day = start_day

            labels: list[str] = []
            counts: list[int] = []
            cursor = start_day
            while cursor <= end_day:
                labels.append(cursor.isoformat())
                counts.append(counts_by_day.get(cursor, 0))
                cursor += datetime.timedelta(days=1)

            turnout_chart_data = {
                "labels": labels,
                "counts": counts,
            }

    return render(
        request,
        "core/election_detail.html",
        {
            "election": election,
            "candidates": candidates,
            "candidate_cards": candidate_cards,
            "can_manage_elections": can_manage_elections,
            "can_vote": can_vote,
            "show_turnout_chart": show_turnout_chart,
            "eligibility_min_membership_age_days": settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS,
            **admin_context,
            "turnout_stats": turnout_stats,
            "turnout_chart_data": turnout_chart_data,
            "exclusion_group_messages": exclusion_group_messages,
            "tally_elected": tally_elected,
            "tally_winners": tally_winners,
            "empty_seats": empty_seats if election.status == Election.Status.tallied else 0,
        },
    )


def _eligible_voters_context(*, request, election: Election, enabled: bool) -> dict[str, object]:
    if not enabled:
        return {}

    eligible = eligible_voters_from_memberships(election=election)

    eligible_q = str(request.GET.get("eligible_q") or "").strip()
    eligible_q_lower = eligible_q.lower()
    eligible_for_grid = (
        [v for v in eligible if eligible_q_lower in str(v.username or "").lower()]
        if eligible_q_lower
        else list(eligible)
    )

    reference_datetime = election.start_datetime
    if election.status == Election.Status.draft:
        reference_datetime = max(election.start_datetime, timezone.now())

    eligible_usernames_lower = {v.username.lower() for v in eligible if str(v.username).strip()}

    # Build an electorate set for ineligible reporting.
    #
    # - If election.eligible_group_cn is set, we use the FreeIPA group membership as the electorate,
    #   which includes users with no membership/sponsorship rows.
    # - Otherwise, we restrict the electorate to users who have any vote-bearing membership/sponsorship
    #   rows to avoid enumerating all FreeIPA users.
    term_start_by_username: dict[str, datetime.datetime] = {}
    has_any_vote_eligible: set[str] = set()
    has_active_vote_eligible_at_reference: set[str] = set()

    memberships = Membership.objects.filter(
        membership_type__isIndividual=True,
        membership_type__enabled=True,
        membership_type__votes__gt=0,
    ).values("target_username", "created_at", "expires_at")
    for row in memberships:
        username = str(row.get("target_username") or "").strip()
        if not username:
            continue
        username = username.lower()

        has_any_vote_eligible.add(username)

        start_at = row.get("created_at")
        if isinstance(start_at, datetime.datetime):
            if username not in term_start_by_username or start_at < term_start_by_username[username]:
                term_start_by_username[username] = start_at

        expires_at = row.get("expires_at")
        if expires_at is None or (
            isinstance(expires_at, datetime.datetime) and expires_at >= reference_datetime
        ):
            has_active_vote_eligible_at_reference.add(username)

    sponsorships = OrganizationSponsorship.objects.select_related("organization").filter(
        membership_type__enabled=True,
        membership_type__votes__gt=0,
    )
    for sponsorship in sponsorships.only("organization__representative", "created_at", "expires_at"):
        username = str(sponsorship.organization.representative or "").strip()
        if not username:
            continue
        username = username.lower()

        has_any_vote_eligible.add(username)

        start_at = sponsorship.created_at
        if isinstance(start_at, datetime.datetime):
            if username not in term_start_by_username or start_at < term_start_by_username[username]:
                term_start_by_username[username] = start_at

        expires_at = sponsorship.expires_at
        if expires_at is None or expires_at >= reference_datetime:
            has_active_vote_eligible_at_reference.add(username)

    group_cn = str(election.eligible_group_cn or "").strip()
    if group_cn:
        electorate = {u.lower() for u in elections_services._freeipa_group_recursive_member_usernames(group_cn=group_cn)}
    else:
        electorate = {
            str(u.username).strip().lower()
            for u in FreeIPAUser.all()
            if str(u.username).strip()
        }

    min_age_days = int(settings.ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS)
    election_start_day = timezone.localtime(election.start_datetime).date()
    reference_day = timezone.localtime(reference_datetime).date()

    ineligible_voters: list[dict[str, object]] = []
    for username in sorted(electorate):
        if username in eligible_usernames_lower:
            continue

        term_start_date: str = "Unknown"
        days_at_start: int | str = ""
        days_short: int | str = ""

        start_at = term_start_by_username.get(username)
        term_start_day: datetime.date | None = None
        if isinstance(start_at, datetime.datetime):
            term_start_day = timezone.localtime(start_at).date()
            term_start_date = term_start_day.isoformat()
            days_at_start = (election_start_day - term_start_day).days

        if username not in has_any_vote_eligible:
            reason = "no_membership"
        elif username not in has_active_vote_eligible_at_reference:
            reason = "expired"
        else:
            reason = "too_new"
            if term_start_day is not None:
                days_at_reference = (reference_day - term_start_day).days
                days_short = max(0, min_age_days - days_at_reference)

        ineligible_voters.append(
            {
                "username": username,
                "reason": reason,
                "term_start_date": term_start_date,
                "election_start_date": election_start_day.isoformat(),
                "days_at_start": days_at_start,
                "days_short": days_short,
            }
        )

    ineligible_q = str(request.GET.get("ineligible_q") or "").strip()
    if ineligible_q:
        ineligible_q_lower = ineligible_q.lower()
        ineligible_voters = [
            v
            for v in ineligible_voters
            if ineligible_q_lower in str(v.get("username") or "").lower()
        ]

    ineligible_voter_details_by_username = {
        str(v["username"]): {
            "reason": str(v["reason"]),
            "term_start_date": str(v["term_start_date"]),
            "election_start_date": str(v["election_start_date"]),
            "days_at_start": v["days_at_start"],
            "days_short": v["days_short"],
        }
        for v in ineligible_voters
        if str(v.get("username") or "").strip()
    }

    usernames = [v.username for v in eligible_for_grid]

    grid_usernames = [v.username for v in eligible_for_grid]
    grid_items = [{"kind": "user", "username": username} for username in grid_usernames]

    paginator = Paginator(grid_items, per_page=24)
    page_number = str(request.GET.get("eligible_page") or "1").strip()
    page_obj = paginator.get_page(page_number)

    total_pages = paginator.num_pages
    current_page = page_obj.number
    if total_pages <= 10:
        page_numbers = list(range(1, total_pages + 1))
        show_first = False
        show_last = False
    else:
        start = max(1, current_page - 2)
        end = min(total_pages, current_page + 2)
        page_numbers = list(range(start, end + 1))
        show_first = 1 not in page_numbers
        show_last = total_pages not in page_numbers

    qs = dict(request.GET.items())
    qs.pop("eligible_page", None)
    page_url_prefix = f"?{urlencode(qs)}&eligible_page=" if qs else "?eligible_page="

    ineligible_grid_items = [
        {"kind": "user", "username": str(v["username"])} for v in ineligible_voters if str(v.get("username") or "").strip()
    ]
    ineligible_paginator = Paginator(ineligible_grid_items, per_page=24)
    ineligible_page_number = str(request.GET.get("ineligible_page") or "1").strip()
    ineligible_page_obj = ineligible_paginator.get_page(ineligible_page_number)

    ineligible_total_pages = ineligible_paginator.num_pages
    ineligible_current_page = ineligible_page_obj.number
    if ineligible_total_pages <= 10:
        ineligible_page_numbers = list(range(1, ineligible_total_pages + 1))
        ineligible_show_first = False
        ineligible_show_last = False
    else:
        ineligible_start = max(1, ineligible_current_page - 2)
        ineligible_end = min(ineligible_total_pages, ineligible_current_page + 2)
        ineligible_page_numbers = list(range(ineligible_start, ineligible_end + 1))
        ineligible_show_first = 1 not in ineligible_page_numbers
        ineligible_show_last = ineligible_total_pages not in ineligible_page_numbers

    ineligible_qs = dict(request.GET.items())
    ineligible_qs.pop("ineligible_page", None)
    ineligible_page_url_prefix = (
        f"?{urlencode(ineligible_qs)}&ineligible_page=" if ineligible_qs else "?ineligible_page="
    )

    return {
        "eligible_voters": eligible,
        "eligible_voter_usernames": usernames,
        "eligible_q": eligible_q,
        "ineligible_voters": ineligible_voters,
        "ineligible_voter_details_by_username": ineligible_voter_details_by_username,
        "ineligible_q": ineligible_q,
        "ineligible_grid_items": list(ineligible_page_obj),
        "ineligible_paginator": ineligible_paginator,
        "ineligible_page_obj": ineligible_page_obj,
        "ineligible_is_paginated": ineligible_paginator.num_pages > 1,
        "ineligible_page_numbers": ineligible_page_numbers,
        "ineligible_show_first": ineligible_show_first,
        "ineligible_show_last": ineligible_show_last,
        "ineligible_page_url_prefix": ineligible_page_url_prefix,
        "ineligible_empty_label": "No ineligible voters found.",
        "grid_items": list(page_obj),
        "paginator": paginator,
        "page_obj": page_obj,
        "is_paginated": paginator.num_pages > 1,
        "page_numbers": page_numbers,
        "show_first": show_first,
        "show_last": show_last,
        "page_url_prefix": page_url_prefix,
        "empty_label": "No eligible voters.",
    }


@require_POST
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_send_mail_credentials(request: HttpRequest, election_id: int) -> HttpResponse:
    election = Election.objects.exclude(status=Election.Status.deleted).filter(pk=election_id).first()
    if election is None:
        raise Http404

    if election.status != Election.Status.open:
        messages.error(request, "Only open elections can send credential reminders.")
        return redirect("election-detail", election_id=election.id)

    admin_username = str(request.session.get("_freeipa_username") or "").strip()
    if not admin_username:
        admin_username = str(request.user.get_username() or "").strip()

    if not allow_request(
        scope="elections.credential_resend",
        key_parts=[str(election.id), admin_username],
        limit=settings.ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_LIMIT,
        window_seconds=settings.ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_WINDOW_SECONDS,
    ):
        return HttpResponse("Too many resend attempts. Please try again later.", status=429)

    target_username = str(request.POST.get("username") or "").strip()

    credentials_qs = VotingCredential.objects.filter(election=election).exclude(freeipa_username__isnull=True)
    if not credentials_qs.exists():
        issued = issue_voting_credentials_from_memberships_detailed(election=election)
        by_username = {c.freeipa_username: c for c in issued if c.freeipa_username}
        if target_username:
            credential_list = [by_username[target_username]] if target_username in by_username else []
        else:
            credential_list = list(by_username.values())
    else:
        if target_username:
            credential_list = list(
                credentials_qs.filter(freeipa_username=target_username).only("freeipa_username", "public_id")
            )
        else:
            credential_list = list(credentials_qs.only("freeipa_username", "public_id"))

    if target_username and not credential_list:
        messages.error(request, "That user does not have a voting credential for this election.")
        return redirect("election-detail", election_id=election.id)

    recipients: list[dict[str, str]] = []
    for credential in credential_list:
        username = str(credential.freeipa_username or "").strip()
        if not username:
            continue

        user = FreeIPAUser.get(username)
        if user is None or not user.email:
            continue

        tz_name = _get_freeipa_timezone_name(user)

        ctx = elections_services.build_voting_credential_email_context(
            request=request,
            election=election,
            username=username,
            credential_public_id=str(credential.public_id),
            tz_name=tz_name,
            user=user,
        )
        recipients.append({str(k): str(v) for k, v in ctx.items()})

    if not recipients:
        messages.error(request, "No credential recipients are available (missing email addresses?).")
        return redirect("election-detail", election_id=election.id)

    request.session[_CSV_SESSION_KEY] = json.dumps(
        {
            "header_to_var": {
                "Email": "email",
                "Username": "username",
                "First name": "first_name",
                "Last name": "last_name",
                "Full name": "full_name",
                "Election ID": "election_id",
                "Election name": "election_name",
                "Election description": "election_description",
                "Election URL": "election_url",
                "Election start": "election_start_datetime",
                "Election end": "election_end_datetime",
                "Number of seats": "election_number_of_seats",
                "Credential": "credential_public_id",
                "Vote URL": "vote_url",
                "Vote URL (with credential)": "vote_url_with_credential_fragment",
            },
            "recipients": recipients,
        }
    )

    send_mail_url = reverse("send-mail")
    template_name = settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME
    query = urlencode({"type": "csv", "template": template_name})
    return redirect(f"{send_mail_url}?{query}")


def _confirm_election_action(*, request: HttpRequest, election: Election) -> bool:
    raw = str(request.POST.get("confirm") or "").strip()
    if not raw:
        return False
    if raw == str(election.id):
        return True
    return raw.casefold() == str(election.name or "").strip().casefold()


@require_POST
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_conclude(request, election_id: int):
    election = Election.objects.exclude(status=Election.Status.deleted).filter(pk=election_id).first()
    if election is None:
        raise Http404

    if not _confirm_election_action(request=request, election=election):
        return HttpResponseBadRequest("Confirmation required.")

    skip_tally = bool(request.POST.get("skip_tally"))

    actor = str(request.user.username or "").strip() or None

    try:
        elections_services.close_election(election=election, actor=actor)
    except ElectionError as exc:
        messages.error(request, str(exc))
        return redirect("election-detail", election_id=election.id)

    if skip_tally:
        messages.success(request, "Election closed.")
        return redirect("election-detail", election_id=election.id)

    try:
        elections_services.tally_election(election=election, actor=actor)
    except ElectionError as exc:
        messages.error(request, f"Election closed, but tally failed: {exc}")
        return redirect("election-detail", election_id=election.id)

    messages.success(request, "Election closed and tallied.")
    return redirect("election-detail", election_id=election.id)


@require_POST
@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_extend_end(request, election_id: int):
    election = Election.objects.exclude(status=Election.Status.deleted).filter(pk=election_id).first()
    if election is None:
        raise Http404

    if not _confirm_election_action(request=request, election=election):
        return HttpResponseBadRequest("Confirmation required.")

    if election.status != Election.Status.open:
        messages.error(request, "Only open elections can be extended.")
        return redirect("election-detail", election_id=election.id)

    end_form = ElectionEndDateForm(request.POST, instance=election)
    if not end_form.is_valid():
        for msg in end_form.errors.get("end_datetime", []):
            messages.error(request, str(msg))
        return redirect("election-detail", election_id=election.id)

    new_end = end_form.cleaned_data.get("end_datetime")
    if not isinstance(new_end, datetime.datetime):
        messages.error(request, "Invalid end datetime.")
        return redirect("election-detail", election_id=election.id)

    actor = str(request.user.username or "").strip() or None

    try:
        elections_services.extend_election_end_datetime(
            election=election,
            new_end_datetime=new_end,
            actor=actor,
        )
    except ElectionError as exc:
        messages.error(request, str(exc))
        return redirect("election-detail", election_id=election.id)

    messages.success(request, "Election end date extended.")
    return redirect("election-detail", election_id=election.id)


def _get_exportable_election(*, election_id: int) -> Election:
    election = (
        Election.objects.exclude(status=Election.Status.deleted)
        .filter(pk=election_id)
        .only("id", "status", "public_ballots_file", "public_audit_file")
        .first()
    )
    if election is None:
        raise Http404
    if election.status not in {Election.Status.closed, Election.Status.tallied}:
        raise Http404
    return election


@require_GET
def election_public_ballots(request, election_id: int):
    election = _get_exportable_election(election_id=election_id)

    if election.status == Election.Status.tallied and election.public_ballots_file:
        return redirect(election.public_ballots_file.url)

    return JsonResponse(elections_services.build_public_ballots_export(election=election))


@require_GET
def election_public_audit(request, election_id: int):
    election = _get_exportable_election(election_id=election_id)

    if election.status == Election.Status.tallied and election.public_audit_file:
        return redirect(election.public_audit_file.url)

    return JsonResponse(elections_services.build_public_audit_export(election=election))


@require_GET
def election_audit_log(request, election_id: int):
    """Render a human-readable election audit log.

    This page is meant to improve transparency and auditability by presenting
    the election's public audit events (and, for election managers, private
    events as well) in a chronological timeline.
    """

    election = Election.objects.exclude(status=Election.Status.deleted).filter(pk=election_id).first()
    if election is None:
        raise Http404

    if election.status not in {Election.Status.closed, Election.Status.tallied}:
        raise Http404

    candidates = list(
        Candidate.objects.filter(election=election).only("id", "freeipa_username").order_by("freeipa_username", "id")
    )
    candidate_username_by_id: dict[int, str] = {
        int(c.id): str(c.freeipa_username or "").strip()
        for c in candidates
        if str(c.freeipa_username or "").strip()
    }

    audit_qs = AuditLogEntry.objects.filter(election=election)
    can_manage_elections = request.user.has_perm(ASTRA_ADD_ELECTION)
    if not can_manage_elections:
        audit_qs = audit_qs.filter(is_public=True)

    timeline_items: list[AuditLogEntry | dict[str, object]] = []
    non_ballot_entries = list(
        audit_qs.exclude(event_type="ballot_submitted")
        .only("id", "timestamp", "event_type", "payload", "is_public")
        .order_by("timestamp", "id")
    )
    timeline_items.extend(non_ballot_entries)

    if can_manage_elections:
        # Group ballot submissions by day for managers to keep the timeline readable.
        ballot_qs = audit_qs.filter(event_type="ballot_submitted")
        for row in (
            ballot_qs.annotate(day=TruncDate("timestamp"))
            .values("day")
            .annotate(
                ballots_count=Count("id"),
                first_timestamp=Min("timestamp"),
                last_timestamp=Max("timestamp"),
            )
            .order_by("day")
        ):
            day = row.get("day")
            first_ts = row.get("first_timestamp")
            last_ts = row.get("last_timestamp")
            if not isinstance(day, datetime.date) or not isinstance(first_ts, datetime.datetime) or not isinstance(
                last_ts, datetime.datetime
            ):
                continue
            timeline_items.append(
                {
                    "timestamp": last_ts,
                    "event_type": "ballots_submitted_summary",
                    "payload": {},
                    "ballot_date": day.isoformat(),
                    "ballots_count": int(row.get("ballots_count") or 0),
                    "first_timestamp": first_ts,
                    "last_timestamp": last_ts,
                }
            )

    def _timeline_sort_key(item: AuditLogEntry | dict[str, object]) -> tuple[datetime.datetime, int]:
        if isinstance(item, dict):
            ts = item.get("timestamp")
            if isinstance(ts, datetime.datetime):
                return (ts, 0)
            return (datetime.datetime.min.replace(tzinfo=timezone.get_current_timezone()), 0)
        return (item.timestamp, item.id)

    # Sort newest-first to support "load older" navigation.
    timeline_items.sort(key=_timeline_sort_key, reverse=True)

    page_raw = str(request.GET.get("page") or "1").strip()
    try:
        page_number = int(page_raw)
    except ValueError:
        page_number = 1

    paginator = Paginator(timeline_items, 60)
    page_obj = paginator.get_page(page_number)

    base_url = reverse("election-audit-log", args=[election.id])

    def _url_for_page(page: int) -> str:
        q = request.GET.copy()
        q["page"] = str(page)
        return f"{base_url}?{q.urlencode()}"

    newer_url = _url_for_page(page_obj.previous_page_number()) if page_obj.has_previous() else ""
    older_url = _url_for_page(page_obj.next_page_number()) if page_obj.has_next() else ""

    ballot_preview_by_date: dict[str, list[dict[str, object]]] = {}
    ballot_preview_limit = 50
    if can_manage_elections:
        preview_dates: list[datetime.date] = []
        for it in page_obj.object_list:
            if not isinstance(it, dict):
                continue
            if str(it.get("event_type") or "") != "ballots_submitted_summary":
                continue
            day_raw = str(it.get("ballot_date") or "").strip()
            if not day_raw:
                continue
            try:
                preview_dates.append(datetime.date.fromisoformat(day_raw))
            except ValueError:
                continue

        if preview_dates:
            ballot_qs = audit_qs.filter(event_type="ballot_submitted")
            for day in sorted(set(preview_dates)):
                rows = list(
                    ballot_qs.filter(timestamp__date=day)
                    .only("timestamp", "payload")
                    .order_by("timestamp", "id")[:ballot_preview_limit]
                )
                preview: list[dict[str, object]] = []
                for row in rows:
                    payload = row.payload if isinstance(row.payload, dict) else {}
                    ballot_hash = str(payload.get("ballot_hash") or "").strip()
                    supersedes_hash = str(payload.get("supersedes_ballot_hash") or "").strip()
                    preview.append(
                        {
                            "timestamp": row.timestamp,
                            "ballot_hash": ballot_hash,
                            "supersedes_ballot_hash": supersedes_hash,
                        }
                    )
                ballot_preview_by_date[day.isoformat()] = preview

    ballot_agg = Ballot.objects.filter(election=election, superseded_by__isnull=True).aggregate(
        ballots=Count("id"),
        weight_total=Sum("weight"),
    )
    ballots_cast = int(ballot_agg.get("ballots") or 0)
    votes_cast = int(ballot_agg.get("weight_total") or 0)

    tally_result = election.tally_result or {}

    def _icon_for_event(event_type: str) -> tuple[str, str]:
        match event_type:
            case "election_started":
                return ("fas fa-play", "bg-green")
            case "ballot_submitted":
                return ("fas fa-vote-yea", "bg-blue")
            case "ballots_submitted_summary":
                return ("fas fa-layer-group", "bg-blue")
            case "quorum_reached":
                return ("fas fa-check-circle", "bg-success")
            case "election_end_extended":
                return ("fas fa-calendar-plus", "bg-orange")
            case "election_closed":
                return ("fas fa-lock", "bg-orange")
            case "election_anonymized":
                return ("fas fa-user-secret", "bg-purple")
            case "tally_round":
                return ("fas fa-calculator", "bg-info")
            case "tally_completed":
                return ("fas fa-flag-checkered", "bg-success")
            case _:
                return ("fas fa-info-circle", "bg-secondary")

    def _title_for_event(event_type: str, payload: dict[str, object]) -> str:
        match event_type:
            case "election_started":
                return "Election started"
            case "ballot_submitted":
                return "Ballot submitted"
            case "ballots_submitted_summary":
                return "Ballots submitted"
            case "quorum_reached":
                return "Quorum reached"
            case "election_end_extended":
                return "Election end extended"
            case "election_closed":
                return "Election closed"
            case "election_anonymized":
                return "Election anonymized"
            case "tally_round":
                round_number = payload.get("round")
                iteration = payload.get("iteration")
                if isinstance(round_number, int) and isinstance(iteration, int):
                    return f"Tally round {round_number} (iteration {iteration})"
                if isinstance(round_number, int):
                    return f"Tally round {round_number}"
                if isinstance(iteration, int):
                    return f"Tally iteration {iteration}"
                return "Tally round"
            case "tally_completed":
                return "Tally completed"
            case _:
                return event_type.replace("_", " ")

    def _candidate_username(cid: int) -> str:
        username = candidate_username_by_id.get(cid)
        if username:
            return username
        return str(cid)

    events: list[dict[str, object]] = []
    jump_links: list[dict[str, str]] = []
    anchor_for_event_type = {
        "election_closed": "jump-election-closed",
        "tally_round": "jump-tally-rounds",
        "tally_completed": "jump-tally-completed",
    }
    anchor_labels = {
        "election_closed": "Election closed",
        "tally_round": "Tally rounds",
        "tally_completed": "Results",
    }
    anchors_added: set[str] = set()

    for item in page_obj.object_list:
        if isinstance(item, dict):
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            event_type = str(item.get("event_type") or "").strip() or "unknown"
            timestamp = item.get("timestamp")
            if not isinstance(timestamp, datetime.datetime):
                continue
            icon, icon_bg = _icon_for_event(event_type)
            event: dict[str, object] = {
                "timestamp": timestamp,
                "event_type": event_type,
                "title": _title_for_event(event_type, payload),
                "icon": icon,
                "icon_bg": icon_bg,
                "payload": payload,
            }
            event.update(item)

            if event_type == "ballots_submitted_summary":
                day = str(event.get("ballot_date") or "").strip()
                entries = ballot_preview_by_date.get(day, [])
                event["ballot_entries"] = entries
                try:
                    count = int(event.get("ballots_count") or 0)
                except (TypeError, ValueError):
                    count = 0
                event["ballots_preview_truncated"] = count > len(entries)
                event["ballots_preview_limit"] = ballot_preview_limit

            anchor = anchor_for_event_type.get(event_type)
            if anchor and anchor not in anchors_added:
                anchors_added.add(anchor)
                event["anchor"] = anchor
                jump_links.append({"anchor": anchor, "label": anchor_labels.get(event_type, event_type)})
            events.append(event)
            continue

        entry = item
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        event_type = str(entry.event_type or "").strip() or "unknown"
        icon, icon_bg = _icon_for_event(event_type)

        event: dict[str, object] = {
            "timestamp": entry.timestamp,
            "event_type": event_type,
            "title": _title_for_event(event_type, payload),
            "icon": icon,
            "icon_bg": icon_bg,
            "payload": payload,
        }

        anchor = anchor_for_event_type.get(event_type)
        if anchor and anchor not in anchors_added:
            anchors_added.add(anchor)
            event["anchor"] = anchor
            jump_links.append({"anchor": anchor, "label": anchor_labels.get(event_type, event_type)})

        if event_type == "tally_round":
            retained_totals_obj = payload.get("retained_totals")
            retention_factors_obj = payload.get("retention_factors")
            retained_totals: dict[int, str] = {}
            retention_factors: dict[int, str] = {}

            if isinstance(retained_totals_obj, dict):
                for k, v in retained_totals_obj.items():
                    try:
                        cid = int(k)
                    except (TypeError, ValueError):
                        continue
                    retained_totals[cid] = str(v)

            if isinstance(retention_factors_obj, dict):
                for k, v in retention_factors_obj.items():
                    try:
                        cid = int(k)
                    except (TypeError, ValueError):
                        continue
                    retention_factors[cid] = str(v)

            elected_ids_obj = payload.get("elected")
            elected_ids = {int(x) for x in elected_ids_obj} if isinstance(elected_ids_obj, list) else set()
            eliminated_obj = payload.get("eliminated")
            eliminated_id = int(eliminated_obj) if isinstance(eliminated_obj, int) else None

            def _sort_key(item: tuple[int, str]) -> tuple[Decimal, str]:
                cid, retained_str = item
                try:
                    retained_val = Decimal(str(retained_str))
                except (InvalidOperation, ValueError):
                    retained_val = Decimal(0)
                return (retained_val, _candidate_username(cid).lower())

            round_rows: list[dict[str, object]] = []
            for cid, retained_str in sorted(retained_totals.items(), key=_sort_key, reverse=True):
                username = candidate_username_by_id.get(cid, "")
                profile_url = reverse("user-profile", args=[username]) if username else ""
                round_rows.append(
                    {
                        "candidate_id": cid,
                        "candidate_username": username,
                        "candidate_profile_url": profile_url,
                        "candidate_label": username or str(cid),
                        "retained_total": retained_str,
                        "retention_factor": retention_factors.get(cid, ""),
                        "is_elected": cid in elected_ids,
                        "is_eliminated": eliminated_id is not None and cid == eliminated_id,
                    }
                )

            event["round_rows"] = round_rows
            event["summary_text"] = str(payload.get("summary_text") or "").strip()
            event["audit_text"] = str(payload.get("audit_text") or "").strip()

        if event_type == "tally_completed":
            elected_obj = payload.get("elected")
            elected_ids = [int(x) for x in elected_obj] if isinstance(elected_obj, list) else []
            elected_users: list[dict[str, str]] = []
            for cid in elected_ids:
                username = candidate_username_by_id.get(cid, "")
                if not username:
                    continue
                elected_users.append(
                    {
                        "username": username,
                        "profile_url": reverse("user-profile", args=[username]),
                    }
                )
            event["elected_users"] = elected_users

        events.append(event)

    tally_elected_users: list[dict[str, str]] = []
    elected_from_result = tally_result.get("elected")
    if isinstance(elected_from_result, list):
        for cid_obj in elected_from_result:
            try:
                cid = int(cid_obj)
            except (TypeError, ValueError):
                continue
            username = candidate_username_by_id.get(cid, "")
            if not username:
                continue
            tally_elected_users.append(
                {
                    "username": username,
                    "profile_url": reverse("user-profile", args=[username]),
                }
            )

    empty_seats = 0
    if election.status == Election.Status.tallied:
        elected_count = len(tally_elected_users)
        empty_seats = election.number_of_seats - elected_count

    return render(
        request,
        "core/election_audit_log.html",
        {
            "election": election,
            "can_manage_elections": can_manage_elections,
            "events": events,
            "jump_links": jump_links,
            "newer_url": newer_url,
            "older_url": older_url,
            "page_obj": page_obj,
            "candidates": candidates,
            "ballots_cast": ballots_cast,
            "votes_cast": votes_cast,
            "tally_result": tally_result,
            "quota": tally_result.get("quota"),
            "tally_elected_users": tally_elected_users,
            "empty_seats": empty_seats,
        },
    )


def _parse_vote_payload(request, *, election: Election) -> tuple[str, list[int]]:
    if request.content_type and request.content_type.startswith("application/json"):
        raw = request.body.decode("utf-8") if request.body else "{}"
        data = json.loads(raw)
        credential_public_id = str(data.get("credential_public_id") or "").strip()
        ranking_raw = data.get("ranking")
    else:
        credential_public_id = str(request.POST.get("credential_public_id") or "").strip()
        ranking_raw = str(request.POST.get("ranking") or "").strip()
        ranking_usernames_raw = str(request.POST.get("ranking_usernames") or "").strip()
        if not ranking_raw and ranking_usernames_raw:
            ranking_raw = ranking_usernames_raw

    if not credential_public_id:
        raise ValueError("credential_public_id is required")

    if isinstance(ranking_raw, list):
        ranking = [int(x) for x in ranking_raw]
    elif isinstance(ranking_raw, str):
        # Allow comma-separated input.
        parts = [p.strip() for p in ranking_raw.split(",") if p.strip()]
        if not parts:
            ranking = []
        else:
            # First try numeric IDs (JS path).
            try:
                ranking = [int(p) for p in parts]
            except ValueError:
                # No-JS fallback: accept comma-separated FreeIPA usernames.
                # Resolve them to candidate IDs at submit-time.
                ranking = []
                usernames = [p.lower() for p in parts]
                candidates = list(
                    Candidate.objects.filter(election=election, freeipa_username__in=usernames).values_list(
                        "freeipa_username",
                        "id",
                    )
                )
                by_username = {u.lower(): int(cid) for u, cid in candidates}
                unknown = sorted({u for u in usernames if u not in by_username})
                if unknown:
                    raise ValueError("Invalid ranking: contains usernames that are not candidates")

                for u in usernames:
                    ranking.append(by_username[u])
    else:
        raise ValueError("ranking must be a list")

    if not ranking:
        raise ValueError("ranking is required")

    return credential_public_id, ranking


@require_POST
def election_vote_submit(request, election_id: int):
    election = (
        Election.objects.exclude(status=Election.Status.deleted)
        .filter(pk=election_id)
        .only("id", "status")
        .first()
    )
    if election is None:
        raise Http404

    try:
        credential_public_id, ranking = _parse_vote_payload(request, election=election)
    except (ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    username = str(request.session.get("_freeipa_username") or "").strip()
    if not username:
        username = str(request.user.get_username() or "").strip()
    if not username:
        return JsonResponse({"ok": False, "error": "Authentication required."}, status=403)

    if not allow_request(
        scope="elections.vote_submit",
        key_parts=[str(election.id), username],
        limit=settings.ELECTION_RATE_LIMIT_VOTE_SUBMIT_LIMIT,
        window_seconds=settings.ELECTION_RATE_LIMIT_VOTE_SUBMIT_WINDOW_SECONDS,
    ):
        return JsonResponse(
            {"ok": False, "error": "Too many vote submissions. Please try again later."},
            status=429,
        )

    # Voting eligibility and weight are determined when credentials are issued.
    # Do not re-check current memberships here; they can change while the election is open.
    try:
        user_credential = VotingCredential.objects.only("public_id", "weight").get(
            election_id=election.id,
            freeipa_username=username,
        )
    except VotingCredential.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Not eligible to vote in this election."}, status=403)

    if int(user_credential.weight or 0) <= 0:
        return JsonResponse({"ok": False, "error": "Not eligible to vote in this election."}, status=403)

    if str(user_credential.public_id) != credential_public_id:
        other_credential = (
            VotingCredential.objects.filter(election_id=election.id, public_id=credential_public_id)
            .only("freeipa_username")
            .first()
        )
        if other_credential is None:
            return JsonResponse({"ok": False, "error": "Invalid credential."}, status=400)
        return JsonResponse({"ok": False, "error": "Credential does not belong to the current user."}, status=403)

    try:
        receipt = submit_ballot(
            election=election,
            credential_public_id=credential_public_id,
            ranking=ranking,
        )
    except (InvalidBallotError, InvalidCredentialError, ElectionNotOpenError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    freeipa_user = FreeIPAUser.get(username)
    voter_email = str(freeipa_user.email or "").strip() if freeipa_user is not None else ""
    if voter_email:
        tz_name = _get_freeipa_timezone_name(freeipa_user) if freeipa_user is not None else None
        elections_services.send_vote_receipt_email(
            request=request,
            election=election,
            username=username,
            email=voter_email,
            receipt=receipt,
            tz_name=tz_name,
        )

    return JsonResponse(
        {
            "ok": True,
            "election_id": election.id,
            "ballot_hash": receipt.ballot.ballot_hash,
            "nonce": receipt.nonce,
            "previous_chain_hash": receipt.ballot.previous_chain_hash,
            "chain_hash": receipt.ballot.chain_hash,
        }
    )


@require_GET
def election_vote(request, election_id: int):
    election = Election.objects.exclude(status=Election.Status.deleted).filter(pk=election_id).first()
    if election is None:
        raise Http404
    if election.status in {Election.Status.closed, Election.Status.tallied}:
        return render(
            request,
            "core/election_vote_closed.html",
            {
                "election": election,
                "ballot_verify_url": reverse("ballot-verify"),
            },
            status=410,
        )
    if election.status != Election.Status.open:
        raise Http404

    username = str(request.session.get("_freeipa_username") or "").strip()
    if not username:
        username = str(request.user.get_username() or "").strip()

    voter_votes: int | None = None
    if username:
        credential = (
            VotingCredential.objects.filter(election=election, freeipa_username=username)
            .only("weight")
            .first()
        )
        voter_votes = int(credential.weight or 0) if credential is not None else 0

    can_submit_vote = voter_votes is not None and voter_votes > 0

    candidates = list(Candidate.objects.filter(election=election))
    random.shuffle(candidates)

    users_by_username: dict[str, FreeIPAUser] = {}
    for c in candidates:
        if c.freeipa_username and c.freeipa_username not in users_by_username:
            user = FreeIPAUser.get(c.freeipa_username)
            if user is None:
                user = FreeIPAUser(c.freeipa_username, {"uid": [c.freeipa_username], "memberof_group": []})
            users_by_username[c.freeipa_username] = user

    candidate_display: list[dict[str, object]] = []
    for c in candidates:
        user = users_by_username.get(c.freeipa_username)
        full_name = user.full_name if user is not None else c.freeipa_username
        label = f"{full_name} ({c.freeipa_username})"
        candidate_display.append({"candidate": c, "label": label})

    return render(
        request,
        "core/election_vote.html",
        {
            "election": election,
            "candidates": candidate_display,
            "voter_votes": voter_votes,
            "can_submit_vote": can_submit_vote,
        },
    )
