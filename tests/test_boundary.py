"""
v3 Step 04: the execution boundary (docs/v3/spec.md §1, §3 rule 1, §4).

The gateway is the only door to a side effect. Its handler is chosen by the token it
consumes, it issues only for a decision committed to the ledger, and nothing handed to the
agent side reaches a handler except through it.
"""

import inspect
import random
import sqlite3
import types
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from _support import AGENT, gateway_store, scratch_store

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
from exe_auth_ctrl_loop.executor import ClaudeExecutionAgent
from exe_auth_ctrl_loop.ledger import EventLedger
from exe_auth_ctrl_loop.pipeline import CrossModelAuthorityLoop
from exe_auth_ctrl_loop.providers import OpenAIProposalGenerator
from exe_auth_ctrl_loop.shadow import ShadowEvidenceLog
from exe_auth_ctrl_loop.tools import HandlerTable, ToolCatalog, ToolDefinition

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "integer"}, "usd": {"type": "number"}},
    "required": ["order_id", "usd"],
    "additionalProperties": False,
}


def tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name, f"{name} one order", SCHEMA, frozenset({f"{name}:write"}), "api-v1", "orders", "low",
    )


def key(tool_version: str = "api-v1") -> PartitionKey:
    return PartitionKey(
        "openai", "m1", "pp1", "anthropic", "m2", "ep1",
        tool_version, "pol1", "prod1", "orders", "0.95-1.00", "low",
    )


def proposal(tool_name: str, order_id: int = 7) -> Proposal:
    return Proposal(
        f"p-{tool_name}-{order_id}", "act on one order", tool_name,
        {"order_id": order_id, "usd": 5}, .96, frozenset({f"{tool_name}:write"}), key(), "openai",
    )


POLICY = Policy("pol1", {"low": .90}, 30, timedelta(days=30), 0.0)

# Host boundary objects (owner decision, 2026-11-02): the walk does not enter them. The
# property under test is that no other route to a handler exists.
BOUNDARY = (ExecutionGateway, AuthorityController)


def reachable(*roots: object, boundary: tuple[type, ...] = BOUNDARY) -> list[object]:
    """
    Every object reachable from `roots` through what the code supplies: instance
    attributes, containers, bound methods, and closures. Not followed: module globals
    (`__globals__`), classes, and modules, which are not objects handed to anyone, and the
    boundary types, which are the intended door.
    """
    seen: dict[int, object] = {}
    stack = list(roots)
    while stack:
        obj = stack.pop()
        if id(obj) in seen or isinstance(obj, (type, types.ModuleType)):
            continue
        seen[id(obj)] = obj
        if isinstance(obj, boundary) and obj not in roots:
            continue
        if isinstance(obj, (str, bytes, int, float, bool, type(None), datetime, timedelta)):
            continue
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
        elif isinstance(obj, types.MethodType):
            stack.extend((obj.__self__, obj.__func__))
        elif isinstance(obj, types.FunctionType):
            stack.extend(cell.cell_contents for cell in obj.__closure__ or ())
            stack.extend(obj.__defaults__ or ())
            stack.extend((obj.__kwdefaults__ or {}).values())
        else:
            stack.extend(getattr(obj, "__dict__", {}).values())
            for slot in getattr(type(obj), "__slots__", ()):
                if hasattr(obj, slot):
                    stack.append(getattr(obj, slot))
    return list(seen.values())


class Loop:
    """The shipped composition: proposer, executor, pipeline, controller, gateway."""

    def __init__(self) -> None:
        self.effects: list[dict[str, Any]] = []
        self.catalog = ToolCatalog()
        self.catalog.register(tool("refund"))
        self.catalog.register(tool("cancel"))
        self.handlers = HandlerTable(self.catalog)
        self.handlers.register("refund", lambda args: self.effects.append({"refund": args}))
        self.handlers.register("cancel", lambda args: self.effects.append({"cancel": args}))
        db = scratch_store()
        self.ledger = EventLedger(db)
        self.store = EvidenceStore(db)
        self.store.put(EvidenceSnapshot("e1", 1, key(), 99, 1, NOW - timedelta(days=1), NOW))
        self.controller = AuthorityController(self.store, POLICY, random.Random(1), lambda: NOW)
        self.gateway = ExecutionGateway(lambda: NOW, **gateway_store(db), handlers=self.handlers)
        self.agent = ClaudeExecutionAgent(
            "m2", "ep1", self.controller, self.gateway, self.catalog, client=object(), **AGENT,
        )
        self.proposer = OpenAIProposalGenerator("m1", "pp1", self.catalog, client=object())
        self.pipeline = CrossModelAuthorityLoop(
            proposer=self.proposer, executor=self.agent, ledger=self.ledger,
            policy_version="pol1", environment_version="prod1",
            shadow=ShadowEvidenceLog(self.ledger),
        )

    def leaks(self, *roots: object) -> list[object]:
        handlers = set(map(id, self.handlers._handlers.values()))
        return [
            obj for obj in reachable(*roots)
            if isinstance(obj, HandlerTable) or id(obj) in handlers
        ]

    def authorize(self, p: Proposal):
        decision = self.controller.evaluate(p)
        self.ledger.commit_decision(decision, p)
        return decision, self.gateway.issue(decision, p, POLICY, **AGENT)


