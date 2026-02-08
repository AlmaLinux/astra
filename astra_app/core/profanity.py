import logging
import re
import warnings
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from django import forms
from django.conf import settings

from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)


class _ValxApi(NamedTuple):
    detect_hate_speech: Callable[..., object]
    detect_profanity: Callable[..., object]
    load_custom_profanity_from_file: Callable[..., list[str]]
    load_profanity_words: Callable[..., list[str]]


@lru_cache(maxsize=1)
def _valx_api() -> _ValxApi:
    """Load ValX lazily.

    ValX imports a pickled sklearn model at import time. When the serialized
    model was produced with an older sklearn, sklearn emits
    InconsistentVersionWarning, which becomes noisy during management commands
    and tests (they import URL routes/forms).

    We intentionally silence that warning for this import, since the mismatch is
    outside Astra's control and does not change our runtime behavior.
    """

    from sklearn.exceptions import InconsistentVersionWarning

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=InconsistentVersionWarning)
        import valx

    return _ValxApi(
        detect_hate_speech=valx.detect_hate_speech,
        detect_profanity=valx.detect_profanity,
        load_custom_profanity_from_file=valx.load_custom_profanity_from_file,
        load_profanity_words=valx.load_profanity_words,
    )


@lru_cache(maxsize=1)
def load_custom_profanity_words() -> list[str]:
    path = Path(settings.VALX_PROFANITY_WORDS_FILE)
    if not str(path).strip():
        return []
    try:
        return _valx_api().load_custom_profanity_from_file(str(path))
    except FileNotFoundError:
        logger.warning("ValX custom profanity file missing: %s", path)
        return []
    except Exception:
        logger.exception("Failed to load ValX custom profanity file: %s", path)
        return []


def _normalize_hate_speech_outcome(outcome: object) -> list[str]:
    if isinstance(outcome, list):
        return outcome
    if isinstance(outcome, tuple):
        return list(outcome)
    try:
        return list(outcome)
    except TypeError:
        return [str(outcome)]


def _detects_profanity(value: str) -> bool:
    detections = _valx_api().detect_profanity(
        [value],
        language="All",
        custom_words_list=load_custom_profanity_words(),
    )
    return bool(detections)


@lru_cache(maxsize=1)
def _profanity_keywords() -> list[str]:
    words = _valx_api().load_profanity_words(language="All", custom_words_list=load_custom_profanity_words())
    return sorted({w.strip().lower() for w in words if w and w.strip()})


def _detects_profanity_in_identifier(value: str) -> bool:
    lowered = value.casefold()
    tokens = [t for t in re.split(r"[^\w]+", lowered) if t]
    if not tokens:
        return False

    compact = "".join(tokens)
    for word in _profanity_keywords():
        if not word:
            continue
        if len(word) < 4:
            if word in tokens or word == compact:
                return True
            continue
        for token in tokens:
            if word in token:
                return True
        if word in compact:
            return True
    return False


def _detects_hate_speech(value: str) -> bool:
    outcome = _normalize_hate_speech_outcome(_valx_api().detect_hate_speech(value))
    return "Hate Speech" in outcome or "Offensive Speech" in outcome


def validate_no_profanity_or_hate_speech(value: object, *, field_label: str) -> str:
    cleaned = _normalize_str(value)
    if not cleaned:
        return ""
    try:
        if _detects_profanity(cleaned) or _detects_profanity_in_identifier(cleaned) or _detects_hate_speech(cleaned):
            raise forms.ValidationError(f"{field_label} contains disallowed language")
    except forms.ValidationError:
        raise
    except Exception as exc:
        logger.exception("ValX validation failed for %s", field_label)
        raise forms.ValidationError(f"Unable to validate {field_label}. Please try again later.") from exc
    return cleaned