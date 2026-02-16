import csv
import datetime
import io

from django.core.files.uploadedfile import UploadedFile

from core.views_utils import _normalize_str


def norm_csv_header(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def normalize_csv_email(value: object) -> str:
    return _normalize_str(value).lower()


def normalize_csv_name(value: object) -> str:
    raw = _normalize_str(value).lower()
    return "".join(ch for ch in raw if ch.isalnum())


def parse_csv_bool(value: object) -> bool:
    normalized = _normalize_str(value).lower()
    if not normalized:
        return False
    return normalized in {"1", "y", "yes", "true", "t", "active", "activemember", "active member"}


def parse_csv_date(value: object) -> datetime.datetime | None:
    raw = _normalize_str(value)
    if not raw:
        return None

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            day = datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
        return datetime.datetime.combine(day, datetime.time(0, 0, 0), tzinfo=datetime.UTC)

    try:
        day = datetime.date.fromisoformat(raw)
    except ValueError:
        return None

    return datetime.datetime.combine(day, datetime.time(0, 0, 0), tzinfo=datetime.UTC)


def extract_csv_headers_from_uploaded_file(uploaded: UploadedFile) -> list[str]:
    uploaded.seek(0)
    sample = uploaded.read(64 * 1024)
    uploaded.seek(0)

    try:
        text = sample.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = sample.decode("utf-8", errors="replace")

    if not text.strip():
        return []

    try:
        dialect = csv.Sniffer().sniff(text, delimiters=",;\t|")
    except Exception:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    headers = next(reader, [])
    return [h.strip() for h in headers if str(h).strip()]
