import unittest

from exe_auth_ctrl_loop.ledger import EventLedger


class LedgerTests(unittest.TestCase):
    def test_hash_chain_verifies(self):
        ledger = EventLedger()
        ledger.append("proposal.created", "p-1", "openai", {"digest": "a"})
        ledger.append("decision.recorded", "p-1", "controller", {"route": "autonomous"})
        self.assertTrue(ledger.verify())
        self.assertEqual(ledger.events[1].previous_hash, ledger.events[0].event_hash)


if __name__ == "__main__":
    unittest.main()
