from pathlib import Path
from unittest import TestCase


class VerifyBallotChainScriptGuidanceTests(TestCase):
    def test_script_uses_product_wording_for_user_inputs(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "static" / "verify-ballot-chain.py"
        content = script_path.read_text(encoding="utf-8")

        self.assertIn('ballot_receipt_code = "', content)
        self.assertIn('submission_nonce = "', content)
        self.assertIn('receipt_previous_ledger_hash = "', content)
        self.assertIn('receipt_current_ledger_hash = "', content)
        self.assertIn('final_election_chain_head = "', content)
        self.assertIn("Ballot receipt code", content)
        self.assertIn("Submission nonce", content)
        self.assertIn("Receipt previous ledger hash", content)
        self.assertIn("Receipt current ledger hash", content)
        self.assertIn("Final election chain head", content)
        self.assertIn("vote receipt email", content)
        self.assertIn("ballot verification page", content)
        self.assertIn("election page", content)
        self.assertIn("Your ballot receipt code appears", content)
        self.assertIn("Exported receipt previous ledger hash", content)
        self.assertIn("Receipt previous ledger hash", content)
        self.assertIn("Exported receipt current ledger hash", content)
        self.assertIn("Chain integrity verified: chain anchor -> head is a single, complete path", content)
        self.assertIn("Chain integrity verified: root -> head is a single, complete path", content)
        self.assertNotIn("your_ballot_hash =", content)
        self.assertNotIn("your_previous_chain_hash =", content)
        self.assertNotIn("Your Ballot receipt code appears", content)
        self.assertNotIn("Exported Previous ledger hash", content)
        self.assertNotIn("Receipt Previous ledger hash", content)
        self.assertNotIn("Chain integrity verified: genesis → head is a single, complete path", content)
