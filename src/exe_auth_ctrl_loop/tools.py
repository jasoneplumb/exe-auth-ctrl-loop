"""
Intent: Keep the facts about a tool -- its effects, version, risk, and schema -- on the
        host's side of the boundary, where no model can restate them
Context: authority.py reads risk and effects from here; executor.py validates every request
        against it before a decision is even sought
Pattern: Single source of truth. A model's claim about a tool is compared to this registry
        and discarded on mismatch, never merged into it.
Future: Handlers are plain callables in-process; production puts them behind a service
        identity the model runtimes cannot address.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .risk import RiskPolicy


class ToolValidationError(ValueError):
    pass


RiskClassifier = Callable[[Mapping[str, Any]], str]
ToolHandler = Callable[[Mapping[str, Any]], Any]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    effects: frozenset[str]
    version: str
    task_category: str
    risk_class: str | RiskClassifier | None
    handler: ToolHandler

    def classify_risk(self, parameters: Mapping[str, Any]) -> str:
        """
        intent: Let risk depend on the actual argument values, not just the tool
        effect: A $24 refund and a $24,000 refund can land in different partitions and face
                different thresholds, so evidence earned on the cheap case does not
                authorize the expensive one
        constraint: Only for a registry without a RiskPolicy. With one attached, the
                    policy classifies and this field must be None (ToolRegistry.register).
        """
        if self.risk_class is None:
            raise ToolValidationError(f"{self.name} defers risk classification to the registry")
        if callable(self.risk_class):
            return self.risk_class(parameters)
        return self.risk_class


class ToolRegistry:
    """
    Host-owned tool metadata and handlers; model-supplied metadata is not trusted.

    constraint: Risk has one classifier per registry. With `risk` attached, every tool
                defers to it (risk envelopes in risk.py) and must register with
                risk_class=None; without one, each tool carries its own. Both at once
                is refused, so two answers to "what class is this operation" cannot exist.
    """

    def __init__(self, risk: RiskPolicy | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.risk = risk

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        if tool.input_schema.get("type") != "object":
            raise ValueError("tool input schema must describe an object")
        try:
            Draft202012Validator.check_schema(tool.input_schema)
        except SchemaError as exc:
            # a malformed schema fails here, at setup, rather than mid-operation
            raise ValueError(f"{tool.name}: invalid input schema: {exc.message}") from exc
        if self.risk is not None and tool.risk_class is not None:
            raise ValueError(
                f"{tool.name}: this registry classifies risk through its RiskPolicy; "
                "register with risk_class=None"
            )
        if self.risk is None and tool.risk_class is None:
            raise ValueError(f"{tool.name}: no risk classifier (no RiskPolicy on the registry)")
        self._tools[tool.name] = tool

    def classify_risk(self, name: str, parameters: Mapping[str, Any]) -> str:
        """
        intent: The single place a risk class is derived for an operation
        effect: providers.py uses it to build the partition key; executor.py re-derives
                and compares before each call; the controller and gateway check the same
                policy's envelopes and budgets. One source, three checkpoints.
        constraint: Arguments no envelope admits have no class and raise, which both
                    callers turn into a refusal -- never a default class.
        """
        tool = self.get(name)
        if self.risk is None:
            return tool.classify_risk(parameters)
        risk_class = self.risk.risk_class(name, parameters)
        if risk_class is None:
            raise ToolValidationError(f"no risk envelope admits these {name} arguments")
        return risk_class

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolValidationError(f"unknown tool: {name}") from exc

    def validate(self, name: str, parameters: Mapping[str, Any]) -> None:
        """
        intent: Check arguments against the host's schema, not the model's idea of it
        method: jsonschema's Draft 2020-12 validator. It is a required dependency, so there
                is no reduced fallback that could accept what it does not implement.
        context: Runs twice per operation -- at proposal time and again at execution -- so
                 a registry change between the two is caught rather than assumed away
        """
        tool = self.get(name)
        errors = sorted(
            Draft202012Validator(tool.input_schema).iter_errors(dict(parameters)),
            key=lambda e: list(e.path),
        )
        if errors:
            rendered = "; ".join(error.message for error in errors)
            raise ToolValidationError(f"invalid {name} parameters: {rendered}")

    def execute(self, name: str, parameters: Mapping[str, Any]) -> Any:
        self.validate(name, parameters)
        return self.get(name).handler(parameters)

    def public_catalog(self) -> list[dict[str, Any]]:
        """
        intent: Show the proposing model what exists without showing it anything it could
                use to claim authority
        constraint: Deliberately omits `version`, `risk_class`, and `handler`. The host
                    resolves those itself, so a model cannot propose a risk class or a tool
                    version and have the claim believed.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "effects": sorted(tool.effects),
                "task_category": tool.task_category,
            }
            for tool in self._tools.values()
        ]

    def anthropic_tools(
        self, proposal_ids_by_tool: Mapping[str, list[str]]
    ) -> list[dict[str, Any]]:
        """
        intent: Build the execution model's tool schema so that selecting a proposal is the
                only move available to it
        method: Inject a required `proposal_id` whose enum is exactly this bundle's ids,
                then set additionalProperties=False and strict=True
        effect: A tool call becomes a selector plus a verbatim echo. An id outside the enum
                is rejected by the API before the host ever sees it.
        """
        tools: list[dict[str, Any]] = []
        for name, proposal_ids in proposal_ids_by_tool.items():
            registered = self.get(name)
            schema = dict(registered.input_schema)
            properties = dict(schema.get("properties", {}))
            if "proposal_id" in properties:
                raise ValueError("registered tool schemas may not reserve proposal_id")
            properties["proposal_id"] = {
                "type": "string",
                "enum": proposal_ids,
                "description": "The exact authorized proposal being requested.",
            }
            required = list(schema.get("required", []))
            required.append("proposal_id")
            schema.update({
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            })
            tools.append({
                "name": name,
                "description": registered.description,
                "input_schema": schema,
                "strict": True,
            })
        return tools

