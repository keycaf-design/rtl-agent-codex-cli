import unittest

from rtl_agent.tools.rtl_parser import RTLParseError, extract_rtl


class RTLParserTests(unittest.TestCase):
    def test_extracts_first_module_from_fenced_response(self) -> None:
        response = "Explanation\n```systemverilog\nmodule demo(input logic a);\nendmodule\n```\nafter"
        self.assertEqual(extract_rtl(response, "demo"), "module demo(input logic a);\nendmodule\n")

    def test_rejects_missing_endmodule(self) -> None:
        with self.assertRaisesRegex(RTLParseError, "endmodule"):
            extract_rtl("module demo;", "demo")

    def test_wrong_top_raises(self) -> None:
        with self.assertRaisesRegex(RTLParseError, "Requested top module"):
            extract_rtl("module other; endmodule", "demo")


if __name__ == "__main__":
    unittest.main()