class CatalogAndHandlerTableTests(unittest.TestCase):
    """Criterion 1."""

    def test_the_catalog_cannot_run_a_tool(self):
        self.assertFalse(hasattr(ToolCatalog(), "execute"))
        self.assertNotIn("handler", {f.name for f in ToolDefinition.__dataclass_fields__.values()})

    def test_the_agent_and_proposer_hold_the_catalog_only(self):
        loop = Loop()
        self.assertIs(loop.agent.catalog, loop.catalog)
        self.assertIs(loop.proposer.catalog, loop.catalog)
        self.assertIs(loop.gateway.handlers, loop.handlers)

    def test_a_handler_needs_a_catalogued_tool(self):
        with self.assertRaises(ValueError):
            HandlerTable(ToolCatalog()).register("refund", lambda args: None)


class TokenChoosesTheHandlerTests(unittest.TestCase):
    """Criterion 2."""

    def test_execute_takes_no_handler(self):
        self.assertEqual(
            list(inspect.signature(ExecutionGateway.execute).parameters),
            ["self", "token_id", "proposal"],
        )

    def test_the_handler_is_the_one_the_token_names(self):
        loop = Loop()
        refund = proposal("refund")
        _, token = loop.authorize(refund)
        loop.gateway.execute(token.token_id, refund)
        self.assertEqual(loop.effects, [{"refund": {"order_id": 7, "usd": 5}}])

    def test_another_tools_proposal_cannot_spend_the_token(self):
        loop = Loop()
        _, token = loop.authorize(proposal("refund"))
        with self.assertRaises(PermissionError):
            loop.gateway.execute(token.token_id, proposal("cancel"))
        self.assertEqual(loop.effects, [])
        self.assertEqual(loop.gateway.token(token.token_id).state, "issued")

    def test_arguments_the_schema_refuses_do_not_spend_the_token(self):
        loop = Loop()
        bad = replace(proposal("refund"), parameters={"order_id": "seven", "usd": 5})
        _, token = loop.authorize(bad)
        with self.assertRaisesRegex(PermissionError, "tool not executable"):
            loop.gateway.execute(token.token_id, bad)
        self.assertEqual(loop.effects, [])
        self.assertEqual(loop.gateway.token(token.token_id).state, "issued")

    def test_a_tool_with_no_handler_does_not_spend_the_token(self):
        loop = Loop()
        loop.catalog.register(tool("archive"))
        archive = proposal("archive")
        _, token = loop.authorize(archive)
        with self.assertRaisesRegex(PermissionError, "no handler registered"):
            loop.gateway.execute(token.token_id, archive)
        self.assertEqual(loop.gateway.token(token.token_id).state, "issued")


class ReachabilityTests(unittest.TestCase):
    """Criterion 3: no route to a handler except through the gateway."""

    def test_nothing_handed_to_the_agent_side_reaches_a_handler(self):
        loop = Loop()
        for name, root in (("agent", loop.agent), ("proposer", loop.proposer),
                           ("pipeline", loop.pipeline)):
            with self.subTest(root=name):
                self.assertEqual(loop.leaks(root), [])

    def test_the_walk_does_reach_the_gateway(self):
        # the door is there; the walk just does not go through it
        loop = Loop()
        self.assertIn(loop.gateway, reachable(loop.agent))
        self.assertIn(loop.handlers, reachable(loop.gateway))

    def test_an_agent_given_the_table_is_caught(self):
        # positive control: the walk is not vacuous
        loop = Loop()
        loop.agent.catalog = loop.handlers  # what executor.py must never be given
        self.assertIn(loop.handlers, loop.leaks(loop.agent))

    def test_a_handler_hidden_in_a_closure_is_caught(self):
        loop = Loop()
        refund_handler = loop.handlers._handlers["refund"]
        loop.agent.commit = lambda decision, p: refund_handler
        self.assertIn(refund_handler, loop.leaks(loop.agent))


class CommittedDecisionTests(unittest.TestCase):
    """Criterion 4: issue() only for a committed decision (closes L7, enforces C20)."""

    def setUp(self):
        self.loop = Loop()
        self.p = proposal("refund")
        self.decision = self.loop.controller.evaluate(self.p)
        self.assertEqual(self.decision.route, Route.AUTONOMOUS)

    def test_an_uncommitted_decision_is_refused(self):
        with self.assertRaisesRegex(PermissionError, "not committed"):
            self.loop.gateway.issue(self.decision, self.p, POLICY, **AGENT)
        self.assertEqual(self.loop.gateway.tokens, {})

    def test_a_decision_built_by_hand_is_refused(self):
        forged = replace(self.decision, decision_id="forged")
        with self.assertRaisesRegex(PermissionError, "not committed"):
            self.loop.gateway.issue(forged, self.p, POLICY, **AGENT)

    def test_a_decision_altered_after_commit_is_refused(self):
        held = replace(self.decision, route=Route.HUMAN_APPROVAL)
        self.loop.ledger.commit_decision(held, self.p)
        promoted = replace(held, route=Route.AUTONOMOUS)
        with self.assertRaisesRegex(PermissionError, "differs from its committed record"):
            self.loop.gateway.issue(promoted, self.p, POLICY, **AGENT)

    def test_a_committed_decision_issues(self):
        self.loop.ledger.commit_decision(self.decision, self.p)
        token = self.loop.gateway.issue(self.decision, self.p, POLICY, **AGENT)
        self.assertEqual(token.decision_id, self.decision.decision_id)

    def test_a_decision_is_committed_once(self):
        self.loop.ledger.commit_decision(self.decision, self.p)
        before = len(self.loop.ledger.events)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "decisions.decision_id"):
            self.loop.ledger.commit_decision(self.decision, self.p)
        self.assertEqual(len(self.loop.ledger.events), before)


if __name__ == "__main__":
    unittest.main()
