import json
import unittest

from rtl_agent.tools.structured_output import (
    StructuredOutputError,
    parse_tb_audit_response,
)


def response(decision: str = "APPROVE") -> dict:
    return {
        "decision": decision,
        "summary": "complete" if decision == "APPROVE" else "unsafe",
        "findings": [] if decision == "APPROVE" else ["missing check"],
        "missing_testcases": [],
        "unsafe_patterns": [],
        "required_changes": [],
    }


class StructuredOutputTests(unittest.TestCase):
    def test_valid_approve(self) -> None:
        result = parse_tb_audit_response(json.dumps(response()))
        self.assertEqual(result.decision, "APPROVE")

    def test_valid_reject(self) -> None:
        result = parse_tb_audit_response(json.dumps(response("REJECT")))
        self.assertEqual(result.decision, "REJECT")

    def test_text_around_json_fails_closed(self) -> None:
        raw = f"Audit result:\n{json.dumps(response())}\nDone."
        with self.assertRaises(StructuredOutputError):
            parse_tb_audit_response(raw)

    def test_invalid_json_fails_closed(self) -> None:
        with self.assertRaises(StructuredOutputError):
            parse_tb_audit_response('{"decision":')

    def test_missing_required_field_fails_closed(self) -> None:
        data = response()
        del data["summary"]
        with self.assertRaises(StructuredOutputError):
            parse_tb_audit_response(json.dumps(data))

    def test_unknown_decision_fails_closed(self) -> None:
        data = response()
        data["decision"] = "PASS"
        with self.assertRaises(StructuredOutputError):
            parse_tb_audit_response(json.dumps(data))

    def test_non_string_array_item_fails_closed(self) -> None:
        data = response()
        data["findings"] = [1]
        with self.assertRaises(StructuredOutputError):
            parse_tb_audit_response(json.dumps(data))

    def test_unsupported_reject_fails_closed(self) -> None:
        data = response("REJECT")
        data["findings"] = []
        with self.assertRaises(StructuredOutputError):
            parse_tb_audit_response(json.dumps(data))


if __name__ == "__main__":
    unittest.main()
