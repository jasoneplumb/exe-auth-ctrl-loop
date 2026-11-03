"""v3 Step 07: MCP server-side redemption (docs/v3/spec.md §6; finding F2, closes L3)."""

import random
import subprocess
import sys
import tempfile
import textwrap
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _support import (
    MCP_SERVER,
    AdapterHandlers,
    issue,
    principals_for,
)

from exe_auth_ctrl_loop import mcp
from exe_auth_ctrl_loop.authority import (
    AuthorityController,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    PartitionKey,
    Policy,
    Proposal,
)
from exe_auth_ctrl_loop.mcp import (
    EXTENSION_VERSION,
    KEY_AUDIENCE,
    KEY_MAC,
    KEY_REDEMPTION,
    KEY_VERSION,
    McpServer,
    MetaVerificationError,
    attach_meta,
    build_call_meta,
    call_digest,
    verify_call_meta,
)
from exe_auth_ctrl_loop.principals import PrincipalKind
from exe_auth_ctrl_loop.store import Store

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
SECRET = b"shared-secret-for-tests"
OTHER = "other-mcp"
KEY = PartitionKey(
    "openai", "m1", "pp1", "anthropic", "m2", "ep1",
    "refund-api-v2", "pol1", "prod1", "refund", "0.95-1.00", "low",
)
POLICY = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)


def proposal(n: int = 1, effects=frozenset({"refund:write"})) -> Proposal:
    tool = "create_refund" if effects else "lookup_order"
    return Proposal(f"p-{n}", "order", tool, {"order_id": n, "usd": 24}, .96,
                    effects, KEY, "openai")


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "authority.db"
        self.db = Store(self.path)
        self.addCleanup(self.db.close)
        self.principals = principals_for(self.db)
        self.principals.register(OTHER, PrincipalKind.SERVICE, by="controller")
        self.store = EvidenceStore(self.db)
        self.store.put(EvidenceSnapshot("e1", 1, KEY, 99, 1, NOW - timedelta(days=1), NOW))
        self.controller = AuthorityController(self.store, POLICY, random.Random(1), lambda: NOW)
        self.gateway = ExecutionGateway(
            lambda: NOW, evidence=self.store, store=self.db, handlers=AdapterHandlers(),
            principals=self.principals,
        )
        self.effects: list[dict] = []

    def authorize(self, p: Proposal, audience: str | None = MCP_SERVER):
        decision = self.controller.evaluate(p)
        return decision, issue(self.gateway, decision, p, POLICY, audience=audience)

    def request(self, p: Proposal, audience: str = MCP_SERVER) -> dict:
        decision, token = self.authorize(p, audience)
        meta = build_call_meta(decision, p, token, SECRET)
        self.token = token
        return attach_meta({"name": p.tool_name, "arguments": dict(p.parameters)}, meta)

    def server(self, name: str = MCP_SERVER) -> McpServer:
        return McpServer(self.gateway, name, SECRET)

    def handler(self, arguments):
        self.effects.append(dict(arguments))
        return {"receipt": "ok"}


