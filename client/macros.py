"""Pure helpers to convert between a macro step list and the GUI's text form.

Kept free of Qt/heavy imports so they can be unit-tested and reused anywhere.
A macro step is a dict: {"key": str, "hold": bool, "taps": int, "delay": float}.
Text form is one step per line: 'f7 hold', 'h x3', 'tab /0.2', 'altleft+c'.
"""
from __future__ import annotations


def parse_macro_line(line: str) -> dict | None:
    """'f7 hold', 'h x3', 'tab /0.2', 'altleft+c' -> a macro step dict (or None)."""
    toks = line.split()
    if not toks:
        return None
    step = {"key": toks[0], "hold": False, "taps": 1, "delay": 0.1}
    for t in toks[1:]:
        tl = t.lower()
        if tl == "hold":
            step["hold"] = True
        elif tl.startswith("x") and tl[1:].isdigit():
            step["taps"] = int(tl[1:])
        elif tl.startswith("/"):
            try:
                step["delay"] = float(tl[1:])
            except ValueError:
                pass
    return step


def macro_to_text(seq: list) -> str:
    lines = []
    for s in seq or []:
        parts = [str(s.get("key", ""))]
        if s.get("hold"):
            parts.append("hold")
        if int(s.get("taps", 1) or 1) > 1:
            parts.append(f"x{int(s['taps'])}")
        if abs(float(s.get("delay", 0.1) or 0) - 0.1) > 1e-9:
            parts.append(f"/{s.get('delay')}")
        lines.append(" ".join(parts))
    return "\n".join(lines)
