import logging
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from email import policy
from email.message import EmailMessage
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import post_office.mail
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import HttpRequest, JsonResponse
from django.template import Context, Template, engines
from django.template.exceptions import TemplateSyntaxError
from post_office.models import Email, EmailTemplate

_VAR_PATTERN = re.compile(r"{{\s*([A-Za-z0-9_]+)")
_PLURALIZE_PATTERN = re.compile(
    r"{{\s*(?P<var>[A-Za-z0-9_]+)\s*\|\s*pluralize(?:\s*:\s*(?P<q>['\"])(?P<arg>.*?)(?P=q))?\s*}}"
)

_INLINE_IMAGE_TAG_PATTERN = re.compile(
    r"{%-?\s*inline_image\s+(?:(?P<q>['\"])(?P<urlq>.*?)(?P=q)|(?P<urlb>[^\s%}]+))\s*-?%}"
)

_INLINE_IMAGE_TAG_REWRITE_PATTERN = re.compile(
    r"(?P<prefix>{%-?\s*inline_image\s+)"
    r"(?:(?P<q>['\"])(?P<urlq>.*?)(?P=q)|(?P<urlb>[^\s%}]+))"
    r"(?P<suffix>\s*-?%})"
)

_MAX_INLINE_IMAGE_BYTES: int = 10 * 1024 * 1024

logger = logging.getLogger(__name__)

_REQUIRED_TEMPLATE_VAR_PATTERN = re.compile(r"{{\s*(?P<var>[A-Za-z0-9_]+)\b")


def configured_email_template_names() -> frozenset[str]:
    """Return template names referenced by Django settings.

    These templates are effectively part of the app's runtime configuration.
    Renaming/deleting them via the UI can break critical workflows.

    This is intentionally explicit (no reflection over settings) so the list is
    obvious and safe to audit.
    """

    return frozenset(
        {
            settings.MEMBERSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME,
            settings.ORGANIZATION_SPONSORSHIP_EXPIRING_SOON_EMAIL_TEMPLATE_NAME,
            settings.ORGANIZATION_SPONSORSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME,
            settings.FREEIPA_MEMBERSHIP_RECONCILE_ALERT_EMAIL_TEMPLATE_NAME,
            settings.PASSWORD_RESET_EMAIL_TEMPLATE_NAME,
            settings.PASSWORD_RESET_SUCCESS_EMAIL_TEMPLATE_NAME,
            settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
            settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME,
            settings.REGISTRATION_EMAIL_TEMPLATE_NAME,
            settings.EMAIL_VALIDATION_EMAIL_TEMPLATE_NAME,
            settings.ACCOUNT_INVITE_EMAIL_TEMPLATE_NAME,
            settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
            *settings.ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES,
        }
    )


def locked_email_template_names() -> frozenset[str]:
    """Return template names that must not be deleted/renamed via the UI.

    This includes templates referenced by settings as well as templates that are
    attached to membership types.
    """

    locked = set(configured_email_template_names())

    # Import lazily to avoid import cycles during Django startup.
    from core.models import MembershipType

    for membership_type in (
        MembershipType.objects.select_related("acceptance_template")
        .exclude(acceptance_template__isnull=True)
        .only("acceptance_template__name")
    ):
        name = str(membership_type.acceptance_template.name or "").strip()
        if name:
            locked.add(name)

    return frozenset(locked)


def validate_email_subject_no_folding(subject: str) -> None:
    """Reject subjects that would be serialized as folded headers.

    RFC header folding is valid, but some downstream tooling mishandles it.
    We validate at template-save time so users can't create templates that
    will later generate folded Subject headers.
    """

    value = str(subject or "")
    if not value.strip():
        return

    if "\n" in value or "\r" in value:
        raise ValidationError("Subject must be a single line.")

    msg = EmailMessage(policy=policy.SMTP)
    msg["Subject"] = value
    raw = msg.as_bytes()
    lines = raw.splitlines()

    for idx, line in enumerate(lines):
        if line.lower().startswith(b"subject:"):
            next_idx = idx + 1
            if next_idx < len(lines) and lines[next_idx].startswith((b" ", b"\t")):
                raise ValidationError(
                    "Subject is too long and will be split across multiple header lines. "
                    "Please keep it shorter."
                )
            return


