from __future__ import annotations

import re


class RTLParseError(ValueError):
    """A model response does not contain the requested RTL module."""


_MODULE = re.compile(r"\bmodule\s+(?:automatic\s+)?([A-Za-z_][A-Za-z0-9_$]*)\b")
_ENDMODULE = re.compile(r"\bendmodule\b")


def extract_rtl(response: str, top_module: str) -> str:
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
