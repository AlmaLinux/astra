from __future__ import annotations

from django.test import TestCase

from core.elections_sankey import build_sankey_flows


class SankeyFlowTests(TestCase):
    def test_build_sankey_flows_links_candidates_across_rounds(self) -> None:
        tally_result = {
            "rounds": [
                {
                    "iteration": 1,
                    "retained_totals": {
                        "1": "3",
                        "2": "1",
                    },
                    "elected": [],
                    "eliminated": None,
                },
                {
                    "iteration": 2,
                    "retained_totals": {
                        "1": "2",
                        "2": "2",
                    },
                    "elected": ["2"],
                    "eliminated": "1",
                },
            ],
            "elected": ["2"],
        }
        candidate_username_by_id = {1: "A", 2: "B"}

        flows, elected_nodes, eliminated_nodes = build_sankey_flows(
            tally_result=tally_result,
            candidate_username_by_id=candidate_username_by_id,
            votes_cast=4,
        )

        self.assertTrue(flows)
        self.assertIn({"from": "Voters", "to": "Round 1 · A", "flow": 3.0}, flows)
        self.assertIn({"from": "Voters", "to": "Round 1 · B", "flow": 1.0}, flows)

        self.assertIn({"from": "Round 1 · A", "to": "Round 2 · A", "flow": 2.0}, flows)
        self.assertIn({"from": "Round 1 · B", "to": "Round 2 · B", "flow": 1.0}, flows)
        self.assertIn({"from": "Round 1 · A", "to": "Round 2 · B", "flow": 1.0}, flows)

        self.assertTrue(
            all(" · " in row["to"] or row["to"] == "Voters" for row in flows)
        )
        self.assertEqual(elected_nodes, ["Round 2 · B"])
        self.assertNotIn("Round 1 · B", elected_nodes)
        self.assertEqual(eliminated_nodes, ["Round 2 · A"])
