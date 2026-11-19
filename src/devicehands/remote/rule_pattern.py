"""The portable regex subset of protocol v2 (protocol/v2/framing.md §7).

A text or partition parameter in the bench op table carries a `pattern`.
Both ends run it — the hub to report a typo before a channel is opened, the
bench as the authority — and the two ends are not written in the same
language. So the pattern may only use what Python `re`, ECMA-262 and Rust
`regex` all read the same way, and it is always a whole-string match.

`problem(pattern)` says why a pattern is outside the subset ("" when it is
inside); `fullmatch(pattern, value)` runs one. The bench program carries an
identical copy (it cannot import this package); both are checked against
protocol/v2/patterns.json.
"""

from __future__ import annotations

import re

# Outside a class these must be escaped to be literal; inside a class, `]`,
# `[`, `\` and `^` (first position) are the ones that need it.
_META = set(".^$|?*+()[]{}\\")
_CLASS_ESCAPES = {"d", "D", "w", "W", "s", "S"}
_HEX = set("0123456789abcdefABCDEF")
_QUANT_RE = re.compile(r"\{(\d{1,4})(,(\d{1,4})?)?\}")


def problem(pattern: str) -> str:
    """Why `pattern` is outside the subset, or "" when it is inside."""
    if not isinstance(pattern, str) or not pattern:
        return "empty pattern"
    try:
        _Parser(pattern).parse()
    except _Refused as e:
        return str(e)
    try:
        re.compile(pattern, re.ASCII)
    except re.error as e:  # pragma: no cover - the parser should refuse first
        return f"does not compile: {e}"
    return ""


def fullmatch(pattern: str, value: str) -> bool:
    """Whole-string match with ASCII-only \\d \\w \\s."""
    return re.fullmatch(pattern, value, re.ASCII) is not None


class _Refused(Exception):
    pass


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.depth = 0

    def fail(self, why: str) -> _Refused:
        return _Refused(f"{why} at offset {self.i}")

    def peek(self, n: int = 0) -> str:
        j = self.i + n
        return self.s[j] if j < len(self.s) else ""

    def parse(self) -> None:
        self.alternation()
        if self.i != len(self.s):
            raise self.fail(f"unexpected {self.peek()!r}")

    def alternation(self) -> None:
        self.sequence()
        while self.peek() == "|":
            self.i += 1
            self.sequence()

    def sequence(self) -> None:
        while self.i < len(self.s) and self.peek() not in ("|", ")"):
            self.atom()
            self.quantifier()

    def quantifier(self) -> None:
        c = self.peek()
        if c in ("*", "+", "?"):
            self.i += 1
        elif c == "{":
            m = _QUANT_RE.match(self.s, self.i)
            if m is None:
                raise self.fail("a literal { must be escaped; a quantifier is {n}, {n,} or {n,m}")
            lo, comma, hi = m.group(1), m.group(2), m.group(3)
            if comma and hi is not None and int(hi) < int(lo):
                raise self.fail("quantifier {n,m} with m < n")
            self.i = m.end()
        else:
            return
        if self.peek() in ("?", "+", "*", "{"):
            raise self.fail("lazy, possessive or repeated quantifiers are not in the subset")

    def atom(self) -> None:
        c = self.peek()
        if c == "(":
            if self.s.startswith("(?:", self.i):
                self.i += 3
            elif self.peek(1) == "?":
                raise self.fail("only (...) and (?:...) groups are in the subset")
            else:
                self.i += 1
            self.depth += 1
            self.alternation()
            if self.peek() != ")":
                raise self.fail("unclosed group")
            self.i += 1
            self.depth -= 1
        elif c == "[":
            self.char_class()
        elif c == "\\":
            self.escape(in_class=False)
        elif c == ".":
            raise self.fail("'.' matches different characters in each language; use a class")
        elif c in ("^", "$"):
            raise self.fail("anchors are not written: every pattern is a whole-string match")
        elif c in _META:
            raise self.fail(f"{c!r} must be escaped to be literal")
        else:
            self.literal()

    def literal(self) -> None:
        c = self.peek()
        if not (" " <= c <= "~"):
            raise self.fail(
                "only printable ASCII literals; write other characters as \\xHH or a class"
            )
        self.i += 1

    def escape(self, *, in_class: bool) -> None:
        nxt = self.peek(1)
        if nxt in _CLASS_ESCAPES:
            self.i += 2
            return
        if nxt == "x":
            if self.peek(2) in _HEX and self.peek(3) in _HEX and self.peek(2) and self.peek(3):
                self.i += 4
                return
            raise self.fail("\\x needs exactly two hex digits")
        allowed = {"\\", "]", "[", "-", "^"} if in_class else _META
        if nxt and nxt in allowed:
            self.i += 2
            return
        raise self.fail(f"escape \\{nxt} is not in the subset")

    def char_class(self) -> None:
        self.i += 1  # [
        if self.peek() == "^":
            self.i += 1
        if self.peek() == "]":
            raise self.fail("a ] right after [ or [^ must be escaped")
        items = 0
        while True:
            c = self.peek()
            if c == "":
                raise self.fail("unclosed class")
            if c == "]":
                break
            if self.s.startswith(("&&", "--", "~~"), self.i):
                raise self.fail("&&, -- and ~~ inside a class are set operators in some languages")
            if c == "[":
                raise self.fail("a [ inside a class must be escaped")
            start_is_range_end = self.class_char()
            items += 1
            if self.peek() == "-" and self.peek(1) not in ("]", ""):
                if self.peek(1) == "-":
                    raise self.fail(
                        "&&, -- and ~~ inside a class are set operators in some languages"
                    )
                self.i += 1
                if self.peek() == "\\" and self.peek(1) in _CLASS_ESCAPES:
                    raise self.fail("a range cannot end in \\d, \\w or \\s")
                if not start_is_range_end:
                    raise self.fail("a range cannot start at \\d, \\w or \\s")
                self.class_char()
        if items == 0:
            raise self.fail("empty class")
        self.i += 1  # ]

    def class_char(self) -> bool:
        """Consume one class member; False when it is a \\d-style class
        (which cannot be a range endpoint)."""
        c = self.peek()
        if c == "\\":
            is_class = self.peek(1) in _CLASS_ESCAPES
            self.escape(in_class=True)
            return not is_class
        if not (" " <= c <= "~"):
            raise self.fail("only printable ASCII in a class; write other characters as \\xHH")
        self.i += 1
        return True
