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

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - exercised only without optional dependencies
    # constraint: jsonschema is optional, so the fallback validator below must stay
    # strictly narrower than the real one -- a permissive fallback would mean validation
    # silently weakens when the dependency is absent.
    Draft202012Validator = None  # type: ignore[assignment,misc]


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
    risk_class: str | RiskClassifier
    handler: ToolHandler

    def classify_risk(self, parameters: Mapping[str, Any]) -> str:
        """
        intent: Let risk depend on the actual argument values, not just the tool
        effect: A $24 refund and a $24,000 refund can land in different partitions and face
                different thresholds, so evidence earned on the cheap case does not
                authorize the expensive one
        """
        if callable(self.risk_class):
            return self.risk_class(parameters)
        return self.risk_class


class ToolRegistry:
    """Host-owned tool metadata and handlers; model-supplied metadata is not trusted."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        if tool.input_schema.get("type") != "object":
            raise ValueError("tool input schema must describe an object")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolValidationError(f"unknown tool: {name}") from exc

    def validate(self, name: str, parameters: Mapping[str, Any]) -> None:
        """
        intent: Check arguments against the host's schema, not the model's idea of it
        method: jsonschema when installed, otherwise the narrow fallback validator below
        context: Runs twice per operation -- at proposal time and again at execution -- so
                 a registry change between the two is caught rather than assumed away
        """
        tool = self.get(name)
        if Draft202012Validator is not None:
            errors = sorted(
                Draft202012Validator(tool.input_schema).iter_errors(dict(parameters)),
                key=lambda e: list(e.path),
            )
            if errors:
                rendered = "; ".join(error.message for error in errors)
                raise ToolValidationError(f"invalid {name} parameters: {rendered}")
            return
        try:
            _validate_schema(tool.input_schema, dict(parameters), "$")
        except ToolValidationError as exc:
            raise ToolValidationError(f"invalid {name} parameters: {exc}") from exc

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


def _validate_schema(schema: Mapping[str, Any], value: Any, path: str) -> None:
    """
    intent: Validate without jsonschema installed, without ever being more permissive
    method: Walk the small subset of JSON Schema the prototype uses
    constraint: An unrecognised `type` raises rather than passing. A validator that ignored
                what it did not understand would let an unknown construct through as valid,
                which is the one failure mode a fallback must not have.
    """
    expected = schema.get("type")
    type_checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if expected not in type_checks:
        raise ToolValidationError(f"{path}: unsupported schema type {expected!r}")
    if not type_checks[expected](value):
        raise ToolValidationError(f"{path}: expected {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolValidationError(f"{path}: value is not in enum")
    if "const" in schema and value != schema["const"]:
        raise ToolValidationError(f"{path}: value does not match const")

    if expected == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise ToolValidationError(f"{path}: missing {sorted(missing)}")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ToolValidationError(f"{path}: unexpected {sorted(extras)}")
        for key, child in value.items():
            if key in properties:
                _validate_schema(properties[key], child, f"{path}.{key}")
    elif expected == "array" and "items" in schema:
        for index, child in enumerate(value):
            _validate_schema(schema["items"], child, f"{path}[{index}]")
    elif expected in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolValidationError(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolValidationError(f"{path}: above maximum")
