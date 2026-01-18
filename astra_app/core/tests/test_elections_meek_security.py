from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import TestCase


class MeekSTVSecurityTests(TestCase):
    """Test suite for security validations and DoS protections in Meek STV."""

    def test_missing_candidate_id_raises_error(self) -> None:
        """Candidate without 'id' field should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"name": "Alice"}],  # Missing 'id'
                seats=1,
            )
        self.assertIn("missing required 'id' field", str(ctx.exception))

    def test_invalid_candidate_id_type_raises_error(self) -> None:
        """Candidate with non-numeric id should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": "not_a_number", "name": "Bob", "tiebreak_uuid": "abc"}],
                seats=1,
            )
        self.assertIn("invalid candidate id", str(ctx.exception))

    def test_missing_tiebreak_uuid_raises_error(self) -> None:
        """Candidate without tiebreak_uuid should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": 1, "name": "Charlie"}],  # Missing tiebreak_uuid
                seats=1,
            )
        self.assertIn("invalid or missing tiebreak_uuid", str(ctx.exception))

    def test_empty_tiebreak_uuid_raises_error(self) -> None:
        """Candidate with empty tiebreak_uuid should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": 1, "name": "Dave", "tiebreak_uuid": ""}],
                seats=1,
            )
        self.assertIn("invalid or missing tiebreak_uuid", str(ctx.exception))

    def test_excessive_ballot_count_raises_error(self) -> None:
        """More than 1 million ballots should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[{"ranking": [], "weight": 1}] * 1_000_001,
                candidates=[{"id": 1, "name": "Eve", "tiebreak_uuid": str(uuid.uuid4())}],
                seats=1,
            )
        self.assertIn("ballot count must not exceed 1,000,000", str(ctx.exception))

    def test_excessive_candidate_count_raises_error(self) -> None:
        """More than 10,000 candidates should raise ValueError."""
        from core.elections_meek import tally_meek

        candidates = [
            {"id": i, "name": f"Candidate{i}", "tiebreak_uuid": str(uuid.uuid4())}
            for i in range(10_001)
        ]

        with self.assertRaises(ValueError) as ctx:
            tally_meek(ballots=[], candidates=candidates, seats=1)
        self.assertIn("candidate count must not exceed 10,000", str(ctx.exception))

    def test_excessive_seats_raises_error(self) -> None:
        """More than 10,000 seats should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": 1, "name": "Frank", "tiebreak_uuid": str(uuid.uuid4())}],
                seats=10_001,
            )
        self.assertIn("seats must not exceed 10,000", str(ctx.exception))

    def test_zero_or_negative_seats_raises_error(self) -> None:
        """Zero or negative seats should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": 1, "name": "Grace", "tiebreak_uuid": str(uuid.uuid4())}],
                seats=0,
            )
        self.assertIn("seats must be positive", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": 1, "name": "Grace", "tiebreak_uuid": str(uuid.uuid4())}],
                seats=-5,
            )
        self.assertIn("seats must be positive", str(ctx.exception))

    def test_invalid_epsilon_raises_error(self) -> None:
        """Non-positive epsilon should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": 1, "name": "Hank", "tiebreak_uuid": str(uuid.uuid4())}],
                seats=1,
                epsilon=Decimal(0),
            )
        self.assertIn("epsilon must be positive", str(ctx.exception))

    def test_invalid_max_iterations_raises_error(self) -> None:
        """max_iterations outside valid range should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": 1, "name": "Ivy", "tiebreak_uuid": str(uuid.uuid4())}],
                seats=1,
                max_iterations=0,
            )
        self.assertIn("max_iterations must be between 1 and 1000", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": 1, "name": "Ivy", "tiebreak_uuid": str(uuid.uuid4())}],
                seats=1,
                max_iterations=1001,
            )
        self.assertIn("max_iterations must be between 1 and 1000", str(ctx.exception))

    def test_malformed_ballot_ranking_does_not_crash(self) -> None:
        """Ballots with invalid ranking values should be handled gracefully."""
        from core.elections_meek import tally_meek

        # Mix of valid and invalid ballot data
        ballots = [
            {"ranking": [1], "weight": 1},
            {"ranking": ["invalid", 2], "weight": 1},  # Invalid ID
            {"ranking": [999_999_999], "weight": 1},  # Huge ID (out of range)
            {"ranking": [2.5], "weight": 1},  # Float instead of int
            {"ranking": None, "weight": 1},  # None ranking
            {"weight": 1},  # Missing ranking
        ]

        candidates = [
            {"id": 1, "name": "Alice", "tiebreak_uuid": str(uuid.uuid4())},
            {"id": 2, "name": "Bob", "tiebreak_uuid": str(uuid.uuid4())},
        ]

        # Should not crash, should elect someone
        result = tally_meek(ballots=ballots, candidates=candidates, seats=1)
        self.assertEqual(len(result["elected"]), 1)
        self.assertIn(result["elected"][0], [1, 2])

    def test_negative_ballot_weight_raises_error(self) -> None:
        """Ballots with negative weights should be rejected."""
        from core.elections_meek import tally_meek

        ballots = [
            {"ranking": [1], "weight": 1},
            {"ranking": [2], "weight": -5},  # Negative weight
        ]

        candidates = [
            {"id": 1, "name": "Alice", "tiebreak_uuid": str(uuid.uuid4())},
            {"id": 2, "name": "Bob", "tiebreak_uuid": str(uuid.uuid4())},
        ]

        with self.assertRaises(ValueError) as ctx:
            tally_meek(ballots=ballots, candidates=candidates, seats=1)
        self.assertIn("ballot weight", str(ctx.exception))

    def test_excessive_ballot_weight_raises_error(self) -> None:
        """Ballots with excessive weights should be rejected."""
        from core.elections_meek import tally_meek

        ballots = [
            {"ranking": [1], "weight": 1},
            {"ranking": [2], "weight": 1_000_001},  # Excessive weight
        ]

        candidates = [
            {"id": 1, "name": "Alice", "tiebreak_uuid": str(uuid.uuid4())},
            {"id": 2, "name": "Bob", "tiebreak_uuid": str(uuid.uuid4())},
        ]

        with self.assertRaises(ValueError) as ctx:
            tally_meek(ballots=ballots, candidates=candidates, seats=1)
        self.assertIn("ballot weight", str(ctx.exception))

    def test_out_of_range_candidate_id_in_ballot_ignored(self) -> None:
        """Candidate IDs outside valid range in ballots should be ignored."""
        from core.elections_meek import tally_meek

        ballots = [
            {"ranking": [1, 1_000_001, -5, 2], "weight": 1},  # Mix of valid and invalid IDs
        ]

        candidates = [
            {"id": 1, "name": "Alice", "tiebreak_uuid": str(uuid.uuid4())},
            {"id": 2, "name": "Bob", "tiebreak_uuid": str(uuid.uuid4())},
        ]

        # Should process only valid IDs (1 and 2)
        result = tally_meek(ballots=ballots, candidates=candidates, seats=1)
        self.assertEqual(len(result["elected"]), 1)
        self.assertIn(result["elected"][0], [1, 2])

    def test_malformed_exclusion_group_does_not_crash(self) -> None:
        """Exclusion groups with invalid data should be handled gracefully."""
        from core.elections_meek import tally_meek

        candidates = [
            {"id": 1, "name": "Alice", "tiebreak_uuid": str(uuid.uuid4())},
            {"id": 2, "name": "Bob", "tiebreak_uuid": str(uuid.uuid4())},
        ]

        ballots = [
            {"ranking": [1, 2], "weight": 1},
        ]

        # Invalid exclusion group data
        exclusion_groups = [
            {"public_id": "g1", "name": "Group1", "max_elected": "not_a_number", "candidate_ids": [1]},
        ]

        with self.assertRaises(ValueError):
            tally_meek(
                ballots=ballots,
                candidates=candidates,
                seats=1,
                exclusion_groups=exclusion_groups,
            )

    def test_exclusion_group_max_elected_exceeds_seats_raises_error(self) -> None:
        """Exclusion group max_elected exceeding total seats should raise ValueError."""
        from core.elections_meek import tally_meek

        candidates = [
            {"id": 1, "name": "Alice", "tiebreak_uuid": str(uuid.uuid4())},
            {"id": 2, "name": "Bob", "tiebreak_uuid": str(uuid.uuid4())},
        ]

        ballots = [{"ranking": [1, 2], "weight": 1}]

        exclusion_groups = [
            {"public_id": "g1", "name": "Group1", "max_elected": 10, "candidate_ids": [1, 2]},
        ]

        with self.assertRaises(ValueError) as ctx:
            tally_meek(ballots=ballots, candidates=candidates, seats=2, exclusion_groups=exclusion_groups)
        self.assertIn("out of valid range", str(ctx.exception))

    def test_no_candidates_raises_error(self) -> None:
        """Empty candidates list should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(ballots=[], candidates=[], seats=1)
        self.assertIn("must have at least one candidate", str(ctx.exception))

    def test_candidate_id_out_of_range_raises_error(self) -> None:
        """Candidate ID outside valid range should raise ValueError."""
        from core.elections_meek import tally_meek

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": 1_000_001, "name": "Alice", "tiebreak_uuid": str(uuid.uuid4())}],
                seats=1,
            )
        self.assertIn("out of valid range", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            tally_meek(
                ballots=[],
                candidates=[{"id": -1, "name": "Bob", "tiebreak_uuid": str(uuid.uuid4())}],
                seats=1,
            )
        self.assertIn("out of valid range", str(ctx.exception))

    def test_valid_input_executes_successfully(self) -> None:
        """Valid input with all security checks should execute successfully."""
        from core.elections_meek import tally_meek

        candidates = [
            {"id": 1, "name": "Alice", "tiebreak_uuid": str(uuid.uuid4())},
            {"id": 2, "name": "Bob", "tiebreak_uuid": str(uuid.uuid4())},
            {"id": 3, "name": "Charlie", "tiebreak_uuid": str(uuid.uuid4())},
        ]

        ballots = [
            {"ranking": [1, 2, 3], "weight": 5},
            {"ranking": [2, 1, 3], "weight": 3},
            {"ranking": [3, 2, 1], "weight": 2},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=2)

        self.assertEqual(len(result["elected"]), 2)
        self.assertIsInstance(result["quota"], Decimal)
        self.assertIsInstance(result["rounds"], list)
        self.assertGreater(len(result["rounds"]), 0)
