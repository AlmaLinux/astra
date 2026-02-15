"""Election creation and editing views."""


from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from post_office.models import EmailTemplate

from core import elections_eligibility, elections_services
from core.backends import FreeIPAUser
from core.elections_eligibility import ElectionEligibilityError
from core.elections_services import (
    election_genesis_chain_hash,
    issue_voting_credentials_from_memberships,
)
from core.forms_elections import (
    CandidateWizardFormSet,
    ElectionDetailsForm,
    ElectionVotingEmailForm,
    ExclusionGroupWizardFormSet,
    is_self_nomination,
)
from core.ipa_user_attrs import _get_freeipa_timezone_name
from core.models import AuditLogEntry, Candidate, Election, ExclusionGroup, ExclusionGroupCandidate
from core.permissions import ASTRA_ADD_ELECTION
from core.templated_email import placeholderize_empty_values, render_templated_email_preview
from core.user_labels import user_choice_from_freeipa
from core.views_elections._helpers import (
    _election_email_preview_context,
    _extend_election_end_from_post,
    _get_active_election,
)

# ---------------------------------------------------------------------------
# election_edit helpers
# ---------------------------------------------------------------------------


def _candidate_queryset(election: Election | None):
    """Candidate queryset for the given election, or empty if unsaved."""
    if election is not None and election.pk is not None:
        return Candidate.objects.filter(election=election).order_by("id")
    return Candidate.objects.none()


def _group_queryset(election: Election | None):
    """ExclusionGroup queryset for the given election, or empty if unsaved."""
    if election is not None and election.pk is not None:
        return ExclusionGroup.objects.filter(election=election).order_by("name", "id")
    return ExclusionGroup.objects.none()


def _disable_details_form_for_started(
    election: Election | None, details_form: ElectionDetailsForm,
) -> None:
    """Disable configuration fields once an election has started."""
    if election is None or election.status == Election.Status.draft:
        return
    for field_name in (
        "name", "description", "url", "start_datetime",
        "number_of_seats", "quorum",
    ):
        details_form.fields[field_name].disabled = True
    if election.status != Election.Status.open:
        details_form.fields["end_datetime"].disabled = True


def _disable_formset_fields(
    candidate_formset: CandidateWizardFormSet,
    group_formset: ExclusionGroupWizardFormSet,
) -> None:
    """Disable all formset fields for non-draft elections."""
    for form in candidate_formset.forms:
        for field in form.fields.values():
            field.disabled = True
    for form in group_formset.forms:
        for field in form.fields.values():
            field.disabled = True


