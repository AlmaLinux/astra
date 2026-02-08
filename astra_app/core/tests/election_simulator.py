import random
import uuid

from core.elections_meek import tally_meek

candidates = [
    {"id": 10, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000010")},
    {"id": 11, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000011")},
    {"id": 12, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000012")},
    {"id": 13, "name": "D", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000013")},
]

def id_to_candidate(candidate_id: int) -> str:
    for candidate in candidates:
        if candidate["id"] == candidate_id:
            name = str(candidate.get("name") or "").strip()
            return name if name else f"Unknown ({candidate_id})"
    return f"Unknown ({candidate_id})"

def pick(n = 1) -> list[int]:
    # sort the candidates randomly, then pick the first n
    shuffled = candidates.copy()
    random.shuffle(shuffled)
    return [c["id"] for c in shuffled[:n]]


ballots = [
    # {"weight": 1, "ranking": pick(2)},
    # {"weight": 1, "ranking": pick(2)},
    # {"weight": 1, "ranking": pick(4)},
    # {"weight": 1, "ranking": pick(2)},
    # {"weight": 1, "ranking": pick(4)},
    # {"weight": random.randint(1,3), "ranking": pick(4)},
    # {"weight": random.randint(1,5), "ranking": pick(2)},
    # {"weight": random.randint(1,5), "ranking": pick(4)},
    {"weight": 1, "ranking": [10, 12]},
    {"weight": 1, "ranking": [11, 10]},
    {"weight": 1, "ranking": [12, 11]},
    {"weight": 1, "ranking": [11, 12, 10]},
    {"weight": 5, "ranking": [11, 10]},
    {"weight": 2, "ranking": [12, 11, 10]},
    {"weight": 5, "ranking": [10, 12, 11]},
]



if 1 == 1:
    exclusions = [
        {"public_id": 1, "name": "Incompatibles", "max_elected": 1, "candidate_ids": [10, 13]}, #pick(2)},
    ]
else:
    exclusions = []

result = tally_meek(seats=4, ballots=ballots, candidates=candidates, exclusion_groups=exclusions)
print("Rounds detail:")
for round_detail in result["rounds"]:
    data = round_detail.copy()
    data.pop("audit_text", None)
    data.pop("summary_text", None)
    print(data)
    print()
    # print(round_detail['summary_text'])
    print(round_detail['audit_text'])
    print()
    # break

print(f"Elected: {[id_to_candidate(cid) for cid in result['elected']]}")
# print(f"Eliminated: {[id_to_candidate(cid) for cid in result['eliminated']]}")
# print(f"Force Excluded: {[id_to_candidate(cid) for cid in result['forced_excluded']]}")
print(f"Quota: {result['quota']:.2f}, Rounds: {len(result['rounds'])}")
