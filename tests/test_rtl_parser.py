import unittest

from rtl_agent.tools.rtl_parser import RTLParseError, extract_rtl, extract_testbench


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

    def test_testbench_rejects_multiple_modules(self) -> None:
        response = "module helper; endmodule\nmodule demo_tb; demo dut(); endmodule"
        with self.assertRaisesRegex(RTLParseError, "exactly one"):
            extract_testbench(response, "demo_tb", "demo")

    def test_testbench_requires_dut_name(self) -> None:
        with self.assertRaisesRegex(RTLParseError, "DUT module name"):
            extract_testbench("module demo_tb; endmodule", "demo_tb", "demo")


if __name__ == "__main__":
    unittest.main()
