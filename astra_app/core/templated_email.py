import logging
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from pathlib import PurePosixPath
from typing import override
from urllib.parse import urlsplit

import css_inline
import post_office.mail
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives
from django.db.models import QuerySet
from django.http import HttpRequest, JsonResponse
from django.template import Context, Origin, Template, TemplateDoesNotExist, engines
from django.template.exceptions import TemplateSyntaxError
from django.template.loaders.base import Loader
from post_office.models import Attachment, Email, EmailTemplate
from post_office.utils import create_attachments

from core.email_context import system_email_context
from core.logging_extras import current_exception_log_fields

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

# post_office's inline_image tag renders `cid:<md5 of the image bytes>`.
_INLINE_IMAGE_CID_PATTERN = re.compile(r"cid:([0-9a-f]{32})")

_MAX_INLINE_IMAGE_BYTES: int = 10 * 1024 * 1024

logger = logging.getLogger(__name__)

_REQUIRED_TEMPLATE_VAR_PATTERN = re.compile(r"{{\s*(?P<var>[A-Za-z0-9_]+)\b")

# Shared layout that other templates `{% extends %}`. Not configurable: existing
# templates reference this name literally.
EMAIL_LAYOUT_TEMPLATE_NAME = "email-layout"

# Starting HTML for new templates.
EMAIL_LAYOUT_CHILD_HTML = f'{{% extends "{EMAIL_LAYOUT_TEMPLATE_NAME}" %}}{{% block content %}}\n\n{{% endblock %}}'

_EXTENDS_TAG_PATTERN = re.compile(r"{%-?\s*extends\b")
_CONTENT_BLOCK_TAG_PATTERN = re.compile(r"{%-?\s*block\s+content\s*-?%}")

# Keep <style> tags so @media rules (which cannot be inlined) still reach
# clients that support them.
_CSS_INLINER = css_inline.CSSInliner(load_remote_stylesheets=False, keep_style_tags=True)


def validate_email_subject(subject: str) -> str:
    if "\r" in subject or "\n" in subject:
        raise ValueError("Email subjects cannot contain line breaks.")
    return subject


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
            settings.ORGANIZATION_SPONSORSHIP_RENEWAL_APPROVED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_EXPIRED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_REQUEST_SUBMITTED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_REQUEST_APPROVED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_REQUEST_RENEWAL_APPROVED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_REQUEST_REJECTED_EMAIL_TEMPLATE_NAME,
            settings.ACCOUNT_DELETION_APPROVED_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_REQUEST_RFI_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_COMMITTEE_PENDING_REQUESTS_EMAIL_TEMPLATE_NAME,
            settings.MEMBERSHIP_COMMITTEE_EMBARGOED_MEMBERS_EMAIL_TEMPLATE_NAME,
            settings.FREEIPA_MEMBERSHIP_RECONCILE_ALERT_EMAIL_TEMPLATE_NAME,
            settings.SPONSORSHIP_INVOICE_PAYMENT_DETAILS_ALERT_EMAIL_TEMPLATE_NAME,
            settings.PASSWORD_RESET_EMAIL_TEMPLATE_NAME,
            settings.PASSWORD_RESET_SUCCESS_EMAIL_TEMPLATE_NAME,
            settings.ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME,
            settings.ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME,
            settings.REGISTRATION_EMAIL_TEMPLATE_NAME,
            settings.EMAIL_VALIDATION_EMAIL_TEMPLATE_NAME,
            settings.ACCOUNT_INVITE_EMAIL_TEMPLATE_NAME,
            settings.ORG_CLAIM_INVITATION_EMAIL_TEMPLATE_NAME,
            *settings.ACCOUNT_INVITATION_EMAIL_TEMPLATE_NAMES,
            EMAIL_LAYOUT_TEMPLATE_NAME,
        }
    )


def sendable_email_templates() -> QuerySet[EmailTemplate]:
    """Templates offered for sending; the layout only exists to be extended."""

    return EmailTemplate.objects.exclude(name=EMAIL_LAYOUT_TEMPLATE_NAME).order_by("name")


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
        if membership_type.acceptance_template.name:
            locked.add(membership_type.acceptance_template.name)

    return frozenset(locked)


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

    bucket = settings.AWS_STORAGE_BUCKET_NAME

    if "://" in value:
        parts = urlsplit(value)
        path = parts.path.lstrip("/")

        # Path-style URL: /<bucket>/<key>
        if bucket and path.startswith(f"{bucket}/"):
            return path[len(bucket) + 1 :]

        # Virtual-host style: <bucket>.<domain>/<key>
        if bucket and parts.netloc.startswith(f"{bucket}."):
            return path

        custom_domain = settings.AWS_S3_CUSTOM_DOMAIN
        if custom_domain:
            custom_parts = urlsplit(custom_domain) if "://" in custom_domain else urlsplit(f"https://{custom_domain}")
            if parts.netloc == custom_parts.netloc:
                custom_path = custom_parts.path.strip("/")
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