def _configure_candidate_choices(
    request: HttpRequest,
    election: Election | None,
    candidate_formset: CandidateWizardFormSet,
) -> None:
    """Set AJAX URLs and pre-selected choices on candidate formset forms."""
    ajax_election_id = election.id if election is not None and election.pk is not None else 0
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
            form.fields["freeipa_username"].choices = [user_choice_from_freeipa(freeipa_value)]
        form.fields["freeipa_username"].widget.attrs["data-ajax-url"] = ajax_url_candidate
        form.fields["freeipa_username"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"

        nominated_value = str(
            form.data.get(form.add_prefix("nominated_by"))
            or form.initial.get("nominated_by")
            or form.instance.nominated_by
            or ""
        ).strip()
        if nominated_value:
            form.fields["nominated_by"].choices = [user_choice_from_freeipa(nominated_value)]
        form.fields["nominated_by"].widget.attrs["data-ajax-url"] = ajax_url_nominator
        form.fields["nominated_by"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"

    candidate_formset.empty_form.fields["freeipa_username"].widget.attrs["data-ajax-url"] = ajax_url_candidate
    candidate_formset.empty_form.fields["nominated_by"].widget.attrs["data-ajax-url"] = ajax_url_nominator
    candidate_formset.empty_form.fields["freeipa_username"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"
    candidate_formset.empty_form.fields["nominated_by"].widget.attrs["data-start-datetime-source"] = "id_start_datetime"


def _configure_group_choices_for_formset(
    request: HttpRequest,
    election: Election | None,
    candidate_formset: CandidateWizardFormSet,
    group_formset: ExclusionGroupWizardFormSet,
) -> None:
    """Populate exclusion-group candidate-username choices on all group forms."""
    if election is None or election.pk is None:
        # Create-mode: derive choices from submitted POST data.
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

    group_formset.empty_form.fields["candidate_usernames"].choices = choices

    for form in group_formset.forms:
        selected = form.data.getlist(form.add_prefix("candidate_usernames")) if hasattr(form.data, "getlist") else []
        existing = {u for u, _label in choices}
        extra = [user_choice_from_freeipa(u) for u in selected if u and u not in existing]
        form.fields["candidate_usernames"].choices = [*choices, *extra]

        # Pre-select saved candidates when rendering an existing group.
        if form.instance.pk and not selected:
            form.initial["candidate_usernames"] = list(
                form.instance.candidates.order_by("freeipa_username", "id").values_list("freeipa_username", flat=True)
            )


def _build_email_preview(
    request: HttpRequest,
    election: Election | None,
    details_form: ElectionDetailsForm,
    email_form: ElectionVotingEmailForm,
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Build the email preview context and template-variable list."""
    rendered_preview: dict[str, str] = {"html": "", "text": "", "subject": ""}
    email_template_variables: list[tuple[str, str]] = []

    # Use the form instance so the preview reflects unsaved edits.
    preview_election = details_form.instance if details_form.instance.pk is not None else (election or Election(id=0))

    try:
        preview_context = _election_email_preview_context(
            request=request, election=preview_election,
        )

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

    return rendered_preview, email_template_variables


def _apply_email_template_from_form(
    election: Election, email_form: ElectionVotingEmailForm,
) -> None:
    """Copy email template settings from the form to the election model."""
    template_id = email_form.cleaned_data.get("email_template_id")
    template = EmailTemplate.objects.filter(pk=int(template_id)).first() if template_id else None
    election.voting_email_template = template
    election.voting_email_subject = str(email_form.cleaned_data.get("subject") or "")
    election.voting_email_html = str(email_form.cleaned_data.get("html_content") or "")
    election.voting_email_text = str(email_form.cleaned_data.get("text_content") or "")


def _save_candidates_and_groups(
    election: Election,
    candidate_formset: CandidateWizardFormSet,
    group_formset: ExclusionGroupWizardFormSet,
) -> None:
    """Persist candidate and exclusion-group formsets to the database."""
    for form in candidate_formset.forms:
        if not hasattr(form, "cleaned_data"):
            continue
        if form.cleaned_data.get("DELETE"):
            if form.instance.pk:
                form.instance.delete()
            continue

        username = str(form.cleaned_data.get("freeipa_username") or "").strip()
        if not username:
            continue

        candidate = form.save(commit=False)
        candidate.election = election
        candidate.save()

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

        selected_usernames = [
            str(u).strip() for u in (form.cleaned_data.get("candidate_usernames") or [])
        ]
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


def _issue_and_email_credentials(
    request: HttpRequest, election: Election,
) -> tuple[int, int, int, int]:
    """Issue voting credentials and email voters.

    Returns (total_credentials, emailed, skipped, failures).
    """
    credentials = issue_voting_credentials_from_memberships(election=election)
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

    return len(credentials), emailed, skipped, failures


def _handle_start_election(
    request: HttpRequest,
    election: Election | None,
    details_form: ElectionDetailsForm,
    email_form: ElectionVotingEmailForm,
) -> HttpResponse | None:
    """Validate and start an election, issuing credentials and emailing voters.

    Returns a redirect on success, or None if validation fails (caller should
    fall through to re-render the form with error messages).
    """
    if election is None:
        messages.error(request, "Save the draft first.")
        return None
    if election.status != Election.Status.draft:
        messages.error(request, "Only draft elections can be started.")
        return None
    if not details_form.is_valid() or not email_form.is_valid():
        messages.error(request, "Please correct the errors below.")
        return None
    if not Candidate.objects.filter(election=election).exists():
        messages.error(request, "Add at least one candidate before starting the election.")
        return None

    candidates = list(
        Candidate.objects.filter(election=election).only("freeipa_username", "nominated_by")
    )
    self_nominations = sorted(
        {
            str(c.freeipa_username or "").strip()
            for c in candidates
            if is_self_nomination(
                candidate_username=c.freeipa_username,
                nominator_username=c.nominated_by,
            )
        },
        key=str.lower,
    )
    candidate_usernames = [
        str(c.freeipa_username or "").strip()
        for c in candidates
        if str(c.freeipa_username or "").strip()
    ]
    nominator_usernames = [
        str(c.nominated_by or "").strip()
        for c in candidates
        if str(c.nominated_by or "").strip()
    ]

    if self_nominations:
        messages.error(request, "Candidates cannot nominate themselves.")
        return None

    try:
        eligible_voter_usernames = {
            v.username
            for v in elections_eligibility.eligible_voters_from_memberships(
                election=election,
                require_fresh=True,
            )
        }
        no_eligible_voters = not eligible_voter_usernames
        validation = elections_eligibility.validate_candidates_for_election(
            election=election,
            candidate_usernames=candidate_usernames,
            nominator_usernames=nominator_usernames,
            eligible_group_cn=str(election.eligible_group_cn or "").strip(),
            require_fresh=True,
        )
    except ElectionEligibilityError as exc:
        messages.error(request, str(exc))
        return None

    has_issues = (
        validation.disqualified_candidates
        or validation.disqualified_nominators
        or validation.ineligible_candidates
        or validation.ineligible_nominators
    )
    if has_issues or no_eligible_voters:
        if validation.disqualified_candidates:
            names = ", ".join(sorted(validation.disqualified_candidates, key=str.lower))
            messages.error(
                request,
                "Election committee members cannot be candidates for this election: " + names,
            )
        if validation.disqualified_nominators:
            names = ", ".join(sorted(validation.disqualified_nominators, key=str.lower))
            messages.error(
                request,
                "Election committee members cannot nominate candidates for this election: " + names,
            )
        if validation.ineligible_candidates:
            names = ", ".join(sorted(validation.ineligible_candidates, key=str.lower))
            messages.error(request, "Candidate is not eligible: " + names)
        if validation.ineligible_nominators:
            names = ", ".join(sorted(validation.ineligible_nominators, key=str.lower))
            messages.error(request, "Nominator is not eligible: " + names)
        if no_eligible_voters:
            messages.error(request, "No eligible voters were found for this election.")
        return None

    # All validations passed — commit the election start.
    election = details_form.save(commit=False)

    # Align the published start timestamp with when the election actually opens.
    election.start_datetime = timezone.now()

    _apply_email_template_from_form(election, email_form)

    election.status = Election.Status.open
    election.save()

    total_credentials, emailed, skipped, failures = _issue_and_email_credentials(request, election)

    AuditLogEntry.objects.create(
        election=election,
        event_type="election_started",
        payload={
            "eligible_voters": total_credentials,
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


@permission_required(ASTRA_ADD_ELECTION, raise_exception=True, login_url=reverse_lazy("users"))
def election_edit(request, election_id: int):
    is_create = election_id == 0
    election = None if is_create else _get_active_election(election_id)

    templates = list(EmailTemplate.objects.all().order_by("name"))
    default_template = EmailTemplate.objects.filter(name=settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME).first()

    def _membership_eligibility_sets(for_election: Election) -> tuple[set[str], set[str]]:
        try:
            eligible_usernames = {
                v.username for v in elections_eligibility.eligible_voters_from_memberships(election=for_election)
            }
            nomination_usernames = {
                v.username
                for v in elections_eligibility.eligible_voters_from_memberships(
                    election=for_election,
                    eligible_group_cn="",
                )
            }
        except ElectionEligibilityError as exc:
            messages.error(request, str(exc))
            return set(), set()

        return eligible_usernames, nomination_usernames

    eligible_voter_usernames: set[str] = set()
    nomination_eligible_usernames: set[str] = set()
    if election is not None:
        eligible_voter_usernames, nomination_eligible_usernames = _membership_eligibility_sets(election)

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

        _disable_details_form_for_started(election, details_form)

        if action == "save_draft":
            candidate_formset = CandidateWizardFormSet(
                request.POST, queryset=_candidate_queryset(election), prefix="candidates",
            )
            group_formset = ExclusionGroupWizardFormSet(
                request.POST, queryset=_group_queryset(election), prefix="groups",
            )
        elif action == "extend_end":
            candidate_formset = CandidateWizardFormSet(
                queryset=_candidate_queryset(election), prefix="candidates",
            )
            group_formset = ExclusionGroupWizardFormSet(
                queryset=_group_queryset(election), prefix="groups",
            )
        else:
            candidate_formset = CandidateWizardFormSet(queryset=Candidate.objects.none(), prefix="candidates")
            group_formset = ExclusionGroupWizardFormSet(queryset=ExclusionGroup.objects.none(), prefix="groups")

        if election is not None:
            eligible_voter_usernames, nomination_eligible_usernames = _membership_eligibility_sets(election)

        if action == "save_draft":
            _configure_candidate_choices(request, election, candidate_formset)
            _configure_group_choices_for_formset(request, election, candidate_formset, group_formset)

        if election is not None and election.status != Election.Status.draft:
            _disable_formset_fields(candidate_formset, group_formset)

        candidate_usernames: list[str] = []
        nominator_usernames: list[str] = []
        formsets_ok = True
        if action == "save_draft":
            formsets_ok = bool(candidate_formset.is_valid() and group_formset.is_valid())
            if formsets_ok:
                for form in candidate_formset.forms:
                    if not hasattr(form, "cleaned_data"):
                        continue
                    if form.cleaned_data.get("DELETE"):
                        continue

                    username = str(form.cleaned_data.get("freeipa_username") or "").strip()
                    nominator = str(form.cleaned_data.get("nominated_by") or "").strip()
                    if username:
                        candidate_usernames.append(username)
                    if nominator:
                        nominator_usernames.append(nominator)

        if action == "save_draft" and election is not None and election.status != Election.Status.draft:
            messages.error(request, "This election is no longer in draft; draft changes are locked.")
            formsets_ok = False

        if action == "extend_end":
            if election is None:
                messages.error(request, "Save the draft first.")
            else:
                result = _extend_election_end_from_post(request=request, election=election)
                if result.success:
                    messages.success(request, "Election end date extended.")
                    return redirect("election-edit", election_id=election.id)
                for msg in result.errors:
                    details_form.add_error("end_datetime", msg)
                    messages.error(request, msg)

        email_save_mode = str(request.POST.get("email_save_mode") or "").strip()

        if action == "save_draft" and details_form.is_valid() and email_form.is_valid() and formsets_ok:
            election = details_form.save(commit=False)

            try:
                validation = elections_eligibility.validate_candidates_for_election(
                    election=election,
                    candidate_usernames=candidate_usernames,
                    nominator_usernames=nominator_usernames,
                    eligible_group_cn=str(election.eligible_group_cn or "").strip(),
                )
            except ElectionEligibilityError as exc:
                messages.error(request, str(exc))
                formsets_ok = False
            else:
                for form in candidate_formset.forms:
                    if not hasattr(form, "cleaned_data"):
                        continue
                    if form.cleaned_data.get("DELETE"):
                        continue
                    username = str(form.cleaned_data.get("freeipa_username") or "").strip()
                    nominator = str(form.cleaned_data.get("nominated_by") or "").strip()
                    if username and username in validation.disqualified_candidates:
                        form.add_error(
                            "freeipa_username",
                            "Election committee members cannot be candidates for this election.",
                        )
                        formsets_ok = False
                    elif username and username in validation.ineligible_candidates:
                        form.add_error("freeipa_username", "User is not eligible.")
                        formsets_ok = False
                    if nominator and nominator in validation.disqualified_nominators:
                        form.add_error(
                            "nominated_by",
                            "Election committee members cannot nominate candidates for this election.",
                        )
                        formsets_ok = False
                    elif nominator and nominator in validation.ineligible_nominators:
                        form.add_error("nominated_by", "User is not eligible.")
                        formsets_ok = False

            if formsets_ok:
                election.status = Election.Status.draft

                if election_id == 0 or email_save_mode != "keep_existing":
                    _apply_email_template_from_form(election, email_form)

                election.save()
                _save_candidates_and_groups(election, candidate_formset, group_formset)

                messages.success(request, "Draft saved.")
                return redirect("election-edit", election_id=election.id)

        if action == "start_election":
            result = _handle_start_election(request, election, details_form, email_form)
            if result is not None:
                return result

        messages.error(request, "Please correct the errors below.")
    else:
        details_form = ElectionDetailsForm(instance=election)
        _disable_details_form_for_started(election, details_form)

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
            queryset=_candidate_queryset(election), prefix="candidates",
        )
        group_formset = ExclusionGroupWizardFormSet(
            queryset=_group_queryset(election), prefix="groups",
        )

        if election is not None and election.status != Election.Status.draft:
            _disable_formset_fields(candidate_formset, group_formset)

        _configure_candidate_choices(request, election, candidate_formset)
        _configure_group_choices_for_formset(request, election, candidate_formset, group_formset)

    rendered_preview, email_template_variables = _build_email_preview(
        request, election, details_form, email_form,
    )

    # The JS adds exclusion-group rows by cloning the rendered empty-form HTML.
    # Ensure that empty form has candidate options, even if the formset recreates
    # ``empty_form`` instances when accessed from the template.
    group_empty_form = group_formset.empty_form
    if election is not None and election.pk is not None:
        candidate_choices = [
            user_choice_from_freeipa(c.freeipa_username)
            for c in Candidate.objects.filter(election=election).only("freeipa_username")
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
