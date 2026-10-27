"""Values from a cs1302-tracer snapshot, rendered EXACTLY as our Java
runtime renders them (`Code2ShortsTrace.repr`), plus a tiny evaluator for
side-effect-free expressions (`SourceStructure.pure`).

Exactness is the point: an event derived from a snapshot is only worth
having if its value string is the one the instrumenter would have printed.
Anything this module cannot render identically raises `ValueUnavailable`
instead of guessing - an object's `toString()` is the main case, since the
tracer does not expose JDK internals (a StringBuilder arrives with no
fields).
"""

from __future__ import annotations

from typing import Any

_PRIMITIVE_ARRAYS = {"int[]", "long[]", "short[]", "byte[]", "double[]", "float[]", "boolean[]"}
_INTEGRAL = {"int", "long", "short", "byte"}
_BOXES = {"java.lang.Integer", "java.lang.Long", "java.lang.Short", "java.lang.Byte",
          "java.lang.Character", "java.lang.Boolean", "java.lang.Double", "java.lang.Float"}


class ValueUnavailable(Exception):
    """The snapshot does not contain enough to render this value exactly."""


def java_double(value: float) -> str:
    """`String.valueOf(double)` for the values a teaching trace meets.
    Outside the range where Python's shortest repr and Java's agree, refuse."""
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueUnavailable("non-finite double")
    if value == int(value) and abs(value) < 1e7:
        return f"{value:.1f}"
    if 1e-3 <= abs(value) < 1e7:
        return repr(float(value))
    raise ValueUnavailable("double outside the exactly-reproducible range")


def render(value: Any, type_name: str, heap: dict) -> str:
    """`Code2ShortsTrace.repr(...)` of one snapshot value."""
    if value is None:
        return "null"
    if isinstance(value, dict) and "ref" in value:
        return _render_ref(value["ref"], heap)
    if type_name == "boolean" or isinstance(value, bool):
        return "true" if value else "false"
    if type_name == "char":
        return str(value)
    if type_name in ("double", "float"):
        return java_double(float(value))
    if type_name in _INTEGRAL or isinstance(value, int):
        return str(int(value))
    if isinstance(value, str):
        return value
    raise ValueUnavailable(f"cannot render {type_name}")


def _element(value: Any, element_type: str, heap: dict) -> str:
    if isinstance(value, dict) and "ref" in value:
        return _render_ref(value["ref"], heap)
    return render(value, element_type, heap)


def _render_ref(ref: int, heap: dict) -> str:
    obj = heap.get(str(ref))
    if obj is None:
        raise ValueUnavailable(f"reference {ref} not in the snapshot heap")
    kind, type_name = obj.get("kind"), obj.get("type", "")
    if kind == "string":
        return obj["value"]
    if kind == "box" and type_name in _BOXES:
        return render(obj["value"], type_name.rsplit(".", 1)[-1].lower(), heap)
    if kind == "array" and type_name.endswith("[]"):
        elements = obj.get("elements", [])
        if type_name == "char[]":
            return "".join(str(e) for e in elements)
        element_type = type_name[:-2]
        if type_name in _PRIMITIVE_ARRAYS:
            return "[" + ", ".join(render(e, element_type, heap) for e in elements) + "]"
        return "[" + ", ".join(_element(e, element_type, heap) for e in elements) + "]"
    # Collections, user objects, StringBuilder...: repr() would call
    # toString(), which the snapshot cannot reproduce.
    raise ValueUnavailable(f"toString() of {type_name} is not in the snapshot")


# ---- evaluation of SourceStructure's side-effect-free expressions -------------

INT_MIN, INT_MAX = -(2**31), 2**31 - 1


def _wrap_int(n: int) -> int:
    return (n - INT_MIN) % 2**32 + INT_MIN


def _wrap_long(n: int) -> int:
    return (n + 2**63) % 2**64 - 2**63


