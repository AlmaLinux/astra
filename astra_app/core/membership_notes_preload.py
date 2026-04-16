from core.models import Note


def build_notes_by_membership_request_id(membership_request_ids: list[int]) -> dict[int, list[Note]]:
    request_ids = sorted({request_id for request_id in membership_request_ids if request_id})
    if not request_ids:
        return {}

    notes_by_request_id: dict[int, list[Note]] = {request_id: [] for request_id in request_ids}
    notes = Note.objects.filter(membership_request_id__in=request_ids).order_by("membership_request_id", "timestamp", "pk")
    for note in notes:
        membership_request_id = int(note.membership_request_id)
        notes_by_request_id.setdefault(membership_request_id, []).append(note)
    return notes_by_request_id
