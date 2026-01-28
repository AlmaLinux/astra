from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from django import forms
from django.conf import settings
from valx import detect_hate_speech, detect_profanity, load_custom_profanity_from_file, load_profanity_words

from core.views_utils import _normalize_str

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_custom_profanity_words() -> list[str]:
    path = Path(settings.VALX_PROFANITY_WORDS_FILE)
    if not str(path).strip():
        return []
    try:
        return load_custom_profanity_from_file(str(path))
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
    detections = detect_profanity([value], language="All", custom_words_list=load_custom_profanity_words())
    return bool(detections)


@lru_cache(maxsize=1)
def _profanity_keywords() -> list[str]:
    words = load_profanity_words(language="All", custom_words_list=load_custom_profanity_words())
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
    outcome = _normalize_hate_speech_outcome(detect_hate_speech(value))
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