def _storage_key_from_inline_image_arg(raw: str) -> str:
    """Infer a storage key from an inline_image argument.

    django-post-office's built-in `inline_image` tag only supports:
    - absolute filesystem paths, or
    - staticfiles finders paths.

    In this app we store uploaded images in default_storage (S3/MinIO). Users
    paste either a storage URL or a bucket-prefixed path; for sending, we stage
    the object into a local temp file and point inline_image at that temp path.
    """

    value = str(raw or "").strip()
    if not value:
        raise ValueError("inline_image requires a non-empty path or URL")

    bucket = str(settings.AWS_STORAGE_BUCKET_NAME or "").strip()

    if "://" in value:
        parts = urlsplit(value)
        path = str(parts.path or "").lstrip("/")

        # Path-style URL: /<bucket>/<key>
        if bucket and path.startswith(f"{bucket}/"):
            return path[len(bucket) + 1 :]

        # Virtual-host style: <bucket>.<domain>/<key>
        if bucket and str(parts.netloc or "").startswith(f"{bucket}."):
            return path

        custom_domain = str(settings.AWS_S3_CUSTOM_DOMAIN or "").strip()
        if custom_domain:
            custom_parts = urlsplit(custom_domain) if "://" in custom_domain else urlsplit(f"https://{custom_domain}")
            if str(parts.netloc or "") == str(custom_parts.netloc or ""):
                custom_path = str(custom_parts.path or "").strip("/")
                if custom_path:
                    if path.startswith(f"{custom_path}/"):
                        return path[len(custom_path) + 1 :]
                else:
                    return path

        raise ValueError(
            "inline_image URLs must point to the configured storage bucket; "
            f"could not infer storage key from: {value}"
        )

    # Common: user pastes a bucket-prefixed key like "astra-media/mail-images/logo.png".
    if bucket:
        normalized = value.lstrip("/")
        if normalized.startswith(f"{bucket}/"):
            return normalized[len(bucket) + 1 :]

    return value.lstrip("/")


