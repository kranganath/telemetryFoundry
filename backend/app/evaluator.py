"""Offline dry-run evaluator for mapping expressions (no Spark needed).

Supports the subset the factory generates plus common string functions:
backtick columns, CAST(x AS type), literals, current_timestamp/date,
upper/lower/trim/concat/coalesce. Anything else yields a per-cell error
instead of guessing.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from .exporter import _col

TOKEN = re.compile(
    r"\s*(?:(?P<num>-?\d+(?:\.\d+)?)"
    r"|(?P<str>'(?:[^']|'')*')"
    r"|(?P<tick>`[^`]+`)"
    r"|(?P<ident>[A-Za-z_][A-Za-z0-9_.]*)"
    r"|(?P<op>[(),]))"
)

FUNCS = {"upper", "lower", "trim", "concat", "coalesce",
         "current_timestamp", "current_date"}


class EvalError(Exception):
    pass


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens = []
    pos = 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise EvalError(f"Cannot parse near '{text[pos:pos + 20]}'")
        pos = m.end()
        kind = m.lastgroup or "op"
        tokens.append((kind, m.group().strip()))
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], row: dict):
        self.tokens = tokens
        self.row = row
        self.pos = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ("", "")

    def next(self) -> tuple[str, str]:
        tok = self.peek()
        self.pos += 1
        return tok

    def parse(self) -> Any:
        value = self.parse_expr()
        if self.pos != len(self.tokens):
            raise EvalError("Unexpected trailing input")
        return value

    def parse_expr(self) -> Any:
        kind, text = self.peek()
        if kind == "num":
            self.next()
            return float(text) if "." in text else int(text)
        if kind == "str":
            self.next()
            return text[1:-1].replace("''", "'")
        if kind == "tick":
            self.next()
            name = text[1:-1]
            if name not in self.row:
                raise EvalError(f"Unknown column '{name}'")
            return self.row[name]
        if kind == "ident":
            upper = text.upper()
            if upper == "CAST":
                return self.parse_cast()
            if upper in ("TRUE", "FALSE"):
                self.next()
                return upper == "TRUE"
            if upper == "NULL":
                self.next()
                return None
            if text.lower() in FUNCS and self._ahead_is("("):
                return self.parse_func()
            if "." not in text and "_" not in text[:1]:
                pass
            self.next()
            if text not in self.row:
                raise EvalError(f"Unknown column '{text}'")
            return self.row[text]
        raise EvalError(f"Unexpected '{text}'")

    def _ahead_is(self, op: str) -> bool:
        return (self.pos + 1 < len(self.tokens)
                and self.tokens[self.pos + 1] == ("op", op))

    def expect(self, op: str) -> None:
        kind, text = self.next()
        if kind != "op" or text != op:
            raise EvalError(f"Expected '{op}'")

    def parse_cast(self) -> Any:
        self.next()  # CAST
        self.expect("(")
        value = self.parse_expr()
        kind, text = self.next()
        if kind != "ident" or text.upper() != "AS":
            raise EvalError("Expected AS in CAST")
        kind, target = self.next()
        if kind != "ident":
            raise EvalError("Expected target type in CAST")
        self.expect(")")
        return cast_value(value, target.upper())

    def parse_func(self) -> Any:
        _, name = self.next()
        self.expect("(")
        args = []
        if self.peek() != ("op", ")"):
            while True:
                args.append(self.parse_expr())
                if self.peek() == ("op", ","):
                    self.next()
                    continue
                break
        self.expect(")")
        return call_func(name.lower(), args)


def cast_value(value: Any, target: str) -> Any:
    if value is None or value == "":
        return None
    try:
        if target in ("STRING", "VARCHAR"):
            return str(value)
        if target in ("BIGINT", "INT", "INTEGER", "LONG"):
            return int(float(value)) if isinstance(value, str) else int(value)
        if target in ("DOUBLE", "FLOAT", "DECIMAL"):
            return float(value)
        if target == "BOOLEAN":
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("true", "t", "1", "yes"):
                    return True
                if low in ("false", "f", "0", "no"):
                    return False
                raise EvalError(f"Cannot cast '{value}' to BOOLEAN")
            return bool(value)
        if target in ("TIMESTAMP", "DATE"):
            dt = parse_time(value)
            return dt.date().isoformat() if target == "DATE" else dt.isoformat(sep=" ")
    except EvalError:
        raise
    except Exception as exc:
        raise EvalError(f"Cannot cast '{value}' to {target}") from exc
    raise EvalError(f"Unsupported cast target {target}")


def parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.split(".")[0], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise EvalError(f"Cannot parse timestamp '{value}'") from exc


def call_func(name: str, args: list[Any]) -> Any:
    if name == "current_timestamp":
        return datetime.now().isoformat(sep=" ", timespec="seconds")
    if name == "current_date":
        return date.today().isoformat()
    if name in ("upper", "lower", "trim"):
        if len(args) != 1:
            raise EvalError(f"{name} takes 1 argument")
        if args[0] is None:
            return None
        text = str(args[0])
        return text.upper() if name == "upper" else (
            text.lower() if name == "lower" else text.strip())
    if name == "concat":
        return "".join("" if a is None else str(a) for a in args)
    if name == "coalesce":
        for a in args:
            if a is not None and a != "":
                return a
        return None
    raise EvalError(f"Unsupported function {name}")


def eval_expr(text: str, row: dict) -> Any:
    return _Parser(_tokenize(text), row).parse()


def dry_run(mappings: list, rows: list[dict],
            target_types: Optional[dict[str, str]] = None,
            limit: int = 20) -> dict:
    """Evaluate mapped expressions over sample rows.

    Returns {columns, rows, errors} where rows are truncated for display
    and errors counts cells that failed to evaluate.
    """
    from .models import MappingEntry  # local import: models has no back-refs

    target_types = target_types or {}
    cols = []
    exprs = []
    for m in mappings:
        entry = m if isinstance(m, MappingEntry) else MappingEntry(**m)
        if not entry.target:
            continue
        cols.append(_col(entry.target))
        exprs.append(entry)
    out_rows: list[dict] = []
    errors = 0
    for row in rows[:limit]:
        out: dict[str, Any] = {}
        for col, entry in zip(cols, exprs):
            try:
                out[col] = render(entry, row, target_types)
            except EvalError as exc:
                out[col] = f"ERR: {exc}"
                errors += 1
        out_rows.append(out)
    return {"columns": cols, "rows": out_rows, "errors": errors,
            "truncated": len(rows) > limit}


def render(entry, row: dict, target_types: dict[str, str]) -> Any:
    """Render one mapping against one row (raises EvalError)."""
    if entry.transform == "constant":
        if not (entry.expression or "").strip():
            raise EvalError("Constant needs an expression")
        return eval_expr(entry.expression, row)
    if entry.transform == "expression":
        if not (entry.expression or "").strip():
            raise EvalError("Expression is empty")
        return eval_expr(entry.expression, row)
    if entry.transform == "cast" and (entry.expression or "").strip():
        return eval_expr(entry.expression, row)
    if entry.source not in row:
        raise EvalError(f"Unknown column '{entry.source}'")
    value = row[entry.source]
    if entry.transform == "cast":
        return cast_value(value, target_types.get(entry.target, "STRING"))
    return value


from typing import Optional  # noqa: E402