class Frame:
    """One stack frame's locals, typed, plus the snapshot heap."""

    def __init__(self, frame: dict, heap: dict) -> None:
        self.heap = heap
        self.locals = {item["name"]: (item["type"], item["value"]) for item in frame["locals"]}

    def has(self, name: str) -> bool:
        return name in self.locals

    def render(self, name: str) -> str:
        type_name, value = self.locals[name]
        return render(value, type_name, self.heap)


def evaluate(expr: dict | None, frame: Frame,
             call_result: tuple[str, Any] | None = None) -> tuple[str, Any]:
    """Evaluate a pure expression to (java type, value). `call_result` is the
    value of the expression's single {"t": "call"} node, when it has one.
    Raises ValueUnavailable for anything outside the supported subset."""
    if expr is None:
        raise ValueUnavailable("expression is not side-effect free")
    kind = expr["t"]
    if kind == "call":
        if call_result is None:
            raise ValueUnavailable("the call's result is not known")
        return call_result
    if kind == "int":
        return "int", int(expr["v"])
    if kind == "long":
        return "long", int(expr["v"])
    if kind == "bool":
        return "boolean", bool(expr["v"])
    if kind == "char":
        return "char", expr["v"]
    if kind == "str":
        return "java.lang.String", expr["v"]
    if kind == "name":
        if not frame.has(expr["n"]):
            raise ValueUnavailable(f"{expr['n']} is not visible in the frame")
        return frame.locals[expr["n"]]
    if kind == "len":
        _, value = evaluate({"t": "name", "n": expr["n"]}, frame)
        obj = frame.heap.get(str(value["ref"])) if isinstance(value, dict) else None
        if not obj or obj.get("kind") != "array":
            raise ValueUnavailable(f"{expr['n']}.length needs an array")
        return "int", len(obj["elements"])
    if kind == "idx":
        array_type, value = evaluate({"t": "name", "n": expr["a"]}, frame)
        index = index_of(expr["i"], frame)
        obj = frame.heap.get(str(value["ref"])) if isinstance(value, dict) else None
        if not obj or obj.get("kind") != "array" or not 0 <= index < len(obj["elements"]):
            raise ValueUnavailable("array element is not in the snapshot")
        return array_type[:-2], obj["elements"][index]
    if kind == "neg":
        type_name, value = evaluate(expr["e"], frame, call_result)
        if type_name not in _INTEGRAL:
            raise ValueUnavailable("negation of a non-integer")
        if type_name == "long":
            return "long", _wrap_long(-int(value))
        return "int", _wrap_int(-int(value))
    if kind == "bin":
        lt, lv = evaluate(expr["l"], frame, call_result)
        rt, rv = evaluate(expr["r"], frame, call_result)
        op = expr["op"]
        if op == "+" and "java.lang.String" in (lt, rt):
            return "java.lang.String", render(lv, lt, frame.heap) + render(rv, rt, frame.heap)
        # Binary numeric promotion: long if either side is long, else int.
        result_type, wrap = ("long", _wrap_long) if "long" in (lt, rt) else ("int", _wrap_int)
        lv, rv = _as_int(lt, lv), _as_int(rt, rv)
        if op == "+":
            return result_type, wrap(lv + rv)
        if op == "-":
            return result_type, wrap(lv - rv)
        if op == "*":
            return result_type, wrap(lv * rv)
        if rv == 0:
            raise ValueUnavailable("division by zero")
        quotient = abs(lv) // abs(rv) * (1 if (lv >= 0) == (rv >= 0) else -1)
        if op == "/":
            return result_type, wrap(quotient)
        return result_type, wrap(lv - quotient * rv)
    raise ValueUnavailable(f"unsupported expression {kind}")


def _as_int(type_name: str, value: Any) -> int:
    if type_name in _INTEGRAL:
        return int(value)
    if type_name == "char" and isinstance(value, str) and len(value) == 1:
        return ord(value)
    raise ValueUnavailable(f"integer arithmetic on {type_name}")


def index_of(expr: dict | None, frame: Frame) -> int:
    type_name, value = evaluate(expr, frame)
    return _as_int(type_name, value)


def render_typed(type_name: str, value: Any, heap: dict) -> str:
    return render(value, type_name, heap)
