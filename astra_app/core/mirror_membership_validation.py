import datetime
import hashlib
import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests
import urllib3
from django.db import transaction
from django.utils import timezone

from core.forms_membership import (
    GENERIC_ADDITIONAL_INFORMATION_QUESTION,
    MIRROR_ADDITIONAL_INFO_QUESTION,
    MembershipRequestForm,
    _AnswerKind,
    _QuestionSpec,
)
from core.membership_constants import MembershipCategoryCode
from core.membership_notes import CUSTOS, add_note
from core.models import MembershipRequest, MembershipType, MirrorMembershipValidation

_HTTP_TIMEOUT_SECONDS = 5
_MAX_ATTEMPTS = 3
_CLAIM_LEASE = datetime.timedelta(minutes=5)
_MIRROR_STALE_AFTER = datetime.timedelta(hours=24)
_TIMESTAMP_CONTENT_READ_LIMIT_BYTES = 256
_TIMESTAMP_PATH_PREFIXES = (
    "/almalinux",
    "/almalinux-kitten",
    "/alma",
)
_RETRY_BACKOFF_SCHEDULE = (
    datetime.timedelta(minutes=5),
    datetime.timedelta(minutes=15),
    datetime.timedelta(hours=1),
)
_ALLOWED_SCHEMES = {"http", "https"}
_NON_HTTP_URL_SCHEME_PREFIXES = {"data", "file", "ftp", "javascript", "mailto", "ssh", "tel"}
_GITHUB_HOSTS = {"github.com", "www.github.com"}
_GITHUB_RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}
_CLOSED_MEMBERSHIP_REQUEST_STATUSES = {
    MembershipRequest.Status.approved,
    MembershipRequest.Status.rejected,
    MembershipRequest.Status.ignored,
    MembershipRequest.Status.rescinded,
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MirrorRequestAnswers:
    domain_url: str
    pull_request_url: str
    additional_info: str

    def as_dict(self) -> dict[str, str]:
        return {
            "domain_url": self.domain_url,
            "pull_request_url": self.pull_request_url,
            "additional_info": self.additional_info,
        }


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    overall_status: str
    result: dict[str, object]
    should_retry: bool


class MirrorValidationError(RuntimeError):
    def __init__(self, *, category: str) -> None:
        super().__init__(category)
        self.category = category


class UnsafeMirrorTargetError(MirrorValidationError):
    pass


class RetryableMirrorValidationError(MirrorValidationError):
    pass


class MalformedMirrorAnswerError(MirrorValidationError):
    pass


class InaccessibleMirrorTargetError(MirrorValidationError):
    pass


class GitHubValidationMalformedError(MirrorValidationError):
    pass


class GitHubValidationWrongRepoError(MirrorValidationError):
    pass


class _BoundHTTPResponse:
    def __init__(
        self,
        *,
        response: urllib3.response.BaseHTTPResponse,
        pool: urllib3.connectionpool.HTTPConnectionPool,
    ) -> None:
        self._response = response
        self._pool = pool
        self.status_code = int(response.status)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._pool.close()

    def read(self, amt: int | None = None, decode_content: bool = False) -> bytes:
        return self._response.read(amt, decode_content=decode_content)


def is_mirror_membership_request(membership_request: MembershipRequest) -> bool:
    return membership_request.membership_type.category_id == MembershipCategoryCode.mirror


def mirror_request_answers(membership_request: MembershipRequest) -> MirrorRequestAnswers | None:
    return mirror_request_answers_from_responses(
        membership_type=membership_request.membership_type,
        responses=membership_request.responses,
    )


def mirror_request_answers_from_responses(
    *,
    membership_type: MembershipType,
    responses: list[dict[str, str]] | None,
) -> MirrorRequestAnswers | None:
    if membership_type.category_id != MembershipCategoryCode.mirror:
        return None

    response_by_name = _response_map_from_responses(membership_type=membership_type, responses=responses)
    specs = MembershipRequestForm.question_specs_for_membership_type(membership_type)

    answers: dict[str, str] = {}
    for spec in specs:
        answers[spec.name] = _normalize_answer(spec=spec, value=response_by_name.get(spec.name, ""))

    return MirrorRequestAnswers(
        domain_url=answers.get("Domain", ""),
        pull_request_url=answers.get("Pull request", ""),
        additional_info=answers.get("Additional info", ""),
    )


def mirror_answers_fingerprint(answers: MirrorRequestAnswers) -> str:
    return _sha256_hexdigest(json.dumps(answers.as_dict(), sort_keys=True, separators=(",", ":")))


@transaction.atomic
def schedule_mirror_membership_validation(*, membership_request: MembershipRequest) -> MirrorMembershipValidation | None:
    answers = mirror_request_answers(membership_request)
    if answers is None:
        return None

    now = timezone.now()
    fingerprint = mirror_answers_fingerprint(answers)
    validation, created = MirrorMembershipValidation.objects.select_for_update().get_or_create(
        membership_request=membership_request,
        defaults={
            "status": MirrorMembershipValidation.Status.pending,
            "answer_fingerprint": fingerprint,
            "next_run_at": now,
        },
    )
    if created:
        return validation

    changed_answers = validation.answer_fingerprint != fingerprint
    retryable_pending = validation.status == MirrorMembershipValidation.Status.failed_retryable
    running_expired = (
        validation.status == MirrorMembershipValidation.Status.running
        and validation.claim_expires_at is not None
        and validation.claim_expires_at <= now
    )

    if not changed_answers and not retryable_pending and not running_expired:
        return validation

    validation.status = MirrorMembershipValidation.Status.pending
    validation.answer_fingerprint = fingerprint
    validation.result = {}
    validation.attempt_count = 0
    validation.last_attempt_at = None
    validation.next_run_at = now
    validation.claimed_at = None
    validation.claim_expires_at = None
    validation.noted_result_fingerprint = ""
    validation.noted_at = None
    validation.save(
        update_fields=[
            "status",
            "answer_fingerprint",
            "result",
            "attempt_count",
            "last_attempt_at",
            "next_run_at",
            "claimed_at",
            "claim_expires_at",
            "noted_result_fingerprint",
            "noted_at",
        ]
    )
    return validation


def eligible_validation_queryset(*, now: datetime.datetime, force: bool) -> object:
    from django.db.models import Q

    status_filter = Q(status__in=[MirrorMembershipValidation.Status.pending, MirrorMembershipValidation.Status.failed_retryable])
    if force:
        due_filter = status_filter
    else:
        due_filter = status_filter & Q(next_run_at__lte=now)

    closed_requests = Q(membership_request__status__in=_CLOSED_MEMBERSHIP_REQUEST_STATUSES)
    expired_running = Q(status=MirrorMembershipValidation.Status.running, claim_expires_at__lte=now)
    return MirrorMembershipValidation.objects.filter(closed_requests | due_filter | expired_running)


@transaction.atomic
def claim_next_validation(*, now: datetime.datetime, force: bool) -> tuple[MirrorMembershipValidation, bool] | None:
    queryset = eligible_validation_queryset(now=now, force=force).select_for_update(skip_locked=True).order_by(
        "next_run_at",
        "pk",
    )
    validation = queryset.first()
    if validation is None:
        return None

    reclaimed = validation.status == MirrorMembershipValidation.Status.running
    validation.status = MirrorMembershipValidation.Status.running
    validation.claimed_at = now
    validation.claim_expires_at = now + _CLAIM_LEASE
    validation.save(update_fields=["status", "claimed_at", "claim_expires_at"])
    return validation, reclaimed


def dry_run_validations(*, now: datetime.datetime, force: bool) -> list[MirrorMembershipValidation]:
    return list(eligible_validation_queryset(now=now, force=force).order_by("next_run_at", "pk"))


def run_validation(*, membership_request: MembershipRequest) -> ValidationOutcome:
    if membership_request.status in _CLOSED_MEMBERSHIP_REQUEST_STATUSES:
        result = {
            "domain": {"status": "not_checked", "detail": "request closed before validation"},
            "timestamp": {"status": "not_checked", "detail": "request closed before validation"},
            "github": {"status": "not_checked", "detail": "request closed before validation"},
        }
        return ValidationOutcome(
            overall_status=MirrorMembershipValidation.Status.skipped,
            result=result,
            should_retry=False,
        )

    answers = mirror_request_answers(membership_request)
    if answers is None:
        result = {
            "domain": {"status": "not_checked", "detail": "request is not a mirror membership"},
            "timestamp": {"status": "not_checked", "detail": "request is not a mirror membership"},
            "github": {"status": "not_checked", "detail": "request is not a mirror membership"},
        }
        return ValidationOutcome(
            overall_status=MirrorMembershipValidation.Status.skipped,
            result=result,
            should_retry=False,
        )

    domain_result = _validate_domain(answers.domain_url)
    if domain_result["status"] == "reachable":
        timestamp_result = _validate_timestamp_files(answers.domain_url)
    else:
        timestamp_result = {
            "status": "not_checked",
            "detail": f"domain status was {domain_result['status']}",
            "checked_urls": [],
        }
    github_result = _validate_github_reference(answers.pull_request_url)
    sanitized_answers = answers.as_dict()
    sanitized_answers["domain_url"] = _sanitize_http_url_for_storage(answers.domain_url, keep_path=False)
    sanitized_answers["pull_request_url"] = _sanitize_github_url_for_storage(answers.pull_request_url)

    result = {
        "domain": domain_result,
        "timestamp": timestamp_result,
        "github": github_result,
        "answers": sanitized_answers,
    }

    terminal_failure = any(
        item.get("status")
        in {
            "unsafe_target",
            "malformed",
            "inaccessible",
            "not_found",
            "stale",
            "invalid_malformed",
            "invalid_wrong_repo",
            "invalid_not_found",
        }
        for item in (domain_result, timestamp_result, github_result)
    )
    retryable_failure = any(
        item.get("status") in {"retryable_failure", "retryable_upstream_failure"}
        for item in (domain_result, timestamp_result, github_result)
    )

    if terminal_failure:
        return ValidationOutcome(
            overall_status=MirrorMembershipValidation.Status.failed_terminal,
            result=result,
            should_retry=False,
        )
    if retryable_failure:
        return ValidationOutcome(
            overall_status=MirrorMembershipValidation.Status.failed_retryable,
            result=result,
            should_retry=True,
        )
    return ValidationOutcome(
        overall_status=MirrorMembershipValidation.Status.completed,
        result=result,
        should_retry=False,
    )


@transaction.atomic
def finalize_validation(
    *,
    validation: MirrorMembershipValidation,
    outcome: ValidationOutcome,
    now: datetime.datetime,
) -> str | None:
    claimed_fingerprint = validation.answer_fingerprint
    claimed_at = validation.claimed_at
    try:
        current_validation = MirrorMembershipValidation.objects.select_for_update().select_related("membership_request").get(
            pk=validation.pk,
        )
    except MirrorMembershipValidation.DoesNotExist:
        return None

    # Resubmission can requeue the row while an older worker is still finishing.
    # Drop that stale outcome instead of attaching it to refreshed answers.
    if (
        current_validation.status != MirrorMembershipValidation.Status.running
        or current_validation.answer_fingerprint != claimed_fingerprint
        or current_validation.claimed_at != claimed_at
    ):
        validation.status = current_validation.status
        validation.answer_fingerprint = current_validation.answer_fingerprint
        validation.attempt_count = current_validation.attempt_count
        validation.result = current_validation.result
        validation.claimed_at = current_validation.claimed_at
        validation.claim_expires_at = current_validation.claim_expires_at
        return None

    validation = current_validation

    answers = mirror_request_answers(validation.membership_request)
    if answers is None:
        validation.answer_fingerprint = ""
    else:
        validation.answer_fingerprint = mirror_answers_fingerprint(answers)

    validation.attempt_count += 1
    validation.last_attempt_at = now
    validation.result = outcome.result
    validation.claimed_at = None
    validation.claim_expires_at = None

    should_note = False
    note_content: str | None = None
    if outcome.overall_status == MirrorMembershipValidation.Status.failed_retryable:
        if validation.attempt_count >= _MAX_ATTEMPTS:
            validation.status = MirrorMembershipValidation.Status.failed_terminal
            validation.next_run_at = now
            validation.result = {
                **outcome.result,
                "retry_exhausted": True,
                "retry_exhausted_after": validation.attempt_count,
            }
            should_note = True
        else:
            validation.status = MirrorMembershipValidation.Status.failed_retryable
            validation.next_run_at = now + _retry_backoff_for_attempt(validation.attempt_count)
    else:
        validation.status = outcome.overall_status
        validation.next_run_at = now
        should_note = validation.status in {
            MirrorMembershipValidation.Status.completed,
            MirrorMembershipValidation.Status.failed_terminal,
            MirrorMembershipValidation.Status.skipped,
        }

    if should_note:
        note_content = build_validation_note_content(validation=validation)
        note_fingerprint = _sha256_hexdigest(f"{validation.answer_fingerprint}\n{note_content}")
        if note_fingerprint != validation.noted_result_fingerprint:
            add_note(
                membership_request=validation.membership_request,
                username=CUSTOS,
                content=note_content,
            )
            validation.noted_result_fingerprint = note_fingerprint
            validation.noted_at = now
        else:
            note_content = None

    validation.save(
        update_fields=[
            "status",
            "answer_fingerprint",
            "attempt_count",
            "last_attempt_at",
            "next_run_at",
            "claimed_at",
            "claim_expires_at",
            "result",
            "noted_result_fingerprint",
            "noted_at",
        ]
    )
    return note_content


def build_validation_note_content(*, validation: MirrorMembershipValidation) -> str:
    result = validation.result or {}
    lines = ["Mirror validation summary"]
    lines.append(f"Domain: {_describe_domain_result(result.get('domain', {}))}")
    lines.append(f"Mirror status: {_describe_timestamp_result(result.get('timestamp', {}))}")
    lines.append(f"GitHub pull request: {_describe_github_result(result.get('github', {}))}")
    if result.get("retry_exhausted"):
        lines.append(f"retry exhausted after {result.get('retry_exhausted_after', validation.attempt_count)} attempts.")
    return "\n".join(lines)


def _response_map_from_request(membership_request: MembershipRequest) -> dict[str, str]:
    return _response_map_from_responses(
        membership_type=membership_request.membership_type,
        responses=membership_request.responses,
    )


def _response_map_from_responses(
    *,
    membership_type: MembershipType,
    responses: list[dict[str, str]] | None,
) -> dict[str, str]:
    specs = MembershipRequestForm.question_specs_for_membership_type(membership_type)
    wanted = {spec.name.strip().lower(): spec.name for spec in specs}
    answers: dict[str, str] = {spec.name: "" for spec in specs}
    mirror_additional_info_answer: str | None = None
    mirror_clarification_alias_answer: str | None = None
    is_mirror_membership = membership_type.category_id == MembershipCategoryCode.mirror

    for item in responses or []:
        if not isinstance(item, dict):
            continue
        for question, answer in item.items():
            key = str(question or "").strip().lower()
            answer_value = str(answer or "").strip()
            if is_mirror_membership and key == MIRROR_ADDITIONAL_INFO_QUESTION.lower():
                mirror_additional_info_answer = answer_value
                continue
            if is_mirror_membership and key == GENERIC_ADDITIONAL_INFORMATION_QUESTION.lower():
                mirror_clarification_alias_answer = answer_value
                continue
            canonical_name = wanted.get(key)
            if canonical_name is None:
                continue
            answers[canonical_name] = answer_value

    if is_mirror_membership:
        if mirror_clarification_alias_answer:
            answers[MIRROR_ADDITIONAL_INFO_QUESTION] = mirror_clarification_alias_answer
        elif mirror_additional_info_answer is not None:
            answers[MIRROR_ADDITIONAL_INFO_QUESTION] = mirror_additional_info_answer
    return answers


def _normalize_answer(*, spec: _QuestionSpec, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if spec.answer_kind == _AnswerKind.url and spec.url_assume_scheme and "://" not in normalized:
        split = urlsplit(normalized)
        if split.scheme.lower() in _NON_HTTP_URL_SCHEME_PREFIXES and not split.netloc:
            return normalized
        return f"{spec.url_assume_scheme}://{normalized}"
    return normalized


def _validate_domain(url: str) -> dict[str, object]:
    normalized = ""
    sanitized_url = _sanitize_http_url_for_storage(url, keep_path=False)
    try:
        normalized = _normalize_http_url(url)
        sanitized_url = _sanitize_http_url_for_storage(normalized, keep_path=False)
        response = _bound_http_get(normalized)
    except UnsafeMirrorTargetError as exc:
        return {"status": "unsafe_target", "detail": exc.category, "url": normalized or sanitized_url}
    except MalformedMirrorAnswerError as exc:
        return {"status": "malformed", "detail": exc.category, "url": normalized or sanitized_url}
    except InaccessibleMirrorTargetError as exc:
        return {"status": "inaccessible", "detail": exc.category, "url": normalized or sanitized_url}
    except requests.exceptions.RequestException as exc:
        return {"status": "retryable_failure", "detail": _request_error_category(exc), "url": normalized or sanitized_url}

    try:
        return {
            "status": "reachable",
            "url": sanitized_url,
            "http_status": int(response.status_code),
        }
    finally:
        response.close()


def _validate_timestamp_files(url: str) -> dict[str, object]:
    try:
        normalized = _normalize_http_url(url)
    except MirrorValidationError as exc:
        return {"status": "not_checked", "detail": exc.category, "checked_urls": []}

    checked_urls: list[str] = []
    for candidate in _timestamp_candidate_urls(normalized):
        sanitized_candidate = _sanitize_timestamp_url_for_storage(candidate)
        try:
            response = _bound_http_get(candidate)
        except UnsafeMirrorTargetError as exc:
            return {
                "status": "unsafe_target",
                "detail": exc.category,
                "checked_urls": checked_urls,
            }
        except InaccessibleMirrorTargetError as exc:
            return {
                "status": "retryable_failure",
                "detail": exc.category,
                "checked_urls": checked_urls,
            }
        except requests.exceptions.RequestException as exc:
            return {
                "status": "retryable_failure",
                "detail": _request_error_category(exc),
                "checked_urls": checked_urls,
            }

        checked_urls.append(sanitized_candidate)
        try:
            if response.status_code == 200:
                timestamp_value = _parse_mirror_timestamp_value(
                    response.read(_TIMESTAMP_CONTENT_READ_LIMIT_BYTES, decode_content=True),
                )
                if timestamp_value is None:
                    continue

                age = timezone.now().astimezone(datetime.UTC) - timestamp_value
                return {
                    "status": "up_to_date" if age <= _MIRROR_STALE_AFTER else "stale",
                    "url": sanitized_candidate,
                    "http_status": int(response.status_code),
                    "timestamp": timestamp_value.isoformat(),
                    "age_hours": round(age.total_seconds() / 3600, 2),
                    "checked_urls": checked_urls,
                }
            if response.status_code in _GITHUB_RETRYABLE_STATUS_CODES:
                return {
                    "status": "retryable_failure",
                    "detail": f"timestamp_http_{response.status_code}",
                    "checked_urls": checked_urls,
                }
        finally:
            response.close()

    return {
        "status": "not_found",
        "detail": "timestamp files not found",
        "checked_urls": checked_urls,
    }


def _parse_mirror_timestamp_value(raw_value: bytes) -> datetime.datetime | None:
    text = raw_value.decode("utf-8", errors="replace").strip()
    if not text:
        return None

    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=datetime.UTC)
        return parsed.astimezone(datetime.UTC)

    if text.isdigit():
        try:
            return datetime.datetime.fromtimestamp(int(text), tz=datetime.UTC)
        except (OverflowError, OSError, ValueError):
            return None

    try:
        parsed = datetime.datetime.strptime(text, "%a %b %d %H:%M:%S UTC %Y")
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.UTC)


