from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.files.storage import default_storage
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone

from core.permissions import ASTRA_ADD_SEND_MAIL

_MAIL_IMAGES_DIR = "mail-images"
_MAIL_IMAGES_PREFIX = f"{_MAIL_IMAGES_DIR}/"


@dataclass(frozen=True)
class MailImage:
    key: str
    relative_key: str
    url: str
    size_bytes: int
    modified_at: str


def _normalize_relative_path(raw: str) -> str:
    path = str(raw or "").strip().replace("\\", "/")
    path = path.strip("/")
    if not path:
        return ""

    normalized = posixpath.normpath(path)

    if normalized in {".", ".."}:
        return ""
    if normalized.startswith("../") or "/../" in normalized or normalized.endswith("/.."):
        raise ValueError("Invalid path")
    if normalized.startswith("/"):
        raise ValueError("Invalid path")

    return normalized


def _join_under_prefix(*parts: str) -> str:
    joined = posixpath.join(*[p for p in parts if p])
    if not joined.startswith(_MAIL_IMAGES_PREFIX):
        raise ValueError("Invalid key")
    return joined


def _iter_image_keys(*, base_dir: str) -> list[str]:
    keys: list[str] = []

    def walk(dir_path: str) -> None:
        subdirs, files = default_storage.listdir(dir_path)
        for name in files:
            keys.append(posixpath.join(dir_path, name))
        for subdir in subdirs:
            walk(posixpath.join(dir_path, subdir))

    walk(base_dir)
    return sorted(keys)


@permission_required(ASTRA_ADD_SEND_MAIL, login_url=reverse_lazy("users"))
def email_images(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip().lower()

        if action == "upload":
            raw_upload_path = str(request.POST.get("upload_path") or "")
            overwrite = str(request.POST.get("overwrite") or "").strip() == "1"

            try:
                upload_path = _normalize_relative_path(raw_upload_path)
            except ValueError:
                messages.error(request, "Upload path must not contain '..'.")
                return redirect("email-images")

            files = request.FILES.getlist("files")
            if not files:
                messages.error(request, "Choose one or more files to upload.")
                return redirect("email-images")

            uploaded = 0
            for f in files:
                filename = posixpath.basename(str(getattr(f, "name", "") or "").strip())
                if not filename:
                    continue

                content_type = str(getattr(f, "content_type", "") or "").strip().lower()
                if not content_type.startswith("image/"):
                    messages.error(request, f"{filename}: only image uploads are allowed.")
                    continue

                try:
                    key = _join_under_prefix(_MAIL_IMAGES_DIR, upload_path, filename)
                except ValueError:
                    messages.error(request, f"{filename}: invalid upload path.")
                    continue

                try:
                    key_exists = default_storage.exists(key)
                except Exception as e:
                    logging.exception(f"Failed to check if file exists for {filename}: {e}")
                    messages.error(request, f"{filename}: failed to check if file exists.")
                    continue

                if key_exists:
                    if not overwrite:
                        messages.error(request, f"{filename}: already exists (enable overwrite to replace it).")
                        continue
                    try:
                        default_storage.delete(key)
                    except Exception as e:
                        logging.exception(f"Failed to delete existing file {filename}: {e}")
                        messages.error(request, f"{filename}: failed to delete existing file.")
                        continue

                try:
                    default_storage.save(key, f)
                    uploaded += 1
                except Exception as e:
                    logging.exception(f"Failed to upload image {filename}: {e}")
                    messages.error(request, f"{filename}: failed to upload.")

            if uploaded:
                messages.success(request, f"Uploaded {uploaded} image{'s' if uploaded != 1 else ''}.")
            return redirect("email-images")

        if action == "delete":
            key = str(request.POST.get("key") or "").strip().lstrip("/")
            if not key.startswith(_MAIL_IMAGES_PREFIX):
                messages.error(request, "Invalid image key.")
                return redirect("email-images")

            default_storage.delete(key)
            messages.success(request, "Deleted image.")
            return redirect("email-images")

        messages.error(request, "Unknown action.")
        return redirect("email-images")

    images: list[MailImage] = []
    try:
        for key in _iter_image_keys(base_dir=_MAIL_IMAGES_DIR):
            relative_key = key.removeprefix(_MAIL_IMAGES_PREFIX)
            url = default_storage.url(key)
            size_bytes = int(default_storage.size(key))

            modified_dt = default_storage.get_modified_time(key)
            if timezone.is_naive(modified_dt):
                modified_dt = timezone.make_aware(modified_dt, timezone=timezone.utc)
            modified_at = timezone.localtime(modified_dt).strftime("%Y-%m-%d %H:%M:%S %Z")

            images.append(
                MailImage(
                    key=key,
                    relative_key=relative_key,
                    url=url,
                    size_bytes=size_bytes,
                    modified_at=modified_at,
                )
            )
    except Exception as e:
        logging.exception(f"Failed to list mail images: {e}")
        messages.error(request, "Unable to list mail images")

    return render(
        request,
        "core/mail_images.html",
        {
            "images": images,
            "mail_images_prefix": _MAIL_IMAGES_PREFIX,
            "mail_images_external_example_url": default_storage.url(f"{_MAIL_IMAGES_DIR}/path/to/image.png"),
        },
    )
