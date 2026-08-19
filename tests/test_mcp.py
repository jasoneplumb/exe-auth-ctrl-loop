"""
Intent: Hold the MCP binding to its two promises — legal key names, and no side effect on
        a call that does not verify
Context: Offline; exercises mcp.py against real Decision and AuthorizationToken objects
        from authority.py rather than fixtures
Pattern: One denial path per test, so a regression names the check that broke
"""

import random
import unittest
from datetime import datetime, timedelta, timezone

from exe_auth_ctrl_loop.authority import (
    AuthorityController,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    PartitionKey,
    Policy,
    Proposal,
    Route,
)
from exe_auth_ctrl_loop.mcp import (
    EXTENSION_PREFIX,
    KEY_AUDIT_COMMITTED,
    KEY_EVIDENCE_SNAPSHOT,
    KEY_MAC,
    KEY_PROPOSAL_DIGEST,
    MetaKeyError,
    MetaVerificationError,
    attach_meta,
    build_call_meta,
    meta_key,
    validate_meta_prefix,
    verify_call_meta,
)

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)
SECRET = b"gateway-and-server-shared-secret"


class MetaKeyRuleTests(unittest.TestCase):
    """
    intent: Pin the MCP _meta naming rules, including the reserved-prefix carve-out
    context: The spec's own worked example — com.mcp.tools/ reserved, com.example.mcp/ not
    """

    def test_vendor_prefix_is_accepted(self):
        validate_meta_prefix("com.jasoneplumb.exe-auth/")
        validate_meta_prefix("com.example/")

    def test_prefixes_reserved_for_mcp_are_rejected(self):
        for reserved in ("io.modelcontextprotocol/", "dev.mcp/", "com.mcp.tools/"):
            with self.assertRaises(MetaKeyError):
                validate_meta_prefix(reserved)

    def test_second_label_example_is_not_reserved(self):
        validate_meta_prefix("com.example.mcp/")

    def test_malformed_prefixes_are_rejected(self):
        for bad in ("com.example", "9com.example/", "com..example/", "com.example-/"):
            with self.assertRaises(MetaKeyError):
                validate_meta_prefix(bad)

    def test_malformed_names_are_rejected(self):
        for bad in ("-leading", "trailing-", "has space"):
            with self.assertRaises(MetaKeyError):
                meta_key(bad)

    def test_empty_name_is_accepted_because_the_spec_permits_it(self):
        """
        intent: Pin the empty name as deliberate, not an oversight in the pattern
        context: The spec reads "Unless empty, MUST begin and end with an alphanumeric
                 character", so a bare prefix is a legal key and rejecting it would be
                 stricter than MCP
        """
        self.assertEqual(meta_key(""), EXTENSION_PREFIX)

    def test_keys_carry_the_vendor_prefix(self):
        self.assertTrue(KEY_PROPOSAL_DIGEST.startswith(EXTENSION_PREFIX))