class WireFormatTests(Fixture):
    """Criteria 1 and 5c."""

    def test_audience_and_redemption_are_signed(self):
        params = self.request(proposal())
        meta = params["_meta"]
        self.assertEqual(meta[KEY_VERSION], "0.2")
        self.assertEqual(meta[KEY_AUDIENCE], MCP_SERVER)
        self.assertEqual(meta[KEY_REDEMPTION], "server")
        for key, value in ((KEY_AUDIENCE, OTHER), (KEY_REDEMPTION, "nonce")):
            with self.subTest(key=key):
                forged = {**params, "_meta": {**meta, key: value}}
                with self.assertRaisesRegex(MetaVerificationError, "failed authentication"):
                    verify_call_meta(forged, "create_refund", forged["arguments"], SECRET,
                                     NOW, audience=value if key == KEY_AUDIENCE else MCP_SERVER)

    def test_only_version_0_2_is_accepted(self):
        params = self.request(proposal())
        for version in ("0.1", "0.3", "1.0", None):
            with self.subTest(version=version):
                meta = {k: v for k, v in params["_meta"].items()}
                meta[KEY_VERSION] = version
                body = {k: meta[k] for k in mcp._SIGNED_KEYS}
                meta[KEY_MAC] = mcp._mac(body, SECRET)  # validly signed, wrong version
                with self.assertRaisesRegex(MetaVerificationError, "unsupported"):
                    verify_call_meta({**params, "_meta": meta}, "create_refund",
                                     params["arguments"], SECRET, NOW, audience=MCP_SERVER)

    def test_a_0_1_block_is_refused(self):
        # 0.1 had no audience and no redemption: what an un-upgraded host would send
        params = self.request(proposal())
        old = {k: v for k, v in params["_meta"].items() if k not in (KEY_AUDIENCE, KEY_REDEMPTION)}
        old[KEY_VERSION] = "0.1"
        body = {k: old[k] for k in mcp._SIGNED_KEYS if k in old}
        old[KEY_MAC] = mcp._mac(body, SECRET)
        with self.assertRaisesRegex(MetaVerificationError, "unsupported extension version"):
            self.server().call({**params, "_meta": old}, self.handler, NOW)
        self.assertEqual(self.effects, [])

    def test_a_local_token_cannot_travel_in_a_block(self):
        decision, token = self.authorize(proposal(), audience=None)
        with self.assertRaisesRegex(ValueError, "not bound to an MCP server"):
            build_call_meta(decision, proposal(), token, SECRET)

    def test_EXTENSION_VERSION_is_0_2(self):
        self.assertEqual(EXTENSION_VERSION, "0.2")


class ConsequentialCallTests(Fixture):
    """Criteria 2 and 3: redeemed at the server, once, by its audience only."""

    def test_the_call_runs_once_and_a_replay_is_refused(self):
        params = self.request(proposal())
        receipt = self.server().call(params, self.handler, NOW)
        self.assertEqual(receipt.result, {"receipt": "ok"})
        self.assertEqual(receipt.authority.token_id, self.token.token_id)
        self.assertEqual(receipt.authority.audience, MCP_SERVER)
        with self.assertRaisesRegex(PermissionError, "consumed"):
            self.server().call(params, self.handler, NOW)
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(self.gateway.token(self.token.token_id).state, "handler_completed")

    def test_another_server_is_refused_by_audience(self):
        params = self.request(proposal())
        with self.assertRaisesRegex(MetaVerificationError, "another server"):
            self.server(OTHER).call(params, self.handler, NOW)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.gateway.token(self.token.token_id).state, "issued")

    def test_a_revoked_token_is_refused_at_the_server(self):
        params = self.request(proposal())
        self.store.invalidate(KEY, "oracle recalled")  # RV-02
        with self.assertRaisesRegex(PermissionError, "revoked by EVIDENCE_INVALIDATED"):
            self.server().call(params, self.handler, NOW)
        self.assertEqual(self.effects, [])

    def test_a_consequential_block_cannot_take_the_nonce_path(self):
        params = self.request(proposal())
        nonce = {**params, "_meta": {**params["_meta"]}}
        with self.assertRaisesRegex(PermissionError, "must be redeemed"):
            self.gateway.accept_nonce(self.token.token_id, audience=MCP_SERVER,
                                      call_digest=call_digest("create_refund",
                                                              nonce["arguments"]))


