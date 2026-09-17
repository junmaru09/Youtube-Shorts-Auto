"""The one-line-per-operation form the script model writes.

JSON operations are what the canvas takes, but a strict tool schema cannot
describe "an object whose keys depend on its op", and unstructured JSON in a
string field is where models drop quotes. So the script carries each
operation as one short line —

    arrow from=sun via=ice1.top to=up-right colour=yellow
    pie slot=center name=pie slices=普通の物質:5:white|ダークマター:27:cyan:見えないし触れない/謎の物質

— and this module turns it into the dict `Canvas.apply` wants. The grammar is
small on purpose: `op key=value …`, `|` between list items, `:` between an
item's fields, `;` between table rows, `/` for a line break inside text.
"""

from __future__ import annotations

import re
from typing import Any

from . import OPS
from . import elements as E

__all__ = ["DSLError", "parse", "parse_many"]


class DSLError(ValueError):
    """A visual line that does not parse."""


_KEY = re.compile(r"\s+(?=[a-z_]+=)")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")

# Keys whose value is a `|`-separated list, and how each item is structured.
# `str` items stay strings; a tuple names the fields split on `:`.
_LIST_KEYS: dict[str, Any] = {
    "items": str,                     # box_row; `columns` overrides below
    "columns": str,
    "elements": str,
    "labels": str,
    "images": str,
    "captions": str,
    "slices": ("label", "value", "colour", "note"),
    "bands": ("from", "to", "colour", "name"),
    "nodes": ("text", "x", "y", "colour", "size"),
    "markers": ("at", "label", "colour", "side"),
    "edges": "edge",
    "span": "numbers",
    "box": "numbers",
}
_COLUMN_ITEM = ("title", "colour", "text")
_TEXT_KEYS = {"text", "note", "label", "title", "centre_label", "x", "y"}


def _scalar(value: str) -> Any:
    if value in ("true", "false"):
        return value == "true"
    if _NUMBER.match(value):
        return float(value) if "." in value else int(value)
    return value


def _item(fields: tuple[str, ...], raw: str, key: str) -> dict[str, Any]:
    parts = raw.split(":")
    if len(parts) < 2:
        raise DSLError(f"{key} item {raw!r} needs at least two ':'-separated fields ({':'.join(fields[:2])})")
    item: dict[str, Any] = {}
    for name, part in zip(fields, parts):
        part = part.strip()
        if part == "":
            continue
        item[name] = part.replace("/", "\n") if name in ("text", "note") else _scalar(part)
    return item


def _list(key: str, raw: str, op: str) -> Any:
    shape = _COLUMN_ITEM if (key == "items" and op == "columns") else _LIST_KEYS[key]
    # a plain tuple of numbers (span=0.2:0.8, box=0.3:0.2:0.4:0.3) splits on ':'
    items = [s.strip() for s in raw.split(":" if shape == "numbers" else "|") if s.strip()]
    if shape is str:
        return [s.replace("/", "\n") for s in items]
    if shape == "edge":
        edges = []
        for s in items:
            a, _, b = s.partition("-")
            if not (a.strip().isdigit() and b.strip().isdigit()):
                raise DSLError(f"edge {s!r} must look like 0-1")
            edges.append((int(a), int(b)))
        return edges
    if shape == "numbers":
        return [_scalar(s) for s in items]
    return [_item(shape, s, key) for s in items]


# The one argument an op is usually given, so `background space`,
# `label 正のフィードバック at=top` and `background=space` all parse. The
# script model writes these forms a fair share of the time; refusing them
# only costs another round.
_PRIMARY = {"background": "name", "label": "text", "heading": "text", "title": "text",
            "list_add": "text", "highlight": "target", "strike": "target"}


def parse(line: str) -> dict[str, Any]:
    """One DSL line to one operation dict."""
    line = line.strip()
    if not line:
        raise DSLError("empty visual line")
    head, *rest = _KEY.split(line)
    op, _, first = head.partition(" ")
    op = op.strip()
    if "=" in op:
        # `background=space`: the op given as if it were a key
        op, _, positional = op.partition("=")
        if op not in _PRIMARY:
            raise DSLError(f"line must start with an operation name, got {line[:40]!r}")
        rest.insert(0, f"{_PRIMARY[op]}={positional}")
    if first.strip():
        chunk = first.strip()
        if "=" not in chunk and op in _PRIMARY:
            chunk = f"{_PRIMARY[op]}={chunk}"      # `background space`
        rest.insert(0, chunk)

    args: dict[str, Any] = {}
    for chunk in rest:
        key, sep, value = chunk.partition("=")
        if not sep:
            if op in _PRIMARY and _PRIMARY[op] not in args:
                key, value, sep = _PRIMARY[op], chunk, "="
            else:
                raise DSLError(f"expected key=value, got {chunk!r} in {line[:60]!r}")
        key, value = key.strip(), value.strip()
        if op == "background" and key == value:
            key = "name"                          # `background room=room`
        if key == "rows":
            args[key] = [[c.strip() for c in row.split("|")] for row in value.split(";") if row.strip()]
        elif key in _LIST_KEYS:
            args[key] = _list(key, value, op)
        elif key in _TEXT_KEYS:
            args[key] = value.replace("/", "\n") if key in ("text", "note") else value
        else:
            args[key] = _scalar(value)

    if op in OPS:
        return {"op": op, **args}
    if op in E.REGISTRY:
        # element name as shorthand for `place element=<name>`
        return {"op": "place", "element": op, **args}
    raise DSLError(f"unknown operation {op!r} (ops: {', '.join(OPS)}; elements: {', '.join(sorted(E.REGISTRY))})")


def parse_many(lines: list[str]) -> list[dict[str, Any]]:
    return [parse(line) for line in lines]
