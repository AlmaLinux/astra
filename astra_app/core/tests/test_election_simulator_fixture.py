"""Election simulator fixture converted to a proper test case.

Originally a standalone dev script (`astra_app/election_simulator.py`) with
hardcoded data and no assertions. Now exercised by the test suite with
deterministic assertions on Meek STV output.
"""

import uuid

from django.test import SimpleTestCase

from core.elections_meek import tally_meek


class ElectionSimulatorFixtureTest(SimpleTestCase):
    """Exercise the Meek STV tally with a small hand-crafted scenario.

    Candidates A(10), B(11), C(12), D(13) — 4 seats, 1 exclusion group
    constraining at most 1 of {A, D}. Ballots are deterministic (no randomness).
    """

    def setUp(self) -> None:
        self.candidates = [
            {"id": 10, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000010")},
            {"id": 11, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000011")},
            {"id": 12, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000012")},
            {"id": 13, "name": "D", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000013")},
        ]
        self.ballots = [
            {"weight": 1, "ranking": [10, 12]},
            {"weight": 1, "ranking": [11, 10]},
            {"weight": 1, "ranking": [12, 11]},
            {"weight": 1, "ranking": [11, 12, 10]},
            {"weight": 5, "ranking": [11, 10]},
            {"weight": 2, "ranking": [12, 11, 10]},
            {"weight": 5, "ranking": [10, 12, 11]},
        ]
        self.exclusion_groups = [
            {"public_id": 1, "name": "Incompatibles", "max_elected": 1, "candidate_ids": [10, 13]},
        ]

    def test_tally_produces_valid_result(self) -> None:
        result = tally_meek(
            seats=4,
            ballots=self.ballots,
            candidates=self.candidates,
            exclusion_groups=self.exclusion_groups,
        )

        self.assertIn("elected", result)
        self.assertIn("eliminated", result)
        self.assertIn("quota", result)
        self.assertIn("rounds", result)
        self.assertIsInstance(result["rounds"], list)
        self.assertGreater(len(result["rounds"]), 0, "Tally must produce at least one round")

    def test_elected_candidates_within_seat_limit(self) -> None:
        result = tally_meek(
            seats=4,
            ballots=self.ballots,
            candidates=self.candidates,
            exclusion_groups=self.exclusion_groups,
        )

        self.assertLessEqual(len(result["elected"]), 4)

    def test_exclusion_group_constraint_respected(self) -> None:
        """At most 1 of {A(10), D(13)} may be elected."""
        result = tally_meek(
            seats=4,
            ballots=self.ballots,
            candidates=self.candidates,
            exclusion_groups=self.exclusion_groups,
        )

        constrained_ids = {10, 13}
        elected_in_group = [cid for cid in result["elected"] if cid in constrained_ids]
        self.assertLessEqual(
            len(elected_in_group), 1,
            f"Exclusion group allows max 1, but {len(elected_in_group)} elected: {elected_in_group}",
        )

    def test_round_data_contains_required_fields(self) -> None:
        result = tally_meek(
            seats=4,
            ballots=self.ballots,
            candidates=self.candidates,
            exclusion_groups=self.exclusion_groups,
        )

        required_fields = {
            "iteration", "elected", "eliminated", "forced_exclusions",
            "tie_breaks", "eligible_candidates", "retention_factors",
            "retained_totals", "numerically_converged", "max_retention_delta",
            "seats", "elected_total", "count_complete", "audit_text", "summary_text",
        }
        for i, round_data in enumerate(result["rounds"]):
            missing = required_fields - set(round_data.keys())
            self.assertFalse(missing, f"Round {i + 1} missing fields: {missing}")

    def test_no_exclusion_groups(self) -> None:
        """Tally works with no exclusion groups."""
        result = tally_meek(
            seats=4,
            ballots=self.ballots,
            candidates=self.candidates,
            exclusion_groups=[],
        )

        self.assertIn("elected", result)
        self.assertLessEqual(len(result["elected"]), 4)

    def test_deterministic_output(self) -> None:
        """Same inputs always produce the same tally output."""
        result1 = tally_meek(
            seats=4,
            ballots=self.ballots,
            candidates=self.candidates,
            exclusion_groups=self.exclusion_groups,
        )
        result2 = tally_meek(
            seats=4,
            ballots=self.ballots,
            candidates=self.candidates,
            exclusion_groups=self.exclusion_groups,
        )

        self.assertEqual(result1["elected"], result2["elected"])
        self.assertEqual(result1["eliminated"], result2["eliminated"])
        self.assertEqual(len(result1["rounds"]), len(result2["rounds"]))