class CallMetaTests(unittest.TestCase):
    def setUp(self):
        self.key = PartitionKey(
            "openai", "openai-model-pinned", "proposal-v1",
            "anthropic", "claude-model-pinned", "execution-v1",
            "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low",
        )
        self.proposal = Proposal(
            "p-1", "refund order", "create_refund", {"order_id": 7, "usd": 24},
            .96, frozenset({"refund:write"}), self.key, "openai",
        )
        self.policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)
        self.store = EvidenceStore()
        self.store.put(EvidenceSnapshot("e1", 1, self.key, 99, 1, NOW - timedelta(days=2), NOW))
        self.gateway = ExecutionGateway(lambda: NOW)

    def authorize(self, audit_rate=0.0, human_approved=False):
        policy = Policy("pol1", {"low": .90}, 30, timedelta(days=30), audit_rate)
        controller = AuthorityController(self.store, policy, random.Random(1), lambda: NOW)
        decision = controller.evaluate(self.proposal)
        token = self.gateway.issue(
            decision, self.proposal, policy, human_approved=human_approved
        )
        return decision, token

    def signed_params(self, **kwargs):
        decision, token = self.authorize(**kwargs)
        meta = build_call_meta(decision, self.proposal, token, SECRET)
        return attach_meta({"name": "create_refund", "arguments": dict(self.proposal.parameters)},
                           meta)

    def test_carries_the_three_fields_the_binding_specifies(self):
        decision, token = self.authorize()
        meta = build_call_meta(decision, self.proposal, token, SECRET)
        self.assertEqual(meta[KEY_PROPOSAL_DIGEST], self.proposal.proposal_digest)
        self.assertIsNotNone(meta[KEY_EVIDENCE_SNAPSHOT])
        self.assertFalse(meta[KEY_AUDIT_COMMITTED])

    def test_authorized_call_verifies(self):
        params = self.signed_params()
        verified = verify_call_meta(
            params, "create_refund", dict(self.proposal.parameters), SECRET, NOW,
        )
        self.assertEqual(verified.proposal_digest, self.proposal.proposal_digest)
        self.assertFalse(verified.audit_committed)
        self.assertFalse(verified.human_approved)

    def test_committed_audit_flag_travels_with_the_call(self):
        decision, token = self.authorize(audit_rate=1.0, human_approved=True)
        self.assertEqual(decision.route, Route.AUDIT)
        meta = build_call_meta(decision, self.proposal, token, SECRET)
        self.assertTrue(meta[KEY_AUDIT_COMMITTED])

    def test_human_approval_is_distinguishable_at_the_server(self):
        """
        intent: The selection-bias firewall needs approved runs to stay distinguishable
        effect: Without this field a downstream reporter would count them as autonomous
                evidence, censoring the estimator exactly where it is least trustworthy
        """
        params = self.signed_params(human_approved=True)
        verified = verify_call_meta(
            params, "create_refund", dict(self.proposal.parameters), SECRET, NOW,
        )
        self.assertTrue(verified.human_approved)

    def test_edited_arguments_are_rejected(self):
        params = self.signed_params()
        with self.assertRaises(MetaVerificationError):
            verify_call_meta(params, "create_refund", {"order_id": 7, "usd": 9999}, SECRET, NOW)

    def test_substituted_tool_is_rejected(self):
        params = self.signed_params()
        with self.assertRaises(MetaVerificationError):
            verify_call_meta(
                params, "delete_order", dict(self.proposal.parameters), SECRET, NOW,
            )

    def test_forged_metadata_is_rejected(self):
        params = self.signed_params()
        with self.assertRaises(MetaVerificationError):
            verify_call_meta(
                params, "create_refund", dict(self.proposal.parameters), b"wrong-secret", NOW,
            )

    def test_tampered_field_is_rejected(self):
        params = self.signed_params()
        params["_meta"][KEY_AUDIT_COMMITTED] = True
        with self.assertRaises(MetaVerificationError):
            verify_call_meta(
                params, "create_refund", dict(self.proposal.parameters), SECRET, NOW,
            )

    def test_expired_authorization_is_rejected(self):
        params = self.signed_params()
        later = NOW + self.policy.token_ttl + timedelta(seconds=1)
        with self.assertRaises(MetaVerificationError):
            verify_call_meta(
                params, "create_refund", dict(self.proposal.parameters), SECRET, later,
            )

    def test_absent_metadata_is_rejected(self):
        with self.assertRaises(MetaVerificationError):
            verify_call_meta({"name": "create_refund"}, "create_refund", {}, SECRET, NOW)

    def test_stripped_signature_is_rejected(self):
        params = self.signed_params()
        del params["_meta"][KEY_MAC]
        with self.assertRaises(MetaVerificationError):
            verify_call_meta(
                params, "create_refund", dict(self.proposal.parameters), SECRET, NOW,
            )

    def test_unsigned_metadata_cannot_be_built(self):
        decision, token = self.authorize()
        with self.assertRaises(ValueError):
            build_call_meta(decision, self.proposal, token, b"")

    def test_foreign_meta_keys_are_preserved(self):
        decision, token = self.authorize()
        meta = build_call_meta(decision, self.proposal, token, SECRET)
        params = attach_meta(
            {"name": "create_refund", "_meta": {"progressToken": 3}}, meta,
        )
        self.assertEqual(params["_meta"]["progressToken"], 3)
        self.assertIn(KEY_PROPOSAL_DIGEST, params["_meta"])

    def test_collision_with_existing_meta_is_refused(self):
        decision, token = self.authorize()
        meta = build_call_meta(decision, self.proposal, token, SECRET)
        with self.assertRaises(ValueError):
            attach_meta({"_meta": {KEY_PROPOSAL_DIGEST: "squatted"}}, meta)


if __name__ == "__main__":
    unittest.main()