def _validate_github_reference(url: str) -> dict[str, object]:
    sanitized_url = _sanitize_github_url_for_storage(url)
    normalized = sanitized_url
    try:
        normalized, reference_kind = _normalize_github_reference(url)
        response = requests.get(
            normalized,
            allow_redirects=False,
            stream=True,
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except GitHubValidationMalformedError as exc:
        return {"status": "invalid_malformed", "detail": exc.category, "url": sanitized_url}
    except GitHubValidationWrongRepoError as exc:
        return {"status": "invalid_wrong_repo", "detail": exc.category, "url": sanitized_url}
    except requests.exceptions.RequestException as exc:
        return {
            "status": "retryable_upstream_failure",
            "detail": _request_error_category(exc),
            "url": normalized,
        }

    try:
        if response.status_code == 200:
            return {
                "status": reference_kind,
                "url": normalized,
                "http_status": int(response.status_code),
            }
        if response.status_code == 404:
            return {
                "status": "invalid_not_found",
                "url": normalized,
                "http_status": int(response.status_code),
            }
        if response.status_code in _GITHUB_RETRYABLE_STATUS_CODES:
            return {
                "status": "retryable_upstream_failure",
                "url": normalized,
                "http_status": int(response.status_code),
            }
        return {
            "status": "invalid_malformed",
            "url": normalized,
            "http_status": int(response.status_code),
        }
    finally:
        response.close()


def _normalize_http_url(url: str) -> str:
    split = urlsplit(str(url or "").strip())
    if split.scheme not in _ALLOWED_SCHEMES or not split.netloc:
        raise MalformedMirrorAnswerError(category="invalid_http_url")
    return urlunsplit((split.scheme, _sanitized_authority(split), split.path.rstrip("/"), "", ""))


def _normalize_github_reference(url: str) -> tuple[str, str]:
    split = urlsplit(str(url or "").strip())
    if split.scheme not in _ALLOWED_SCHEMES or split.hostname not in _GITHUB_HOSTS:
        raise GitHubValidationMalformedError(category="invalid_github_url")

    parts = [part for part in split.path.split("/") if part]
    if len(parts) < 4:
        raise GitHubValidationMalformedError(category="invalid_github_path")
    owner, repo, resource, identifier = parts[:4]
    if owner.lower() != "almalinux" or repo.lower() != "mirrors":
        raise GitHubValidationWrongRepoError(category="wrong_github_repo")
    if resource == "pull" and identifier.isdigit():
        return (urlunsplit(("https", "github.com", f"/{owner}/{repo}/pull/{identifier}", "", "")), "valid")
    if resource == "commit" and identifier:
        return (urlunsplit(("https", "github.com", f"/{owner}/{repo}/commit/{identifier}", "", "")), "commit")
    raise GitHubValidationMalformedError(category="invalid_github_reference")


def _resolve_public_ip_addresses(url: str) -> tuple[str, ...]:
    split = urlsplit(url)
    hostname = split.hostname
    if hostname is None:
        raise MalformedMirrorAnswerError(category="missing_hostname")

    port = split.port or (443 if split.scheme == "https" else 80)
    try:
        address_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise InaccessibleMirrorTargetError(category="dns_lookup_failed") from None

    addresses: list[str] = []
    for family, _socktype, _proto, _canonname, sockaddr in address_info:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = sockaddr[0]
        ip = ipaddress.ip_address(address)
        # DNS resolution is the trust boundary for outbound validation.
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            logger.debug("mirror_validation.unsafe_target host=%s resolved_to=%s", hostname, address)
            raise UnsafeMirrorTargetError(category="unsafe_target")
        addresses.append(address)

    if not addresses:
        raise InaccessibleMirrorTargetError(category="no_public_address")
    return tuple(addresses)


def _timestamp_candidate_urls(url: str) -> tuple[str, ...]:
    split = urlsplit(url)
    candidates: list[str] = []
    for prefix in ("",) + _TIMESTAMP_PATH_PREFIXES:
        candidate = _replace_path(split, f"{prefix}/timestamp.txt")
        if candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def _replace_path(split: SplitResult, path: str) -> str:
    return urlunsplit((split.scheme, _sanitized_authority(split), path or "/", "", ""))


def _sanitize_http_url_for_storage(url: str, *, keep_path: bool = True) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        return ""
    split = urlsplit(normalized)
    if split.scheme not in _ALLOWED_SCHEMES or not split.netloc:
        return normalized

    path = split.path.rstrip("/") if keep_path else ""
    try:
        return urlunsplit((split.scheme, _sanitized_authority(split), path, "", ""))
    except MalformedMirrorAnswerError:
        hostname = split.hostname
        if hostname is None:
            return normalized

        authority = f"[{hostname}]" if ":" in hostname else hostname
        try:
            port = split.port
        except ValueError:
            port = None

        if port is not None:
            authority = f"{authority}:{port}"
        return urlunsplit((split.scheme, authority, path, "", ""))


def _sanitize_github_url_for_storage(url: str) -> str:
    normalized = str(url or "").strip()
    if not normalized:
        return ""
    try:
        canonical_url, _reference_kind = _normalize_github_reference(normalized)
        return canonical_url
    except (GitHubValidationMalformedError, GitHubValidationWrongRepoError):
        return _sanitize_http_url_for_storage(normalized, keep_path=False)


def _sanitize_timestamp_url_for_storage(url: str) -> str:
    origin_url = _sanitize_http_url_for_storage(url, keep_path=False)
    if not origin_url:
        return origin_url

    normalized_path = urlsplit(str(url or "").strip()).path.rstrip("/")
    for prefix in _TIMESTAMP_PATH_PREFIXES:
        path = f"{prefix}/timestamp.txt" if prefix else "/timestamp.txt"
        if normalized_path.endswith(path):
            return f"{origin_url}{path}"
    return origin_url


def _sanitized_authority(split: SplitResult) -> str:
    hostname = split.hostname
    if hostname is None:
        raise MalformedMirrorAnswerError(category="missing_hostname")

    try:
        port = split.port
    except ValueError as exc:
        raise MalformedMirrorAnswerError(category="invalid_http_url") from exc

    if ":" in hostname:
        authority = f"[{hostname}]"
    else:
        authority = hostname
    if port is None:
        return authority
    return f"{authority}:{port}"


def _describe_domain_result(result: dict[str, object]) -> str:
    status = str(result.get("status") or "unknown")
    if status == "reachable":
        return "reachable"
    if status == "unsafe_target":
        return f"unsafe target ({result.get('detail')})"
    if status == "inaccessible":
        return f"not reachable ({result.get('detail')})"
    if status == "retryable_failure":
        return f"retryable failure ({result.get('detail')})"
    if status == "not_checked":
        return str(result.get("detail") or "not checked")
    return f"invalid ({result.get('detail')})"


def _describe_timestamp_result(result: dict[str, object]) -> str:
    status = str(result.get("status") or "unknown")
    if status in {"up_to_date", "found"}:
        return "up-to-date"
    if status == "stale":
        return "stale"
    if status in {"not_found", "missing"}:
        return "not found"
    if status == "unsafe_target":
        return f"unsafe target ({result.get('detail')})"
    if status == "retryable_failure":
        return f"retryable failure ({result.get('detail')})"
    return str(result.get("detail") or "not checked")


def _bound_http_get(url: str) -> _BoundHTTPResponse:
    split = urlsplit(url)
    hostname = split.hostname
    if hostname is None:
        raise MalformedMirrorAnswerError(category="missing_hostname")

    addresses = _resolve_public_ip_addresses(url)
    connect_host = addresses[0]
    pool = _build_bound_connection_pool(split=split, connect_host=connect_host)
    try:
        response = pool.urlopen(
            "GET",
            _request_target(split),
            headers={"Host": _host_header(split)},
            redirect=False,
            preload_content=False,
            timeout=_HTTP_TIMEOUT_SECONDS,
            retries=False,
        )
    except requests.exceptions.RequestException:
        pool.close()
        raise
    except Exception as exc:
        pool.close()
        raise _coerce_transport_exception(exc) from exc
    return _BoundHTTPResponse(response=response, pool=pool)


def _build_bound_connection_pool(
    *,
    split: SplitResult,
    connect_host: str,
) -> urllib3.connectionpool.HTTPConnectionPool:
    port = split.port or (443 if split.scheme == "https" else 80)
    if split.scheme == "https":
        hostname = split.hostname
        if hostname is None:
            raise MalformedMirrorAnswerError(category="missing_hostname")
        return urllib3.connectionpool.HTTPSConnectionPool(
            host=connect_host,
            port=port,
            cert_reqs="CERT_REQUIRED",
            ca_certs=requests.certs.where(),
            assert_hostname=hostname,
            server_hostname=hostname,
        )
    return urllib3.connectionpool.HTTPConnectionPool(host=connect_host, port=port)


def _host_header(split: SplitResult) -> str:
    hostname = split.hostname
    if hostname is None:
        raise MalformedMirrorAnswerError(category="missing_hostname")
    if split.port is None:
        return hostname
    default_port = 443 if split.scheme == "https" else 80
    if split.port == default_port:
        return hostname
    if ":" in hostname:
        return f"[{hostname}]:{split.port}"
    return f"{hostname}:{split.port}"


def _request_target(split: SplitResult) -> str:
    path = split.path or "/"
    if split.query:
        return f"{path}?{split.query}"
    return path


def _coerce_transport_exception(exc: Exception) -> requests.exceptions.RequestException:
    if isinstance(exc, requests.exceptions.RequestException):
        return exc
    if isinstance(exc, urllib3.exceptions.TimeoutError):
        return requests.exceptions.Timeout(str(exc))
    if isinstance(exc, urllib3.exceptions.HTTPError):
        return requests.exceptions.ConnectionError(str(exc))
    if isinstance(exc, OSError):
        return requests.exceptions.ConnectionError(str(exc))
    return requests.exceptions.RequestException(str(exc))


def _describe_github_result(result: dict[str, object]) -> str:
    status = str(result.get("status") or "unknown")
    if status == "valid":
        return "valid"
    if status == "commit":
        return "commit URL is valid"
    if status == "retryable_upstream_failure":
        return f"retryable upstream failure ({result.get('detail')})"
    if status == "invalid_wrong_repo":
        return "invalid wrong repo"
    if status == "invalid_not_found":
        return "not found"
    if status == "not_checked":
        return str(result.get("detail") or "not checked")
    return f"invalid ({result.get('detail')})"


def _retry_backoff_for_attempt(attempt_count: int) -> datetime.timedelta:
    if attempt_count <= 0:
        return _RETRY_BACKOFF_SCHEDULE[0]
    if attempt_count >= len(_RETRY_BACKOFF_SCHEDULE):
        return _RETRY_BACKOFF_SCHEDULE[-1]
    return _RETRY_BACKOFF_SCHEDULE[attempt_count - 1]


def _request_error_category(exc: Exception) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "connection_error"
    if isinstance(exc, requests.exceptions.TooManyRedirects):
        return "redirect_blocked"
    if isinstance(exc, requests.exceptions.RequestException):
        return "request_error"
    return type(exc).__name__.lower()


# Map requests exceptions onto stable categories used in results/notes.
def _sha256_hexdigest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
