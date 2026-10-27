"""
The tool registry validates arguments with the full JSON Schema validator and refuses a
malformed schema at registration. Offline.
"""

import unittest

from exe_auth_ctrl_loop import ToolDefinition, ToolRegistry, ToolValidationError


def lookup_tool(input_schema):
    return ToolDefinition(
        name="lookup_order",
        description="Look up one order.",
        input_schema=input_schema,
        effects=frozenset({"order:read"}),
        version="orders-v1",
        task_category="order_lookup",
        risk_class="low",
        handler=lambda args: "ok",
    )


class SchemaValidationTests(unittest.TestCase):
    def test_keywords_beyond_type_and_range_are_enforced(self):
        # the removed fallback validator silently ignored each of these keywords
        registry = ToolRegistry()
        registry.register(lookup_tool({
            "type": "object",
            "properties": {
                "code": {"type": "string", "pattern": "^[A-Z]{3}-[0-9]{4}$"},
                "note": {"type": "string", "maxLength": 5},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                "ref": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            },
            "required": ["code"],
            "additionalProperties": False,
        }))
        registry.validate(
            "lookup_order", {"code": "ABC-1234", "note": "hi", "tags": ["a"], "ref": None}
        )
        for bad in (
            {"code": "abc-1234"},
            {"code": "ABC-1234", "note": "too long"},
            {"code": "ABC-1234", "tags": ["a", "b", "c"]},
            {"code": "ABC-1234", "ref": "7"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ToolValidationError):
                registry.validate("lookup_order", bad)

    def test_malformed_schema_is_refused_at_registration(self):
        for schema in (
            {"type": "object", "required": "code"},
            {"type": "object", "properties": {"code": {"type": "strng"}}},
            {"type": "object", "properties": {"code": {"type": "string", "maxLength": "5"}}},
        ):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                ToolRegistry().register(lookup_tool(schema))


if __name__ == "__main__":
    unittest.main()
