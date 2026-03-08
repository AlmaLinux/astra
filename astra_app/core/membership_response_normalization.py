from dataclasses import dataclass

ADDITIONAL_INFORMATION_QUESTION = "Additional information"
_LEGACY_ADDITIONAL_INFORMATION_QUESTION = "Additional info"
ADDITIONAL_INFORMATION_QUESTION_KEYS = frozenset(
    {
        ADDITIONAL_INFORMATION_QUESTION.casefold(),
        _LEGACY_ADDITIONAL_INFORMATION_QUESTION.casefold(),
    }
)
ADDITIONAL_INFORMATION_HEADER_ALIASES = (
    _LEGACY_ADDITIONAL_INFORMATION_QUESTION,
    "q_additional_info",
    "additional_info",
)


@dataclass(frozen=True, slots=True)
class NormalizedMembershipResponses:
    entries: tuple[tuple[str, str], ...]

    def as_responses(self) -> list[dict[str, str]]:
        return [{question_name: answer} for question_name, answer in self.entries]

    def as_response_map(self) -> dict[str, str]:
        return dict(self.entries)

    def get(self, question_name: str, default: str = "") -> str:
        wanted_question_name = canonicalize_membership_response_question(question_name)
        for entry_question_name, answer in self.entries:
            if entry_question_name == wanted_question_name:
                return answer
        return default

    def contains_question(self, question_name: str) -> bool:
        wanted_question_name = canonicalize_membership_response_question(question_name)
        return any(entry_question_name == wanted_question_name for entry_question_name, _ in self.entries)


def canonicalize_membership_response_question(question_name: object) -> str:
    normalized_question_name = str(question_name or "").strip()
    if normalized_question_name.casefold() in ADDITIONAL_INFORMATION_QUESTION_KEYS:
        return ADDITIONAL_INFORMATION_QUESTION
    return normalized_question_name


def normalize_membership_request_responses(
    *,
    responses: list[dict[str, str]] | None,
    is_mirror_membership: bool,
) -> NormalizedMembershipResponses:
    answers_by_question: dict[str, str] = {}
    question_order: list[str] = []
    canonical_additional_information = ""
    legacy_additional_information = ""

    def remember_answer(question_name: str, answer: str) -> None:
        if not question_name or not answer:
            return
        if question_name not in answers_by_question:
            question_order.append(question_name)
        answers_by_question[question_name] = answer

    for item in responses or []:
        if not isinstance(item, dict):
            continue
        for question_name, answer in item.items():
            raw_question_name = str(question_name or "").strip()
            canonical_question_name = canonicalize_membership_response_question(raw_question_name)
            normalized_answer = str(answer or "").strip()
            if not canonical_question_name:
                continue
            # Historical rows can contain both canonical and legacy clarification labels.
            # Resolve them once here so every consumer gets the same canonical answer.
            if canonical_question_name == ADDITIONAL_INFORMATION_QUESTION:
                if raw_question_name.casefold() == ADDITIONAL_INFORMATION_QUESTION.casefold():
                    canonical_additional_information = normalized_answer
                elif raw_question_name.casefold() in ADDITIONAL_INFORMATION_QUESTION_KEYS:
                    legacy_additional_information = normalized_answer
                continue
            remember_answer(canonical_question_name, normalized_answer)

    if canonical_additional_information:
        remember_answer(ADDITIONAL_INFORMATION_QUESTION, canonical_additional_information)
    elif legacy_additional_information:
        remember_answer(ADDITIONAL_INFORMATION_QUESTION, legacy_additional_information)

    return NormalizedMembershipResponses(
        entries=tuple((question_name, answers_by_question[question_name]) for question_name in question_order),
    )
