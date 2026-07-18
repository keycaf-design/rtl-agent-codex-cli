from __future__ import annotations

import re


class RTLParseError(ValueError):
    """A model response does not contain the requested RTL module."""


_MODULE = re.compile(r"\bmodule\s+(?:automatic\s+)?([A-Za-z_][A-Za-z0-9_$]*)\b")
_ENDMODULE = re.compile(r"\bendmodule\b")


def extract_rtl(response: str, top_module: str) -> str:
    if not response.strip():
        raise RTLParseError("Model response is empty")
    start = _MODULE.search(response)
    if start is None:
        raise RTLParseError("Model response does not contain a module declaration")
    end = _ENDMODULE.search(response, start.end())
    if end is None:
        raise RTLParseError("Model response does not contain a matching endmodule")
    rtl = response[start.start():end.end()].strip()
    names = {match.group(1) for match in _MODULE.finditer(rtl)}
    if top_module not in names:
        raise RTLParseError(f"Requested top module '{top_module}' was not found")
    return rtl + "\n"


def extract_testbench(response: str, tb_top_module: str, dut_top_module: str) -> str:
    """Extract and minimally validate one model-generated testbench module."""
    declarations = list(_MODULE.finditer(response))
    if not declarations:
        raise RTLParseError("Model response does not contain a module declaration")
    if len(declarations) != 1:
        names = ", ".join(match.group(1) for match in declarations)
        raise RTLParseError(f"Expected exactly one testbench module, found: {names}")
    testbench = extract_rtl(response, tb_top_module)
    if not re.search(rf"\b{re.escape(dut_top_module)}\b", testbench):
        raise RTLParseError(
            f"DUT module name '{dut_top_module}' was not found in the testbench"
        )
    return testbench


def module_interface(source: str, module_name: str) -> str:
    """Return a whitespace-normalized module declaration through its first semicolon."""
    match = re.search(
        rf"\bmodule\s+{re.escape(module_name)}\b(?P<header>.*?);",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise RTLParseError(f"Module interface for '{module_name}' was not found")
    return re.sub(r"\s+", " ", match.group(0)).strip()


def require_same_interface(before: str, after: str, module_name: str) -> None:
    if module_interface(before, module_name) != module_interface(after, module_name):
        raise RTLParseError(f"Repair changed public interface of module '{module_name}'")