def stage_inline_images_for_sending(html_content: str, *, strict: bool = True) -> tuple[str, list[str]]:
    """Rewrite inline_image arguments to local temp files for sending.

    Returns (rewritten_html, temp_paths). Callers must delete temp_paths.
    """

    staged: dict[str, str] = {}
    temp_paths: list[str] = []

    def repl(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        suffix = match.group("suffix")
        quote = match.group("q")
        raw_arg = match.group("urlq") or match.group("urlb") or ""
        raw_arg = str(raw_arg).strip()
        if not raw_arg:
            return match.group(0)

        local_path = staged.get(raw_arg)
        if local_path is None:
            try:
                key = _storage_key_from_inline_image_arg(raw_arg)
            except ValueError:
                if strict:
                    raise
                logger.warning("Inline image arg could not be mapped to storage: %s", raw_arg, exc_info=True)
                return ""
            try:
                file_obj = default_storage.open(key, "rb")
            except Exception as exc:
                if strict:
                    raise ValueError(f"No such file in storage: {key}") from exc

                # Best-effort sending: missing inline images should not block the email.
                # We drop the tag entirely so clients simply won't render the image.
                logger.warning("Inline image missing in storage key=%s", key, exc_info=True)
                return ""

            with file_obj:
                data = file_obj.read(_MAX_INLINE_IMAGE_BYTES + 1)
            if len(data) > _MAX_INLINE_IMAGE_BYTES:
                raise ValueError(f"Inline image is too large (> {_MAX_INLINE_IMAGE_BYTES} bytes): {key}")

            suffix_ext = PurePosixPath(key).suffix
            if not suffix_ext:
                suffix_ext = ".bin"

            tmp = tempfile.NamedTemporaryFile(prefix="astra-inline-image-", suffix=suffix_ext, delete=False)
            try:
                tmp.write(data)
                tmp.flush()
            finally:
                tmp.close()

            local_path = tmp.name
            staged[raw_arg] = local_path
            temp_paths.append(local_path)

        if quote:
            return f"{prefix}{quote}{local_path}{quote}{suffix}"
        return f"{prefix}{local_path}{suffix}"

    rewritten = _INLINE_IMAGE_TAG_REWRITE_PATTERN.sub(repl, str(html_content or ""))
    return rewritten, temp_paths


def queue_templated_email(
    *,
    recipients: list[str],
    sender: str,
    template_name: str,
    context: Mapping[str, object],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: list[str] | None = None,
    strict_inline_images: bool = False,
) -> Email:
    """Queue an EmailTemplate with inline_image support and storage-backed images.

    We render subject/text/html immediately (using the `post_office` template
    engine so `{% inline_image %}` works), stage any storage-backed images into
    local temp files, and convert attached inline images into post_office
    Attachments with the correct Content-ID headers.

    This avoids delivery-time template rendering failures (e.g. FileNotFoundError
    for inline image URLs) from blocking the email from being queued/sent.
    """

    template = EmailTemplate.objects.get(name=template_name)

    # Templates are editable via the django-post-office UI. For critical workflows,
    # warn operators if the effective template stops rendering required variables.
    if template_name == settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME:
        sources = (template.subject or "", template.content or "", template.html_content or "")
        rendered_vars = {
            m.group("var")
            for src in sources
            for m in _REQUIRED_TEMPLATE_VAR_PATTERN.finditer(src)
        }
        for var in ["ballot_hash", "nonce", "weight", "chain_hash", "previous_chain_hash"]:
            if var not in rendered_vars:
                logger.warning(
                    f"Vote receipt EmailTemplate is missing required variable: {var} (template={template_name})",
                )

    staged_files: list[str] = []
    try:
        staged_html_content, staged_files = stage_inline_images_for_sending(
            template.html_content or "",
            strict=strict_inline_images,
        )

        template_engine = engines["post_office"]
        subject_template = template_engine.from_string(template.subject or "")
        text_template = template_engine.from_string(preview_drop_inline_image_tags(template.content or ""))
        html_template = template_engine.from_string(staged_html_content)

        rendered_subject = subject_template.render(dict(context))
        rendered_text = text_template.render(dict(context))
        rendered_html = html_template.render(dict(context))

        attachments: dict[str, object] = {}
        for image in getattr(html_template, "_attached_images", []) or []:
            cid = str(image.get("Content-ID") or "").strip().strip("<>")
            if not cid:
                continue
            payload = image.get_payload(decode=True) or b""
            content_type = str(image.get_content_type() or "application/octet-stream")
            headers = {str(k): str(v) for k, v in image.items()}

            filename = cid
            subtype = content_type.split("/", 1)[-1]
            if subtype and subtype.isalnum():
                filename = f"{cid}.{subtype}"

            attachments[filename] = {
                "file": ContentFile(payload),
                "mimetype": content_type,
                "headers": headers,
            }

        headers: dict[str, str] | None = None
        if reply_to:
            headers = {"Reply-To": ", ".join(reply_to)}

        email = post_office.mail.send(
            recipients=recipients,
            sender=sender,
            subject=rendered_subject,
            message=rendered_text,
            html_message=rendered_html,
            attachments=attachments or None,
            render_on_delivery=False,
            cc=cc,
            bcc=bcc,
            headers=headers,
        )

        # Preserve linkage to the EmailTemplate and original context so the admin UI
        # and any dedupe logic based on template/context continue to work.
        try:
            email.template = template
            email.context = dict(context)
            email.save(update_fields=["template", "context"])
        except Exception:
            logger.exception("Failed to persist post_office template/context metadata template=%s", template_name)

        return email
    finally:
        for path in staged_files:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except Exception:
                logger.exception("Failed to delete temp inline image path=%s", path)


def _iter_pluralize_vars(*sources: str) -> Iterable[str]:
    for s in sources:
        for m in _PLURALIZE_PATTERN.finditer(str(s or "")):
            yield m.group("var")


def _parse_pluralize_arg(raw_arg: str | None) -> tuple[str, str]:
    """Return (singular, plural) for the pluralize filter argument.

    - No arg: equivalent to pluralize:,s
    - One part: pluralize:'es' => ('', 'es')
    - Two parts: pluralize:'y,ies' => ('y', 'ies')
    """

    if raw_arg is None:
        return "", "s"

    arg = str(raw_arg)
    if "," not in arg:
        return "", arg

    singular, plural = arg.split(",", 1)
    return singular, plural


def _render_pluralize_placeholder(*, singular: str, plural: str) -> str:
    if singular:
        return f"-{singular}/{plural}-"
    return f"-/{plural}-"


def _try_get_count(value: object) -> int | None:
    if isinstance(value, int):
        return value

    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit():
            return int(raw)
        return None

    # Best-effort: if it's a container (list/queryset/etc), use its length.
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        return None


def _coerce_pluralize_inputs(*, render_context: dict[str, object], sources: tuple[str, ...]) -> None:
    """Normalize values used with `pluralize` so Django picks correctly.

    Django's `pluralize` compares against integer 1; if a template provides
    count values as strings (e.g. "1"), the default behavior can select the
    wrong branch. For preview, we try to coerce integer-like strings.
    """

    for var in set(_iter_pluralize_vars(*sources)):
        if var not in render_context:
            continue

        value = render_context[var]
        if isinstance(value, str):
            raw = value.strip()
            if raw.isdigit():
                render_context[var] = int(raw)


def _apply_pluralize_placeholders(*, template: str, render_context: Mapping[str, object]) -> str:
    """Render `pluralize` to a deterministic preview string.

    For unknown values (missing or not count-like), render a placeholder
    `-singular/plural-` (or `-/plural-` for empty singular).
    """

    def repl(match: re.Match[str]) -> str:
        var = match.group("var")
        singular, plural = _parse_pluralize_arg(match.group("arg"))

        if var not in render_context:
            return _render_pluralize_placeholder(singular=singular, plural=plural)

        count = _try_get_count(render_context[var])
        if count is None:
            return _render_pluralize_placeholder(singular=singular, plural=plural)

        return singular if count == 1 else plural

    return _PLURALIZE_PATTERN.sub(repl, template or "")


def placeholder_context_from_sources(*sources: str) -> dict[str, str]:
    names: set[str] = set()
    for s in sources:
        for m in _VAR_PATTERN.finditer(str(s or "")):
            names.add(m.group(1))
    return {name: f"-{name}-" for name in sorted(names)}


def preview_rewrite_inline_image_tags_to_urls(value: str) -> str:
    """Rewrite `{% inline_image 'URL' %}` to `URL` for HTML previews.

    This is intentionally preview-only: real sending needs the tag so
    django-post-office can embed the image as an inline attachment.
    """

    def repl(match: re.Match[str]) -> str:
        quoted = match.group("urlq")
        if quoted is not None:
            return quoted
        bare = match.group("urlb")
        return bare or ""

    return _INLINE_IMAGE_TAG_PATTERN.sub(repl, str(value or ""))


def preview_drop_inline_image_tags(value: str) -> str:
    """Drop `{% inline_image '...' %}` entirely (useful for plain-text previews)."""

    return _INLINE_IMAGE_TAG_PATTERN.sub("", str(value or ""))


def placeholderize_empty_values(context: Mapping[str, object]) -> dict[str, object]:
    """Return a copy of context with empty values replaced by `-var-` placeholders.

    This is intended for email preview/UX: if a variable is present but empty
    (e.g. blank election name during create), show a deterministic placeholder
    so templates remain readable.
    """

    out: dict[str, object] = dict(context)
    for key, value in list(out.items()):
        if value is None:
            out[key] = f"-{key}-"
            continue
        if isinstance(value, str) and not value.strip():
            out[key] = f"-{key}-"
    return out


def render_template_string(value: str, context: Mapping[str, object]) -> str:
    """Render a Django template string with a plain context.

    Raise ValueError for template syntax errors so callers can consistently
    surface user-facing messages.
    """

    post_office_engine = None
    try:
        post_office_engine = engines["post_office"]
    except Exception:
        post_office_engine = None

    if post_office_engine is not None:
        try:
            return post_office_engine.from_string(value or "").render(dict(context))
        except TemplateSyntaxError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    try:
        return Template(value or "").render(Context(dict(context)))
    except TemplateSyntaxError as exc:
        raise ValueError(str(exc)) from exc


def render_templated_email_preview(*, subject: str, html_content: str, text_content: str, context: Mapping[str, object]) -> dict[str, str]:
    sources = (subject, html_content, text_content)

    # Always start with placeholders inferred from the template sources, then
    # overlay caller-provided values. This ensures:
    # - missing keys render as `-var-` placeholders (even when context provided)
    # - present-but-empty values render as `-var-` placeholders
    placeholders: dict[str, str] = placeholder_context_from_sources(*sources)
    render_context: dict[str, object] = dict(placeholders)
    if context:
        render_context.update(dict(context))

    render_context = placeholderize_empty_values(render_context)

    _coerce_pluralize_inputs(render_context=render_context, sources=sources)

    subject = _apply_pluralize_placeholders(template=subject, render_context=render_context)
    html_content = _apply_pluralize_placeholders(template=html_content, render_context=render_context)
    text_content = _apply_pluralize_placeholders(template=text_content, render_context=render_context)

    return {
        "subject": render_template_string(subject, render_context),
        "html": render_template_string(html_content, render_context),
        "text": render_template_string(text_content, render_context),
    }


def render_templated_email_preview_response(*, request: HttpRequest, context: Mapping[str, object]) -> JsonResponse:
    """Render preview from request.POST and return a JSON response.

    This keeps view code focused on gathering context. Rendering and template
    error handling (syntax errors surfaced as 400s) is centralized here.
    """

    subject = str(request.POST.get("subject") or "")
    html_content = preview_rewrite_inline_image_tags_to_urls(str(request.POST.get("html_content") or ""))
    text_content = preview_drop_inline_image_tags(str(request.POST.get("text_content") or ""))

    try:
        return JsonResponse(
            render_templated_email_preview(
                subject=subject,
                html_content=html_content,
                text_content=text_content,
                context=context,
            )
        )
    except ValueError as exc:
        return JsonResponse({"error": f"Template error: {exc}"}, status=400)


def email_template_to_dict(template: EmailTemplate) -> dict[str, object]:
    return {
        "id": template.pk,
        "name": template.name,
        "subject": template.subject or "",
        "html_content": template.html_content or "",
        "text_content": template.content or "",
    }


def update_email_template(*, template: EmailTemplate, subject: str, html_content: str, text_content: str) -> None:
    validate_email_subject_no_folding(subject)
    template.subject = subject
    template.html_content = html_content
    template.content = text_content
    template.save(update_fields=["subject", "html_content", "content"])


def unique_email_template_name(raw_name: str) -> str:
    name = raw_name
    suffix = 2
    while EmailTemplate.objects.filter(name=name).exists():
        name = f"{raw_name}-{suffix}"
        suffix += 1
    return name


def create_email_template_unique(*, raw_name: str, subject: str, html_content: str, text_content: str) -> EmailTemplate:
    validate_email_subject_no_folding(subject)
    name = unique_email_template_name(raw_name)
    return EmailTemplate.objects.create(
        name=name,
        subject=subject,
        html_content=html_content,
        content=text_content,
    )