def stage_inline_images_for_sending(
    html_content: str,
    *,
    strict: bool = True,
    template_name: str = "unknown",
) -> tuple[str, list[str]]:
    """Rewrite inline_image arguments to local temp files for sending.

    Returns (rewritten_html, temp_paths). Callers must delete temp_paths.
    """

    staged: dict[str, str] = {}
    temp_paths: list[str] = []

    def repl(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        suffix = match.group("suffix")
        quote = match.group("q")
        raw_arg = (match.group("urlq") or match.group("urlb") or "").strip()
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
                logger.warning(
                    "Inline image missing in storage; sending without image",
                    extra={
                        "event": "astra.email.inline_image.missing",
                        "component": "email",
                        "outcome": "warning",
                        "template_name": template_name or "unknown",
                        "image_ref": raw_arg,
                        "correlation_id": "email.templated.queue",
                    },
                )
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

    rewritten = _INLINE_IMAGE_TAG_REWRITE_PATTERN.sub(repl, html_content)
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
    attachments: Mapping[str, object] | None = None,
    strict_inline_images: bool = False,
    commit: bool = True,
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

    email = queue_composed_email(
        recipients=recipients,
        sender=sender,
        subject_source=template.subject or "",
        text_source=template.content or "",
        html_source=template.html_content or "",
        context=context,
        cc=cc,
        bcc=bcc,
        reply_to=reply_to,
        attachments=attachments,
        strict_inline_images=strict_inline_images,
        template_name=template_name,
        commit=commit,
    )

    # Record the template name and context for de-duplication lookups. Not via
    # Email.template: post_office re-renders template+context at delivery, which
    # would discard this rendering (staged inline images, inlined CSS, system context).
    try:
        email.context = {**dict(context), "template_name": template_name}
        if commit:
            email.save(update_fields=["context"])
    except Exception:
        logger.exception(
            "Failed to persist post_office template/context metadata template=%s",
            template_name,
            extra=current_exception_log_fields(),
        )

    return email


def _store_inline_images(*, html_template: Template, rendered_html: str) -> None:
    """Store each `{% inline_image %}` once as a post_office Attachment.

    Content-IDs are hashes of the image bytes, so a stored row can be shared by
    every email that references it; bulk_save_emails links them by `cid:`.
    """

    # `_attached_images` is provided by the post_office template backend when
    # `{% inline_image %}` is used. We guard with `hasattr` because plain Django
    # templates do not expose this attribute.
    attached_images = html_template._attached_images if hasattr(html_template, "_attached_images") else []
    if not attached_images and hasattr(html_template, "attach_related"):
        probe = EmailMultiAlternatives(
            subject="",
            body="",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[settings.DEFAULT_FROM_EMAIL],
        )
        probe.attach_alternative(rendered_html or " ", "text/html")
        html_template.attach_related(probe)
        attached_images = [
            attachment
            for attachment in probe.attachments
            if hasattr(attachment, "get") and hasattr(attachment, "get_payload")
        ]
    for image in attached_images or []:
        cid = str(image.get("Content-ID") or "").strip().strip("<>")
        if not cid or Attachment.objects.filter(headers__contains={"Content-ID": f"<{cid}>"}).exists():
            continue
        content_type = str(image.get_content_type() or "application/octet-stream")

        filename = cid
        subtype = content_type.split("/", 1)[-1]
        if subtype and subtype.isalnum():
            filename = f"{cid}.{subtype}"

        create_attachments(
            {
                filename: {
                    # Keep the payload in its transfer encoding: post_office sends
                    # attachment bytes as-is under these headers, which include
                    # `Content-Transfer-Encoding: base64`.
                    "file": ContentFile(image.get_payload().encode("ascii")),
                    "mimetype": content_type,
                    "headers": {str(k): str(v) for k, v in image.items()},
                }
            }
        )


def queue_composed_email(
    *,
    recipients: list[str],
    sender: str,
    subject_source: str,
    text_source: str,
    html_source: str,
    context: Mapping[str, object],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to: list[str] | None = None,
    attachments: Mapping[str, object] | None = None,
    strict_inline_images: bool = False,
    template_name: str = "unknown",
    commit: bool = True,
) -> Email:
    """Queue operator/template-composed email through the SSOT pipeline.

    This helper is intentionally metadata-agnostic: callers that need
    `Email.context` persistence (e.g. for de-duplication) should set it after
    calling this helper. Never set `Email.template` as well: post_office would
    re-render the raw template at delivery instead of sending this rendering.

    Every email is saved and dispatched by bulk_save_emails, which also links
    its inline images. When *commit=False*, the Email is rendered but not
    saved, so callers can pass a batch to bulk_save_emails themselves; caller
    *attachments* are not supported then (pass shared Attachment rows to
    bulk_save_emails instead).

    *attachments* uses post_office's format ({filename: file or
    {"file": ..., "mimetype": ...}}).
    """

    staged_files: list[str] = []

    def stage_source(source: str) -> str:
        staged_source, paths = stage_inline_images_for_sending(
            source,
            strict=strict_inline_images,
            template_name=template_name,
        )
        staged_files.extend(paths)
        return staged_source

    # Parent templates (the email layout) are loaded mid-render, so they get
    # their inline images staged through the loader.
    source_transform_token = _parent_template_source_transform.set(stage_source)
    try:
        template_engine = engines["post_office"]
        text_template = template_engine.from_string(preview_drop_inline_image_tags(text_source))
        html_template = template_engine.from_string(stage_source(html_source))

        rendered_context = {
            **system_email_context(),
            **dict(context),
        }
        rendered_subject = validate_email_subject(
            render_template_string(subject_source, rendered_context, autoescape=False)
        )
        rendered_text = text_template.render(rendered_context)
        rendered_html = _inline_css(html_template.render(rendered_context))

        headers: dict[str, str] | None = None
        if reply_to:
            headers = {"Reply-To": ", ".join(reply_to)}

        # create() rather than send(): send() rejects commit=False with
        # priority='now' and would dispatch before inline images are linked.
        email = post_office.mail.create(
            sender=sender,
            recipients=recipients,
            cc=cc,
            bcc=bcc,
            subject=rendered_subject,
            message=rendered_text,
            html_message=rendered_html,
            headers=headers,
            render_on_delivery=False,
            commit=False,
        )
        # Email.save() would validate (e.g. recipient addresses); bulk_create does not.
        email.full_clean()
        _store_inline_images(html_template=html_template, rendered_html=rendered_html)

        if commit:
            bulk_save_emails([email], attachments=create_attachments(attachments) if attachments else ())
        return email
    finally:
        _parent_template_source_transform.reset(source_transform_token)
        for path in staged_files:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except Exception:
                logger.exception(
                    "Failed to delete temp inline image path=%s",
                    path,
                    extra=current_exception_log_fields(),
                )


def bulk_save_emails(
    emails: list[Email],
    batch_size: int = 500,
    attachments: Sequence[Attachment] = (),
) -> None:
    """Bulk-insert deferred Email objects and dispatch any with priority='now'.

    In dev (default priority='now'), emails are dispatched immediately after
    the bulk insert.  In production (priority='medium' / 'low'), emails remain
    queued for the ``send_queued_mail`` management command.

    *attachments* are shared post_office Attachment rows linked to every email.
    Each email is also linked to the inline images its HTML references (stored
    by queue_composed_email). Both happen before dispatch, so immediate sends
    include them.
    """
    if not emails:
        return
    Email.objects.bulk_create(emails, batch_size=batch_size)
    for attachment in attachments:
        attachment.emails.add(*emails)

    emails_by_cid: dict[str, list[Email]] = {}
    for email in emails:
        for cid in set(_INLINE_IMAGE_CID_PATTERN.findall(email.html_message or "")):
            emails_by_cid.setdefault(cid, []).append(email)
    for cid, cid_emails in emails_by_cid.items():
        inline_image = Attachment.objects.filter(headers__contains={"Content-ID": f"<{cid}>"}).first()
        if inline_image is not None:
            inline_image.emails.add(*cid_emails)

    for email in emails:
        # create() sets status=None for priority='now'; dispatch sends
        # the email immediately through the configured backend.
        if email.status is None:
            email.dispatch()


def _iter_pluralize_vars(*sources: str) -> Iterable[str]:
    for s in sources:
        for m in _PLURALIZE_PATTERN.finditer(s):
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
        for m in _VAR_PATTERN.finditer(s):
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

    return _INLINE_IMAGE_TAG_PATTERN.sub(repl, value)


def preview_drop_inline_image_tags(value: str) -> str:
    """Drop `{% inline_image '...' %}` entirely (useful for plain-text previews)."""

    return _INLINE_IMAGE_TAG_PATTERN.sub("", value)


# How EmailTemplateLoader rewrites `{% inline_image %}` in parent templates:
# URLs for previews by default, staged temp files while queue_composed_email sends.
_parent_template_source_transform: ContextVar[Callable[[str], str]] = ContextVar(
    "parent_template_source_transform",
    default=preview_rewrite_inline_image_tags_to_urls,
)


class EmailTemplateLoader(Loader):
    """Load EmailTemplate HTML by name so templates can `{% extends %}` each other."""

    @override
    def get_template_sources(self, template_name: str) -> Iterator[Origin]:
        yield Origin(name=template_name, template_name=template_name, loader=self)

    @override
    def get_contents(self, origin: Origin) -> str:
        template = EmailTemplate.objects.filter(name=origin.name, default_template__isnull=True).first()
        if template is None:
            raise TemplateDoesNotExist(origin)
        return _parent_template_source_transform.get()(template.html_content)


def _inline_css(html: str) -> str:
    # Only layouts carry stylesheets; inlining a fragment would wrap it in <html><body>.
    if "<style" not in html:
        return html
    return _CSS_INLINER.inline(html)


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


def render_template_string(value: str, context: Mapping[str, object], *, autoescape: bool = True) -> str:
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
            if not autoescape:
                return post_office_engine.engine.from_string(value or "").render(
                    Context(dict(context), autoescape=False)
                )
            return post_office_engine.from_string(value or "").render(dict(context))
        except TemplateSyntaxError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    try:
        return Template(value or "").render(Context(dict(context), autoescape=autoescape))
    except TemplateSyntaxError as exc:
        raise ValueError(str(exc)) from exc


def validate_email_template_sources(*, name: str, subject: str, html_content: str, text_content: str) -> None:
    """Reject template sources that would fail or mis-render when sent.

    Rendering (not just compiling) is needed: Django only resolves an
    `{% extends %}` parent at render time.
    """

    validate_email_subject(subject)
    if _EXTENDS_TAG_PATTERN.search(text_content):
        raise ValueError("The plain-text version cannot use {% extends %}; the email layout is HTML only.")
    if name == EMAIL_LAYOUT_TEMPLATE_NAME and not _CONTENT_BLOCK_TAG_PATTERN.search(html_content):
        raise ValueError(
            "The email layout must contain {% block content %}{% endblock %} where each email's content goes."
        )
    try:
        render_templated_email_preview(
            subject=subject,
            html_content=html_content,
            text_content=text_content,
            context={},
        )
    except ValueError as exc:
        raise ValueError(f"Template error: {exc}") from exc


def execute_email_template_save(
    *,
    template: EmailTemplate | None,
    raw_name: str | None,
    subject: str,
    html_content: str,
    text_content: str,
) -> EmailTemplate:
    validate_email_template_sources(
        name=template.name if template is not None else raw_name or "",
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )
    if template is not None:
        update_email_template(
            template=template,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )
        return template

    return create_email_template_unique(
        raw_name=raw_name or "",
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )


def render_templated_email_preview(*, subject: str, html_content: str, text_content: str, context: Mapping[str, object]) -> dict[str, str]:
    subject = validate_email_subject(subject)
    html_content = preview_rewrite_inline_image_tags_to_urls(html_content)
    text_content = preview_drop_inline_image_tags(text_content)
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
        "subject": render_template_string(subject, render_context, autoescape=False),
        "html": _inline_css(render_template_string(html_content, render_context)),
        "text": render_template_string(text_content, render_context),
    }


def render_templated_email_preview_response(*, request: HttpRequest, context: Mapping[str, object]) -> JsonResponse:
    """Render preview from request.POST and return a JSON response.

    This keeps view code focused on gathering context. Rendering and template
    error handling (syntax errors surfaced as 400s) is centralized here.
    """

    try:
        return JsonResponse(
            render_templated_email_preview(
                subject=str(request.POST.get("subject") or ""),
                html_content=str(request.POST.get("html_content") or ""),
                text_content=str(request.POST.get("text_content") or ""),
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
    name = unique_email_template_name(raw_name)
    return EmailTemplate.objects.create(
        name=name,
        subject=subject,
        html_content=html_content,
        content=text_content,
    )
