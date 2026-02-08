
import os
import random
import time
import uuid
from decimal import Decimal

from django.test import SimpleTestCase

from core.elections_meek import tally_meek


class MeekRandomizedPerformanceBenchmarks(SimpleTestCase):
    """Micro-benchmarks for Meek STV tallying.

    These are skipped by default because they can add noticeable runtime to CI.

    Run explicitly with:
      `RUN_MEEK_BENCHMARKS=1 podman-compose exec -T web python manage.py test core.tests.test_elections_meek_benchmarks`
    """

    weight_choices: tuple[int, ...] = (1, 2, 5, 6, 7, 15, 16, 17, 50, 51, 52)

    def _candidates_5(self) -> list[dict[str, object]]:
        return [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000002")},
            {"id": 3, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000003")},
            {"id": 4, "name": "D", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000004")},
            {"id": 5, "name": "E", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000005")},
        ]

    def _random_ballots(
        self,
        *,
        rng: random.Random,
        n: int,
        candidate_ids: list[int],
        ranking_mode: str,
    ) -> list[dict[str, object]]:
        ballots: list[dict[str, object]] = []
        for _ in range(n):
            if ranking_mode == "full":
                ranking = candidate_ids[:]
                rng.shuffle(ranking)
            elif ranking_mode == "partial":
                # Simulate real-world voters who often rank only a subset.
                ranking = candidate_ids[:]
                rng.shuffle(ranking)
                ranking = ranking[: rng.randint(1, len(ranking))]
            else:
                raise ValueError(f"Unknown ranking_mode: {ranking_mode}")

            ballots.append(
                {
                    "weight": rng.choice(self.weight_choices),
                    "ranking": ranking,
                }
            )
        return ballots

    def _parse_int_list_env(self, *, name: str, default: list[int]) -> list[int]:
        raw = str(os.environ.get(name) or "").strip()
        if not raw:
            return default

        values: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            values.append(int(part))
        return values or default

    def _parse_str_list_env(self, *, name: str, default: list[str]) -> list[str]:
        raw = str(os.environ.get(name) or "").strip()
        if not raw:
            return default
        values = [p.strip() for p in raw.split(",") if p.strip()]
        return values or default

    def test_meek_randomized_5_candidates_smoke(self) -> None:
        """Always-on smoke test so the generator stays sane."""

        candidates = self._candidates_5()
        candidate_ids = [int(c["id"]) for c in candidates]
        ballots = self._random_ballots(
            rng=random.Random(20251231),
            n=100,
            candidate_ids=candidate_ids,
            ranking_mode="partial",
        )

        result = tally_meek(ballots=ballots, candidates=candidates, seats=2)

        self.assertIsInstance(result.get("quota"), Decimal)
        elected = list(result.get("elected") or [])
        self.assertEqual(len(elected), 2)
        self.assertEqual(len(set(elected)), 2)
        self.assertTrue(set(elected).issubset(set(candidate_ids)))

        rounds = list(result.get("rounds") or [])
        self.assertGreaterEqual(len(rounds), 1)
        self.assertTrue(all(isinstance(r, dict) for r in rounds))
        self.assertTrue(all("audit_text" in r and "summary_text" in r for r in rounds if isinstance(r, dict)))

    def test_meek_tally_timings_randomized_weighted_ballots(self) -> None:
        if os.environ.get("RUN_MEEK_BENCHMARKS") not in {"1", "true", "TRUE", "yes", "YES"}:
            self.skipTest("Set RUN_MEEK_BENCHMARKS=1 to run Meek performance timings")

        candidates = self._candidates_5()
        candidate_ids = [int(c["id"]) for c in candidates]

        # Defaults keep runtime reasonable, but you can expand coverage via env vars:
        # - MEEK_BENCH_SEATS="1,2,3"
        # - MEEK_BENCH_RANKINGS="full,partial"
        seat_counts = self._parse_int_list_env(name="MEEK_BENCH_SEATS", default=[2])
        ranking_modes = self._parse_str_list_env(name="MEEK_BENCH_RANKINGS", default=["full"])

        sizes = [100, 500, 1000, 5000, 10_000]

        # Note: no assertions on absolute speed to avoid flaky failures.
        # Print a compact table to help humans compare runs.
        print("Meek STV timings (5 candidates, randomized rankings, randomized weights)")
        print(f"- sizes={sizes}")
        print(f"- seats={seat_counts}")
        print(f"- ranking_modes={ranking_modes}")

        for seats in seat_counts:
            if seats <= 0:
                raise ValueError("seats must be positive")
            expected_elected = min(seats, len(candidate_ids))

            for ranking_mode in ranking_modes:
                timings_ms: list[tuple[int, float]] = []
                for n in sizes:
                    rng = random.Random(20251231 + (seats * 1_000_000) + n)
                    ballots = self._random_ballots(
                        rng=rng,
                        n=n,
                        candidate_ids=candidate_ids,
                        ranking_mode=ranking_mode,
                    )

                    start = time.perf_counter()
                    result = tally_meek(ballots=ballots, candidates=candidates, seats=seats)
                    elapsed_ms = (time.perf_counter() - start) * 1000.0

                    elected = list(result.get("elected") or [])
                    self.assertEqual(len(elected), expected_elected)
                    self.assertEqual(len(set(elected)), expected_elected)

                    timings_ms.append((n, elapsed_ms))

                print(f"\nScenario: seats={seats} ranking_mode={ranking_mode}")
                for n, ms in timings_ms:
                    per_ballot_us = (ms * 1000.0 / float(n)) if n else 0.0
                    print(f"- ballots={n:>5} time_ms={ms:>9.2f}  per_ballot_us={per_ballot_us:>8.2f}")