class ChannelTests(Fixture):
    """Criteria 5 and 5b: the host never spends an MCP-bound token."""

    def test_the_host_does_not_consume_an_mcp_token(self):
        p = proposal()
        params = self.request(p)
        with self.assertRaisesRegex(PermissionError, "bound to MCP server mcp-server"):
            self.gateway.execute(self.token.token_id, p)
        self.assertEqual(self.gateway.token(self.token.token_id).state, "issued")
        self.server().call(params, self.handler, NOW)  # still the server's to spend
        self.assertEqual(len(self.effects), 1)

    def test_server_redemption_refuses_a_local_token_and_a_wrong_audience(self):
        p = proposal()
        _, local = self.authorize(p, audience=None)
        digest = call_digest(p.tool_name, p.parameters)
        with self.assertRaisesRegex(PermissionError, "not bound to MCP"):
            self.gateway.redeem_remote(local.token_id, audience=MCP_SERVER,
                                       call_digest=digest, handler=lambda: "ran")
        q = proposal(2)
        _, bound = self.authorize(q)
        with self.assertRaisesRegex(PermissionError, "another server"):
            self.gateway.redeem_remote(bound.token_id, audience=OTHER,
                                       call_digest=call_digest(q.tool_name, q.parameters),
                                       handler=lambda: "ran")
        self.assertEqual(self.gateway.token(bound.token_id).state, "issued")

    def test_server_redemption_checks_the_call_against_the_stored_proposal(self):
        q = proposal()
        _, bound = self.authorize(q)
        with self.assertRaisesRegex(PermissionError, "does not match"):
            self.gateway.redeem_remote(bound.token_id, audience=MCP_SERVER,
                                       call_digest=call_digest(q.tool_name, {"order_id": 9}),
                                       handler=lambda: "ran")
        self.assertEqual(self.gateway.token(bound.token_id).state, "issued")

    def test_the_audience_must_be_an_active_service(self):
        p = proposal()
        decision = self.controller.evaluate(p)
        for audience in ("agent", "nobody"):
            with self.subTest(audience=audience), self.assertRaisesRegex(
                PermissionError, "not an active service"
            ):
                issue(self.gateway, replace(decision, decision_id=f"d-{audience}"), p, POLICY,
                      audience=audience)


class NonceTests(Fixture):
    """Criterion 4: an effect-free call is accepted once."""

    def test_a_second_presentation_is_refused(self):
        params = self.request(proposal(effects=frozenset()))
        self.assertEqual(params["_meta"][KEY_REDEMPTION], "nonce")
        self.server().call(params, self.handler, NOW)
        with self.assertRaisesRegex(PermissionError, "already presented"):
            self.server().call(params, self.handler, NOW)
        self.assertEqual(len(self.effects), 1)
        self.assertEqual(self.gateway.token(self.token.token_id).state, "issued")

    def test_a_revoked_token_is_refused_on_the_nonce_path(self):
        params = self.request(proposal(effects=frozenset()))
        self.store.invalidate(KEY, "oracle recalled")
        with self.assertRaisesRegex(PermissionError, "revoked by EVIDENCE_INVALIDATED"):
            self.server().call(params, self.handler, NOW)

    WORKER = textwrap.dedent("""
        import sys
        sys.path.insert(0, sys.argv[4])
        from _support import AdapterHandlers, principals_for
        from exe_auth_ctrl_loop.authority import ExecutionGateway
        from datetime import datetime
        from exe_auth_ctrl_loop.store import Store
        store = Store(sys.argv[1])
        now = datetime.fromisoformat(sys.argv[5])  # the test's clock, not the wall clock
        gateway = ExecutionGateway(lambda: now, store=store, handlers=AdapterHandlers(),
                                   principals=principals_for(store))
        try:
            gateway.accept_nonce(sys.argv[2], audience="mcp-server", call_digest=sys.argv[3])
            print("accepted")
        except PermissionError as exc:
            print("refused:", exc)
        store.close()
    """)

    def test_two_processes_present_the_same_block_and_one_is_accepted(self):
        p = proposal(effects=frozenset())
        self.request(p)
        digest = call_digest(p.tool_name, p.parameters)
        support = str(Path(__file__).resolve().parent)
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", self.WORKER, str(self.path), self.token.token_id,
                 digest, support, NOW.isoformat()],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for _ in range(2)
        ]
        outcomes = []
        for worker in workers:
            out, err = worker.communicate(timeout=120)
            self.assertEqual(worker.returncode, 0, err)
            outcomes.append(out.strip())
        self.assertEqual(sorted(o.split(":")[0] for o in outcomes), ["accepted", "refused"],
                         outcomes)
        self.assertIn("already presented", next(o for o in outcomes if o.startswith("refused")))


if __name__ == "__main__":
    unittest.main()
