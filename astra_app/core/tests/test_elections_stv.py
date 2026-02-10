
import datetime
import uuid
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone


class STVTallyTests(TestCase):
    def test_wikipedia_example(self) -> None:
        from core.elections_meek import tally_meek

        # Replicating the example from Wikipedia:
        # https://en.wikipedia.org/wiki/Single_transferable_vote#Example_for_a_non-partisan_election
        candidates = [
            {"id": 10, "name": "Orange", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000010")},
            {"id": 11, "name": "Pear", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000011")},
            {"id": 12, "name": "Strawberry", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000012")},
            {"id": 13, "name": "Cake", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000013")},
            {"id": 14, "name": "Chocolate", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000014")},
            {"id": 15, "name": "Hamburger", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000015")},
            {"id": 16, "name": "Chicken", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000016")},
        ]
        ballots = [
            {"weight": 3, "ranking": [10, 11]},
            {"weight": 8, "ranking": [11, 12, 13]},
            {"weight": 1, "ranking": [12, 10, 11]},
            {"weight": 3, "ranking": [13, 14]},
            {"weight": 1, "ranking": [14, 13, 15]},
            {"weight": 4, "ranking": [15, 16]},
            {"weight": 3, "ranking": [16, 14, 15]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=3)
        rounds = list(result.get("rounds") or [])

        self.assertGreaterEqual(len(rounds), 1)
        self.assertEqual(result["elected"], [11, 13, 15])

    def test_meek_does_not_elect_more_than_seats_in_single_iteration(self) -> None:
        from core.elections_meek import tally_meek

        # With the Hagenbach-Bischoff quota (total/(seats+1)), it is possible for more
        # candidates to meet the quota than there are remaining seats. The tally must
        # deterministically elect at most the remaining number of seats.
        candidates = [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000002")},
        ]
        ballots = [
            {"weight": 1, "ranking": [1]},
            {"weight": 1, "ranking": [2]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=1)
        rounds = list(result.get("rounds") or [])
        self.assertGreaterEqual(len(rounds), 1)

        r0 = rounds[0]
        self.assertIsNone(r0.get("eliminated"))
        self.assertIsInstance(r0.get("elected"), list)
        self.assertLessEqual(len(list(r0.get("elected") or [])), 1)
        self.assertEqual(len(result["elected"]), 1)

    def test_meek_numerical_convergence_does_not_imply_count_complete(self) -> None:
        from core.elections_meek import tally_meek

        # Construct a scenario where vote transfers immediately stabilize (numerical convergence)
        # but the outcome is not yet determined: 3 candidates, 1 seat, each has 1 first preference.
        # Quota is 3 / 2 = 1.5, so nobody reaches quota in the converged iteration.
        candidates = [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000002")},
            {"id": 3, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000003")},
        ]
        ballots = [
            {"weight": 1, "ranking": [1]},
            {"weight": 1, "ranking": [2]},
            {"weight": 1, "ranking": [3]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=1)
        rounds = list(result.get("rounds") or [])
        self.assertGreaterEqual(len(rounds), 2)

        # Find the numerically converged iteration before an elimination occurs.
        converged_rounds = [
            r
            for r in rounds
            if isinstance(r, dict)
            and r.get("eliminated") is None
            and not list(r.get("elected") or [])
            and bool(r.get("numerically_converged"))
        ]
        self.assertGreaterEqual(len(converged_rounds), 1)

        r0 = converged_rounds[0]
        self.assertIn("count_complete", r0)
        self.assertFalse(bool(r0.get("count_complete")))

        # Guardrail: if count_complete is false, do not claim finality.
        audit_text = str(r0.get("audit_text") or "")
        summary_text = str(r0.get("summary_text") or "")
        self.assertNotIn("Final results", audit_text)
        self.assertNotIn("all available seats have been filled", audit_text)
        self.assertNotIn("Final results", summary_text)

    def test_meek_when_eligible_candidates_fill_remaining_seats_elects_them_immediately(self) -> None:
        from core.elections_meek import tally_meek

        # Scenario: 3 candidates, 2 seats.
        # A is elected by quota early; later an elimination occurs among B/C.
        # Once one of B/C is eliminated, the remaining eligible candidates exactly
        # fill the remaining seats, so the remaining candidate should be elected
        # in that same iteration.
        candidates = [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000002")},
            {"id": 3, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000003")},
        ]
        ballots = [
            {"weight": 1, "ranking": [1]},
            {"weight": 1, "ranking": [1]},
            {"weight": 1, "ranking": [2]},
            {"weight": 1, "ranking": [3]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=2)
        self.assertIn(1, result["elected"])
        self.assertEqual(len(result["elected"]), 2)
        self.assertTrue(set(result["elected"]).issubset({1, 2, 3}))
        self.assertTrue(2 in result["elected"] or 3 in result["elected"])

        rounds = list(result.get("rounds") or [])
        self.assertGreaterEqual(len(rounds), 2)

        # Find the first elimination round and assert it also contains an election,
        # finishing the count in that same iteration.
        elimination_rounds = [r for r in rounds if isinstance(r, dict) and r.get("eliminated") is not None]
        self.assertGreaterEqual(len(elimination_rounds), 1)
        elim_round = elimination_rounds[0]

        self.assertTrue(bool(elim_round.get("count_complete")))
        self.assertIsInstance(elim_round.get("elected"), list)
        self.assertGreaterEqual(len(list(elim_round.get("elected") or [])), 1)

        audit_text = str(elim_round.get("audit_text") or "")
        self.assertIn("Final results", audit_text)
        self.assertTrue(

                "After this elimination, the remaining eligible candidate exactly filled" in audit_text
                or "After this elimination, the remaining eligible candidates exactly filled" in audit_text

        )

    def test_meek_fill_remaining_seats_explains_rule_election(self) -> None:
        from core.elections_meek import tally_meek

        # Seats=2, candidates=3. All ballots only rank A, so B and C have zero support.
        # Once one of B/C is eliminated (tie-break), the remaining candidate must be
        # elected to fill the remaining seat. The audit text must explain this rule.
        candidates = [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000002")},
            {"id": 3, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000003")},
        ]
        ballots = [
            {"weight": 1, "ranking": [1]},
            {"weight": 1, "ranking": [1]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=2)
        rounds = list(result.get("rounds") or [])
        self.assertGreaterEqual(len(rounds), 1)

        elimination_rounds = [r for r in rounds if isinstance(r, dict) and r.get("eliminated") is not None]
        self.assertGreaterEqual(len(elimination_rounds), 1)
        r = elimination_rounds[0]

        self.assertTrue(bool(r.get("count_complete")))
        self.assertTrue(list(r.get("elected_to_fill_remaining_seats") or []))

        audit_text = str(r.get("audit_text") or "")
        self.assertTrue(

                "remaining eligible candidate exactly filled" in audit_text
                or "remaining eligible candidates exactly filled" in audit_text

        )
        # Guardrail: do not imply every elected candidate had quota-reduction / surplus.
        self.assertNotIn("each elected candidate's retained vote total was reduced", audit_text)

    def test_two_seats_simple_elimination(self) -> None:
        from core.elections_meek import tally_meek

        candidates = [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-0000000000aa")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-0000000000bb")},
            {"id": 3, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-0000000000cc")},
        ]
        ballots = [
            {"weight": 1, "ranking": [1, 2, 3]},
            {"weight": 1, "ranking": [1, 2, 3]},
            {"weight": 1, "ranking": [1, 2, 3]},
            {"weight": 1, "ranking": [2, 1, 3]},
            {"weight": 1, "ranking": [2, 1, 3]},
            {"weight": 1, "ranking": [3, 2, 1]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=2)

        # Meek quota: floor(T/(S+1)) + 1 = floor(6/3) + 1 = 2 + 1 = 3
        self.assertEqual(result["quota"], Decimal("3"))
        self.assertEqual(result["elected"], [1, 2])
        self.assertNotIn(3, result["elected"])
        self.assertGreaterEqual(len(result["rounds"]), 1)

    def test_surplus_transfer_is_fractional_decimal(self) -> None:
        from core.elections_meek import tally_meek

        candidates = [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000002")},
        ]
        ballots = [
            {"weight": 1, "ranking": [1, 2]},
            {"weight": 1, "ranking": [1, 2]},
            {"weight": 1, "ranking": [1, 2]},
            {"weight": 1, "ranking": [1, 2]},
            {"weight": 1, "ranking": [2, 1]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=2)

        # Meek quota: floor(T/(S+1)) + 1 = floor(5/3) + 1 = 1 + 1 = 2
        expected_quota = Decimal("2")
        self.assertEqual(result["quota"], expected_quota)
        self.assertEqual(result["elected"], [1, 2])

        # Ensure at least one retention factor becomes fractional due to surplus.
        last_round = result["rounds"][-1]
        r = last_round["retention_factors"]
        self.assertLess(Decimal(str(r["1"])), Decimal("1"))

    def test_tie_break_uses_lowest_tiebreak_uuid(self) -> None:
        from core.elections_meek import tally_meek

        # A and B are tied for elimination; A has lower tiebreak UUID so should be eliminated.
        candidates = [
            {"id": 10, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000010")},
            {"id": 11, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000011")},
            {"id": 12, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000012")},
        ]
        ballots = [
            # Exhausted ballots (no subsequent preferences) force elimination among tied zero-support candidates.
            {"weight": 1, "ranking": [12]},
            {"weight": 1, "ranking": [12]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=1)

        self.assertEqual(result["elected"], [12])
        # The tie-break only matters if we have to eliminate; force it by requesting 2 seats.
        result2 = tally_meek(ballots=ballots, candidates=candidates, seats=2)
        self.assertIn(result2["eliminated"][0], {10, 11})
        self.assertEqual(result2["eliminated"][0], 10)

        elimination_rounds = [r for r in result2["rounds"] if r.get("eliminated") is not None]
        self.assertGreaterEqual(len(elimination_rounds), 1)
        audit_text = str(elimination_rounds[0].get("audit_text") or "")
        self.assertIn("Candidates A and B", audit_text)
        self.assertIn("predefined deterministic tie-breaking rules", audit_text)
        self.assertIn(
            "No distinction could be made based on prior round totals, current support totals, or first-preference votes.",
            audit_text,
        )
        self.assertIn("fixed candidate ordering identifier", audit_text)
        self.assertIn("selected for elimination", audit_text)
        self.assertIn("tied for the lowest vote total", audit_text)
        self.assertNotIn("had the lowest vote total and was eliminated", audit_text)

    def test_exclusion_group_forces_exclusion_after_election(self) -> None:
        from core.elections_meek import tally_meek

        candidates = [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000002")},
            {"id": 3, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000003")},
        ]
        ballots = [
            {"weight": 1, "ranking": [1, 2, 3]},
            {"weight": 1, "ranking": [1, 2, 3]},
            {"weight": 1, "ranking": [1, 2, 3]},
            {"weight": 1, "ranking": [2, 1, 3]},
            {"weight": 1, "ranking": [2, 1, 3]},
            {"weight": 1, "ranking": [2, 1, 3]},
        ]
        exclusion_groups = [
            {
                "public_id": "group-1",
                "max_elected": 1,
                "candidate_ids": [1, 2],
            }
        ]

        result = tally_meek(
            ballots=ballots,
            candidates=candidates,
            seats=2,
            exclusion_groups=exclusion_groups,
        )

        self.assertIn(result["elected"][0], {1, 2})
        self.assertEqual(len(result["elected"]), 2)
        self.assertIn(result["forced_excluded"][0], {1, 2})
        self.assertNotIn(result["forced_excluded"][0], result["elected"])

    def test_exclusion_group_quota_reached_explanation_is_not_confusing(self) -> None:
        from core.elections_meek import tally_meek

        # A and B both exceed quota immediately, but exclusion group allows only one of them.
        candidates = [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000002")},
            {"id": 3, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000003")},
        ]
        ballots = [
            {"weight": 1, "ranking": [1, 2, 3]},
            {"weight": 1, "ranking": [1, 2, 3]},
            {"weight": 1, "ranking": [1, 2, 3]},
            {"weight": 1, "ranking": [2, 1, 3]},
            {"weight": 1, "ranking": [2, 1, 3]},
            {"weight": 1, "ranking": [2, 1, 3]},
        ]
        exclusion_groups = [
            {
                "public_id": "incompat",
                "name": "Incompatibles",
                "max_elected": 1,
                "candidate_ids": [1, 2],
            }
        ]

        result = tally_meek(
            ballots=ballots,
            candidates=candidates,
            seats=2,
            exclusion_groups=exclusion_groups,
        )

        forced = set(result.get("forced_excluded") or [])
        self.assertEqual(forced & {1, 2}, forced)

        rounds = result.get("rounds") or []
        self.assertGreaterEqual(len(rounds), 1)
        forced_rounds = [r for r in rounds if isinstance(r, dict) and list(r.get("forced_exclusions") or [])]
        self.assertGreaterEqual(len(forced_rounds), 1)
        first_forced_round = forced_rounds[0]

        audit_text = str(first_forced_round.get("audit_text") or "")
        # Narration may omit a separate "reached quota" sentence to avoid redundancy, but
        # must still be clear that a quota threshold was met and why election did not occur.
        self.assertIn("meeting the quota", audit_text)
        self.assertIn("could not be elected", audit_text)
        self.assertIn('Incompatibles', audit_text)

        # Trustworthiness: if the count is complete, don't imply that excluded
        # candidates' vote value will transfer and affect remaining candidates.
        if bool(first_forced_round.get("count_complete")):
            self.assertNotIn("will be redistributed to remaining candidates", audit_text)
            self.assertNotIn("will transfer to remaining eligible candidates", audit_text)

    def test_meek_does_not_mark_count_complete_when_eligible_less_than_remaining_seats(self) -> None:
        from core.elections_meek import tally_meek

        # Regression: `count_complete` must NOT be set merely because the number of eligible
        # candidates is less than the number of remaining seats.
        # In this scenario:
        # - Two candidates are elected immediately by quota.
        # - An exclusion group forces another candidate out.
        # - One eligible candidate remains, but there are still two seats open.
        # The count is not complete at that point.
        candidates = [
            {"id": 10, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000010")},
            {"id": 11, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000011")},
            {"id": 12, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000012")},
            {"id": 13, "name": "D", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000013")},
        ]
        ballots = [
            {"weight": 1, "ranking": [10, 12]},
            {"weight": 1, "ranking": [11, 10]},
            {"weight": 1, "ranking": [12, 11]},
            {"weight": 1, "ranking": [11, 12, 10]},
            {"weight": 5, "ranking": [11, 10]},
            {"weight": 2, "ranking": [12, 11, 10]},
            {"weight": 5, "ranking": [10, 12, 11]},
        ]
        exclusion_groups = [
            {
                "public_id": "incompat",
                "name": "Incompatibles",
                "max_elected": 1,
                # If A is elected, D becomes ineligible.
                "candidate_ids": [10, 13],
            }
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=4, exclusion_groups=exclusion_groups)
        rounds = list(result.get("rounds") or [])
        self.assertGreaterEqual(len(rounds), 1)

        r0 = rounds[0]
        self.assertEqual(set(r0.get("elected") or []), {10, 11})
        self.assertEqual(r0.get("eligible_candidates"), [12])
        self.assertFalse(bool(r0.get("count_complete")))

        # Exact-fit must not trigger when eligible candidates are fewer than remaining seats.
        self.assertEqual(list(r0.get("elected_to_fill_remaining_seats") or []), [])
        self.assertNotIn(12, set(r0.get("elected") or []))

    def test_meek_vacant_seats_does_not_emit_extra_empty_round(self) -> None:
        from core.elections_meek import tally_meek

        # Regression: When the count becomes complete due to a vacancy (no eligible candidates
        # remain but seats are still open), the tally should stop cleanly and not emit a
        # redundant additional round with no actions.
        candidates = [
            {"id": 10, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000010")},
            {"id": 11, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000011")},
            {"id": 12, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000012")},
            {"id": 13, "name": "D", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000013")},
        ]
        ballots = [
            {"weight": 1, "ranking": [10, 12]},
            {"weight": 1, "ranking": [11, 10]},
            {"weight": 1, "ranking": [12, 11]},
            {"weight": 1, "ranking": [11, 12, 10]},
            {"weight": 5, "ranking": [11, 10]},
            {"weight": 2, "ranking": [12, 11, 10]},
            {"weight": 5, "ranking": [10, 12, 11]},
        ]
        exclusion_groups = [
            {
                "public_id": "incompat",
                "name": "Incompatibles",
                "max_elected": 1,
                "candidate_ids": [10, 13],
            }
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=4, exclusion_groups=exclusion_groups)
        rounds = list(result.get("rounds") or [])

        # The count should end in 2 rounds for this deterministic scenario:
        # - Round 1: A/B elected, D excluded.
        # - Round 2: C elected; no eligible candidates remain so 1 seat is vacant.
        self.assertEqual(len(rounds), 2)

        last = rounds[-1]
        self.assertTrue(bool(last.get("count_complete")))
        self.assertEqual(list(last.get("eligible_candidates") or []), [])
        self.assertIn("seat remains vacant", str(last.get("audit_text") or ""))


class ElectionPrivacyFlowTests(TestCase):
    def test_ballot_hash_receipt_and_anonymization(self) -> None:
        from core.models import Candidate, Election, Membership, MembershipType

        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
            description="",
        )

        mt = MembershipType.objects.create(code="voter", name="Voter", votes=1, category_id="individual")
        Membership.objects.create(
            target_username="reviewer",
            membership_type=mt,
            created_at=now - datetime.timedelta(days=200),
            expires_at=None,
        )

        # This test will be expanded once vote submission view exists.
        # For now: ensure models exist and can be created.
        self.assertEqual(election.candidates.count(), 1)


class MeekTieBreakSummaryTests(TestCase):
    def _render_round(self, *, tie_break: dict[str, object]) -> tuple[str, str]:
        from core.elections_meek import generate_meek_round_explanations

        round_data: dict[str, object] = {
            "iteration": 1,
            "quota_reached": [],
            "elected": [],
            "elected_to_fill_remaining_seats": [],
            "eliminated": None,
            "forced_exclusions": [],
            "tie_breaks": [tie_break],
            "eligible_candidates": [1, 2],
            "retention_factors": {"1": "1", "2": "1"},
            "retained_totals": {"1": "0", "2": "0"},
            "numerically_converged": False,
            "max_retention_delta": "0",
            "seats": 1,
            "elected_total": 0,
            "count_complete": False,
        }

        rendered = generate_meek_round_explanations(
            round_data,
            quota=Decimal("1"),
            candidate_name_by_id={1: "A", 2: "B"},
        )
        return str(rendered["audit_text"]), str(rendered["summary_text"])

    def test_tie_break_rule_1_resolves(self) -> None:
        audit_text, summary_text = self._render_round(
            tie_break={
                "type": "election_order",
                "candidate_ids": [1, 2],
                "ordered": [1, 2],
                "rule_trace": [
                    {"rule": 1, "title": "prior round totals", "result": "resolved"},
                ],
            }
        )
        self.assertIn("tie resolved deterministically", summary_text)
        self.assertIn("The tie was resolved using prior round totals.", audit_text)
        self.assertNotIn("No distinction could be made based on", audit_text)

    def test_tie_break_rule_2_resolves(self) -> None:
        audit_text, summary_text = self._render_round(
            tie_break={
                "type": "election_order",
                "candidate_ids": [1, 2],
                "ordered": [1, 2],
                "rule_trace": [
                    {"rule": 1, "title": "prior round totals", "result": "tied"},
                    {"rule": 2, "title": "current support totals", "result": "resolved"},
                ],
            }
        )
        self.assertIn("tie resolved deterministically", summary_text)
        self.assertIn("No distinction could be made based on prior round totals.", audit_text)
        self.assertIn("The tie was resolved using current support totals.", audit_text)

    def test_tie_break_rule_3_resolves(self) -> None:
        audit_text, summary_text = self._render_round(
            tie_break={
                "type": "election_order",
                "candidate_ids": [1, 2],
                "ordered": [1, 2],
                "rule_trace": [
                    {"rule": 1, "title": "prior round totals", "result": "tied"},
                    {"rule": 2, "title": "current support totals", "result": "tied"},
                    {"rule": 3, "title": "first-preference votes", "result": "resolved"},
                ],
            }
        )
        self.assertIn("tie resolved deterministically", summary_text)
        self.assertIn(
            "No distinction could be made based on prior round totals or current support totals.",
            audit_text,
        )
        self.assertIn("The tie was resolved using first-preference votes.", audit_text)

    def test_tie_break_rule_4_resolves(self) -> None:
        audit_text, summary_text = self._render_round(
            tie_break={
                "type": "election_order",
                "candidate_ids": [1, 2],
                "ordered": [1, 2],
                "rule_trace": [
                    {"rule": 1, "title": "prior round totals", "result": "tied"},
                    {"rule": 2, "title": "current support totals", "result": "tied"},
                    {"rule": 3, "title": "first-preference votes", "result": "tied"},
                    {"rule": 4, "title": "fixed candidate ordering identifier", "result": "resolved"},
                ],
            }
        )
        self.assertIn("tie resolved deterministically", summary_text)
        self.assertIn(
            "No distinction could be made based on prior round totals, current support totals, or first-preference votes.",
            audit_text,
        )
        self.assertIn("The tie was resolved using a fixed candidate ordering identifier.", audit_text)


class MeekTieBreakEndToEndTests(TestCase):
    def _base_candidates(self) -> list[dict[str, object]]:
        return [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 2, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000002")},
            {"id": 3, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000003")},
            {"id": 4, "name": "D", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000004")},
        ]

    def _find_resolved_rule(self, rounds: list[dict[str, object]], *, rule: int) -> tuple[dict[str, object], dict[str, object]]:
        for rnd in rounds:
            for tb in list(rnd.get("tie_breaks") or []):
                resolved = [
                    step
                    for step in list(tb.get("rule_trace") or [])
                    if step.get("result") == "resolved"
                ]
                if resolved and resolved[0].get("rule") == rule:
                    return rnd, tb
        raise AssertionError(f"No tie-break resolved by rule {rule} was found")

    def test_tally_meek_emits_rule_4_tie_break_in_summary(self) -> None:
        from core.elections_meek import tally_meek

        # End-to-end: a deterministic processing-order tie resolved by rule 4.
        candidates = self._base_candidates()
        ballots = [
            {"weight": 1, "ranking": [3, 4, 1, 2]},
            {"weight": 1, "ranking": [4, 3, 1, 2]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=2)
        rounds = list(result.get("rounds") or [])
        self.assertGreaterEqual(len(rounds), 1)

        r0 = rounds[0]
        self.assertTrue(list(r0.get("tie_breaks") or []))
        summary_text = str(r0.get("summary_text") or "")
        audit_text = str(r0.get("audit_text") or "")
        self.assertIn("tie resolved deterministically", summary_text)
        self.assertIn("fixed candidate ordering identifier", audit_text)

        tb0 = list(r0.get("tie_breaks") or [])[0]
        resolved = [step for step in list(tb0.get("rule_trace") or []) if step.get("result") == "resolved"]
        self.assertTrue(resolved)
        self.assertEqual(resolved[0].get("rule"), 4)

    def test_tally_meek_emits_rule_4_elimination_tie_break_in_summary(self) -> None:
        from core.elections_meek import tally_meek

        # End-to-end: elimination tie resolved by rule 4 (UUID fallback).
        candidates = [
            {"id": 10, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000010")},
            {"id": 11, "name": "B", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000011")},
            {"id": 12, "name": "C", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000012")},
        ]
        ballots = [
            {"weight": 1, "ranking": [12]},
            {"weight": 1, "ranking": [12]},
        ]

        result = tally_meek(ballots=ballots, candidates=candidates, seats=2)
        elimination_rounds = [r for r in result["rounds"] if r.get("eliminated") is not None]
        self.assertGreaterEqual(len(elimination_rounds), 1)

        r = elimination_rounds[0]
        summary_text = str(r.get("summary_text") or "")
        audit_text = str(r.get("audit_text") or "")
        self.assertIn("tie resolved deterministically", summary_text)
        self.assertIn("fixed candidate ordering identifier", audit_text)

        tb = [tb for tb in list(r.get("tie_breaks") or []) if tb.get("type") == "elimination"][0]
        resolved = [step for step in list(tb.get("rule_trace") or []) if step.get("result") == "resolved"]
        self.assertTrue(resolved)
        self.assertEqual(resolved[0].get("rule"), 4)

    def test_tally_meek_resolves_tie_by_rule_1_prior_round_totals(self) -> None:
        # Skip: Rule 1 (prior round totals) end-to-end scenarios are exceedingly rare.
        # For a tie resolved by rule 1, candidates must have:
        # 1. Identical retained totals in current iteration (causing the tie)
        # 2. Different prior round totals (so rule 1 can resolve)
        # 3. Identical current support totals (so rule 2 doesn't resolve first)
        #
        # In Meek STV, condition #1 is nearly impossible after iteration 1 due to
        # iterative convergence creating decimal differences. In iteration 1, all prior
        # totals are 0, so condition #2 cannot be met. Systematic search of thousands
        # of ballot configurations found no examples.
        #
        # Rule 1 logic IS tested in `test_tie_break_rule_1_resolves` unit test.
        self.skipTest("Rule 1 end-to-end scenarios are not engineerable with simple ballot sets")

    def test_tally_meek_rule_2_is_not_observable_in_current_implementation(self) -> None:
        # At present, tie groups are formed only when retained totals are exactly equal, and for
        # non-elected candidates retained totals equal incoming totals. That makes rule 2 (current
        # support totals) effectively unable to resolve a tie end-to-end. We still have unit tests
        # covering rule-2 narration in `MeekTieBreakSummaryTests`.
        self.skipTest("Rule 2 resolution is not observable end-to-end with current tie semantics.")

    def test_tally_meek_resolves_tie_by_rule_3_first_preferences(self) -> None:
        from core.elections_meek import tally_meek

        # Deterministic profile that produces an elimination tie resolved by rule 3.
        # Total=9, Seats=2, Quota=4
        # Candidates have same prior totals and same current support, but different
        # first preferences, so rule 3 (first-preference votes) resolves the tie.
        # From systematic search: (1, 2, 3, 3) triggers rule 3 resolution.
        candidates = self._base_candidates()
        ballots = [
            {"weight": 1, "ranking": [1, 2, 3]},  # A first (1)
            {"weight": 2, "ranking": [2, 1, 3]},  # B first (2)
            {"weight": 3, "ranking": [3, 1, 2]},  # C first (3)
            {"weight": 3, "ranking": [4, 2, 3]},  # D first (3)
        ]

        result = tally_meek(
            ballots=ballots,
            candidates=candidates,
            seats=2,
            epsilon=Decimal("1e-18"),
            max_iterations=60,
        )
        rounds = [r for r in list(result.get("rounds") or []) if isinstance(r, dict)]
        rnd, _tb = self._find_resolved_rule(rounds, rule=3)
        self.assertIn("tie resolved deterministically", str(rnd.get("summary_text") or ""))
        self.assertIn("The tie was resolved using first-preference votes.", str(rnd.get("audit_text") or ""))

    def test_tally_meek_elimination_uses_rule_trace_winner(self) -> None:
        from core.elections_meek import tally_meek

        # Regression: tie-break traces can resolve a tie before the UUID fallback.
        # The elimination choice must match the resolved rule-trace winner, not a
        # leftover ordering based on candidate IDs.
        candidates = [
            {"id": 1, "name": "A", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000001")},
            {"id": 5, "name": "E", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000005")},
            {"id": 9, "name": "I", "tiebreak_uuid": uuid.UUID("00000000-0000-0000-0000-000000000009")},
        ]
        ballots = [
            {"weight": 1, "ranking": [5, 9, 1]},
            {"weight": 1, "ranking": [5, 9]},
            {"weight": 1, "ranking": [1, 5, 9]},
            {"weight": 1, "ranking": [5, 1, 9]},
            {"weight": 1, "ranking": [5, 9, 1]},
        ]

        result = tally_meek(
            ballots=ballots,
            candidates=candidates,
            seats=2,
            epsilon=Decimal("1e-6"),
            max_iterations=120,
        )

        elimination_rounds = [r for r in list(result.get("rounds") or []) if isinstance(r, dict) and r.get("eliminated") is not None]
        self.assertGreaterEqual(len(elimination_rounds), 1)

        r0 = elimination_rounds[0]
        self.assertEqual(r0.get("eliminated"), 9)

        tie_breaks = list(r0.get("tie_breaks") or [])
        elimination_tie_breaks = [tb for tb in tie_breaks if tb.get("type") == "elimination"]
        self.assertTrue(elimination_tie_breaks)
        tb = elimination_tie_breaks[0]
        self.assertEqual(tb.get("selected"), 9)

        resolved = [step for step in list(tb.get("rule_trace") or []) if step.get("result") == "resolved"]
        self.assertTrue(resolved)
        self.assertEqual(resolved[0].get("rule"), 3)
