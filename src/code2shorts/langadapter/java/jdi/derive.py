"""SnapshotDiffer: cs1302-tracer line snapshots -> the raw events our Java
runtime (`Code2ShortsTrace`) would have printed for the same run.

The tracer snapshots the world whenever execution reaches the FIRST code
location of a source line (one breakpoint per line). It never reports what
happened BETWEEN two snapshots, so every event here is derived from two
inputs only:

* the observed snapshots (frames, locals, heap) before and after a
  statement, and
* the statement structure from `SourceStructure` (JavaParser).

Nothing is guessed. Every transition between two snapshots is PREDICTED
from the structure and the prediction is CHECKED against the line the next
snapshot actually shows; a mismatch raises `DerivationError` and no trace
is produced. A value that cannot be rendered exactly fails the same way
(in strict mode). Wrong events never reach a video.

The event rules mirror `tools/java-instrumenter/.../SourceInstrumenter.java`
one for one. Three facts about where javac puts line numbers (and so where
the tracer stops) shape the prediction - all observed on the goldens and
trace samples:

* A `for` header is reached ONCE (its init). The update and the condition
  of later iterations are not the line's first location, so they never
  stop; they are derived at the back-edge.
* A `while` header IS reached on every iteration (the condition is the
  line's first location).
* When a `{...}` block that declares local variables ends, javac records
  its closing-brace line where those scopes close. That stop is only seen
  when the next instruction has no source position of its own: a `while`
  back-edge `goto`, the `goto` that skips an `else`, or (always) a void
  method's implicit `return`. When several blocks end at once, the
  OUTERMOST declaring block's brace is the one that remains.

Generic by construction: no algorithm, class or method names appear here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from code2shorts.langadapter.java.jdi.values import (
    Frame,
    ValueUnavailable,
    evaluate,
    index_of,
    render,
)


class DerivationError(Exception):
    """The snapshots cannot be turned into exact events. Carries a reason."""


@dataclass
class _Node:
    stmt: dict
    container: list
    position: int
    parent: "_Node | None"
    role: str  # "body" | "then" | "else" | "method"


@dataclass
class _Method:
    data: dict
    by_line: dict[int, _Node] = field(default_factory=dict)
    ambiguous_lines: set[int] = field(default_factory=set)
    overloaded: bool = False

    @property
    def qualified(self) -> str:
        return f"{self.data['class']}.{self.data['name']}"


def index_methods(structures: list[dict]) -> dict[tuple[str, str], _Method]:
    methods: dict[tuple[str, str], _Method] = {}
    for structure in structures:
        for data in structure["methods"]:
            key = (data["class"], data["name"])
            method = _Method(data)
            _index(method, data["body"], None, "method")
            if key in methods:
                method.overloaded = True  # frames carry names only; overloads can't be told apart
            methods[key] = method
    return methods


def _index(method: _Method, statements: list, parent: _Node | None, role: str) -> None:
    for position, stmt in enumerate(statements):
        node = _Node(stmt, statements, position, parent, role)
        line = stmt["line"]
        if line in method.by_line:
            method.ambiguous_lines.add(line)
        method.by_line[line] = node
        kind = stmt["kind"]
        if kind in ("while", "for", "block"):
            _index(method, stmt["body"], node, "body")
        elif kind == "if":
            _index(method, stmt["then"], node, "then")
            if stmt["else"] is not None:
                _index(method, stmt["else"], node, "else")


def _declares(container: list) -> bool:
    return any(s["kind"] == "decl" for s in container)


def _container_end(node: _Node, method: _Method) -> int | None:
    """Closing-brace line of the block `node` sits in, or None (no braces)."""
    if node.parent is None:
        return method.data["endLine"]
    stmt = node.parent.stmt
    if node.role == "then":
        return stmt.get("thenEnd")
    if node.role == "else":
        return stmt.get("elseEnd")
    return stmt.get("bodyEnd")


@dataclass
class _Pending:
    """A caller statement waiting for its callee to return."""

    node: _Node
    method: _Method
    before: Frame
    depth: int


class SnapshotDiffer:
    def __init__(self, structures: list[dict], harness_file: str, strict: bool = True) -> None:
        self._methods = index_methods(structures)
        self._harness = harness_file
        # strict=True (the backend): an unrenderable value fails the trace.
        # strict=False is for parity MEASUREMENT only: the value is recorded
        # as unavailable (None) and listed in `unavailable`.
        self._strict = strict
        self.unavailable: list[str] = []
        self.events: list[dict] = []
        self._loop_counter = 0
        # depth -> (brace line, continuation kind, node, method) of a predicted brace stop
        self._resume: dict[int, tuple[int, str, _Node | None, _Method]] = {}

    # ---- public -------------------------------------------------------------

    def derive(self, steps: list[dict]) -> list[dict]:
        """Raw events, in emission order, ending with PROGRAM_END. Raises
        DerivationError rather than return a partial or inexact list."""
        self.events = []
        self.unavailable = []
        self._loop_counter = 0
        self._resume = {}
        self._emit("PROGRAM_START", 0, description="program started")
        pending: dict[int, _Pending] = {}
        for index, current in enumerate(steps):
            frames = self.user_frames(current)
            if not frames:
                continue  # the harness, before main starts or after it returns
            following = steps[index + 1] if index + 1 < len(steps) else None
            if following is None:
                # No later snapshot: the run stopped inside this statement
                # (an uncaught exception or a tracer limit). It did not
                # complete, so it contributes no events.
                break
            depth = len(frames) - 1
            method = self._method(frames[-1])
            line = frames[-1]["line"]
            next_frames = self.user_frames(following)
            next_line = next_frames[-1]["line"] if next_frames else None

            resume = self._resume.pop(depth, None)
            if resume is not None:
                if resume[0] != line or line in method.by_line:
                    raise DerivationError(f"a stop on brace line {resume[0]} was predicted but "
                                          f"the tracer stopped on line {line}")
                exit_event = self._after_brace(resume, depth, frames, next_frames, following)
                if len(next_frames) < len(frames):
                    self._return_to_caller(pending, exit_event, depth, following, next_frames)
                continue

            node = self._node(method, line)
            before = Frame(frames[-1], current["heap"])
            if len(next_frames) > len(frames):
                # A call into user code: the statement completes after it returns.
                callee = self._method(next_frames[-1])
                self._emit("METHOD_ENTER", depth + 1, line_number=callee.data["declLine"],
                           method=callee.qualified, description=f"enter {callee.qualified}")
                pending[depth] = _Pending(node, method, before, depth)
                continue

            exit_event = None
            if node.stmt["kind"] == "return":
                if not method.data["isMain"]:  # main's exit is PROGRAM_END
                    exit_event = self._emit_return(node, method, before, depth)
                if len(next_frames) >= len(frames):
                    raise DerivationError(f"line {line}: a return did not end the frame")
            elif len(next_frames) == len(frames):
                after = Frame(next_frames[-1], following["heap"])
                self._emit_statement(node, before, after, depth)
                self._flow(node, method, after, depth, next_line)
            else:
                # The frame ended right after this statement with no stop on
                # the method's brace (the brace shares the statement's line).
                self._require_frame_end(node, line)
                self._emit_statement(node, before, None, depth)
                exit_event = self._void_exit(method, depth)
            if len(next_frames) < len(frames):
                self._return_to_caller(pending, exit_event, depth, following, next_frames)
        self._emit("PROGRAM_END", 0, description="program ended")
        for event in self.events:
            event.pop("_needs_value", None)
            event.pop("_typed", None)
        return self.events

    def user_frames(self, step: dict | None) -> list:
        if step is None:
            return []
        return [f for f in step["callStack"]
                if PurePath(f["file"].replace("\\", "/")).name != self._harness]

    def ended_inside_user_code(self, steps: list[dict]) -> bool:
        """True when the last snapshot is still inside the program: the run
        did not return to the harness (exception, limit or timeout)."""
        return bool(steps) and bool(self.user_frames(steps[-1]))

    # ---- statements (SourceInstrumenter's emit points) ------------------------

    def _emit_statement(self, node: _Node, before: Frame, after: Frame | None, depth: int) -> None:
        stmt, line = node.stmt, node.stmt["line"]
        kind = stmt["kind"]
        try:
            if kind == "decl":
                for var in stmt["vars"]:
                    if not var["hasInit"]:
                        continue
                    if _is_collection(var["type"]):
                        raise DerivationError(
                            f"line {line}: collection declarations (COLLECTION_MUTATION) "
                            "are not derived by the JDI backend")
                    if var["initRead"] is not None:
                        self._emit_read(var["initRead"], before, depth, line)
                    else:
                        value = self._value_or_gap(after, var["name"], line)
                        self._emit_assign(var["name"], None, value, depth, line)
            elif kind == "assign":
                target = stmt["target"]
                if target["kind"] == "array":
                    if stmt["valueRead"] is not None:
                        self._emit_read(stmt["valueRead"], before, depth, line)
                    self._emit_write(target["access"], before, after, depth, line)
                elif target["kind"] == "name":
                    name = target["name"]
                    old = before.render(name) if before.has(name) else None
                    self._emit_assign(name, old, self._value_or_gap(after, name, line), depth, line)
            elif kind == "incdec":
                name = stmt["name"]
                self._emit_assign(name, before.render(name), self._value_or_gap(after, name, line),
                                  depth, line)
        except ValueUnavailable as error:
            raise DerivationError(f"line {line}: {error}") from error

    def _value_or_gap(self, after: Frame | None, name: str, line: int) -> str | None:
        if after is None or not after.has(name):
            raise DerivationError(f"line {line}: the value of {name} after the statement "
                                  "is not observable (its frame has ended)")
        try:
            return after.render(name)
        except ValueUnavailable as error:
            if self._strict:
                raise
            self.unavailable.append(f"line {line}: {name}: {error}")
            return None

    def _emit_read(self, access: dict, frame: Frame, depth: int, line: int) -> None:
        index = index_of(access["index"], frame)
        element_type, value = evaluate(
            {"t": "idx", "a": access["array"], "i": {"t": "int", "v": index}}, frame)
        text = render(value, element_type, frame.heap)
        name = f"{access['array']}[{index}]"
        self._emit("ARRAY_READ", depth, line_number=line, variable_name=name,
                   new_value=text, description=f"{name} -> {text}")

    def _emit_write(self, access: dict | None, before: Frame, after: Frame | None, depth: int,
                    line: int) -> None:
        if access is None:
            raise DerivationError(f"line {line}: array target is not a plain name")
        if after is None:
            raise DerivationError(f"line {line}: array state after the write is not observable")
        index = index_of(access["index"], before)
        element_type, value = evaluate(
            {"t": "idx", "a": access["array"], "i": {"t": "int", "v": index}}, after)
        text = render(value, element_type, after.heap)
        name = f"{access['array']}[{index}]"
        self._emit("ARRAY_WRITE", depth, line_number=line, variable_name=name,
                   new_value=text, description=f"{name} = {text}")

    def _emit_assign(self, name: str, old: str | None, new: str | None, depth: int,
                     line: int) -> None:
        fields: dict[str, Any] = {"line_number": line, "variable_name": name}
        if old is not None:
            fields["old_value"] = old
        fields["new_value"] = new
        self._emit("VARIABLE_ASSIGN", depth, **fields, description=f"{name} = {new}")

    def _emit_return(self, node: _Node, method: _Method, before: Frame, depth: int,
                     call_result: tuple[str, Any] | None = None) -> dict:
        """METHOD_EXIT for a `return`. Its value is the return expression
        evaluated on the observed state: a pure expression directly, or one
        with a single call (`n * f(n - 1)`) using that call's own derived
        return value. Otherwise it is resolved later from what the caller
        receives, or stays absent."""
        stmt = node.stmt
        fields: dict[str, Any] = {"line_number": stmt["line"], "method": method.qualified}
        typed = None
        expression = stmt["value"] if stmt["value"] is not None else (
            stmt.get("valueWithCall") if call_result is not None else None)
        if stmt["hasValue"] and expression is not None:
            try:
                typed = evaluate(expression, before, call_result)
                fields["return_value"] = render(typed[1], typed[0], before.heap)
            except ValueUnavailable:
                typed = None  # resolved from the caller's observation below, or left absent
        event = self._emit("METHOD_EXIT", depth, **fields, description=f"exit {method.qualified}")
        event["_needs_value"] = bool(stmt["hasValue"]) and "return_value" not in event
        event["_typed"] = typed
        return event

    def _void_exit(self, method: _Method, depth: int) -> dict | None:
        if method.data["isMain"]:
            return None
        if not method.data["isVoid"]:
            raise DerivationError(f"{method.qualified} ended without a return")
        return self._emit("METHOD_EXIT", depth, line_number=method.data["declLine"],
                          method=method.qualified, description=f"exit {method.qualified}")

    def _return_to_caller(self, pending: dict[int, _Pending], exit_event: dict | None,
                          depth: int, following: dict, next_frames: list) -> None:
        # Frames between the one that returned and the observed caller ended
        # without a stop of their own: each must have been suspended in a
        # `return` containing the call (`return n * f(n - 1)`), which then
        # completes with the callee's value. Anything else is refused.
        level = depth - 1
        while level >= len(next_frames):
            waiting = pending.pop(level, None)
            if waiting is None or waiting.node.stmt["kind"] != "return" or waiting.method.data["isMain"]:
                raise DerivationError("more than one frame ended between two snapshots, "
                                      "and not through a `return` of the call")
            callee_value = exit_event.get("_typed") if exit_event is not None else None
            if exit_event is not None:
                exit_event.pop("_needs_value", None)
            exit_event = self._emit_return(waiting.node, waiting.method, waiting.before, level,
                                           call_result=callee_value)
            level -= 1
        if exit_event is not None:
            needs_value = exit_event.pop("_needs_value", False)
        else:
            needs_value = False
        caller_depth = len(next_frames) - 1
        if caller_depth < 0:
            return  # main finished; only the harness frame remains
        waiting = pending.pop(caller_depth, None)
        if waiting is None:
            raise DerivationError(f"return to depth {caller_depth} with no pending call")
        after = Frame(next_frames[-1], following["heap"])
        if needs_value:
            # `x = callee(...)`: what the caller received IS the returned value.
            received = _received_value(waiting.node.stmt)
            if received is not None and after.has(received):
                try:
                    value = after.render(received)
                    description = exit_event.pop("description")
                    exit_event["return_value"] = value
                    exit_event["description"] = description
                except ValueUnavailable:
                    pass  # stays absent: reported as a missing return value
        self._emit_statement(waiting.node, waiting.before, after, waiting.depth)
        self._flow(waiting.node, waiting.method, after, waiting.depth, next_frames[-1]["line"])

    # ---- control flow -------------------------------------------------------------

    def _flow(self, node: _Node, method: _Method, after: Frame, depth: int, target: int) -> None:
        """From `node` (just executed) to the next observed line: emit the
        condition / loop events crossed, and require the walk to land
        exactly on `target`."""
        kind, line = node.stmt["kind"], node.stmt["line"]
        if kind in ("if", "while", "for"):
            body = node.stmt["then"] if kind == "if" else node.stmt["body"]
            if not body:
                raise DerivationError(f"line {line}: empty body; the branch is not observable")
            if kind == "for" and node.stmt["cond"] is None:
                self._enter_iteration(node, after, depth)
                return self._expect(_entry(body[0]), target, line)
            taken = self._decide(node, target, line)
            self._condition(node, taken, depth)
            if taken:
                if kind in ("while", "for"):
                    self._enter_iteration(node, after, depth)
                return None
            if kind == "if" and node.stmt["else"]:
                return None  # _decide already checked the else entry
            return self._continue_after(node, method, after, depth, target)
        if kind == "break":
            return self._continue_after(self._innermost_loop(node, line), method, after, depth,
                                        target)
        if kind == "continue":
            loop = self._innermost_loop(node, line)
            if loop.stmt["kind"] == "while":
                return self._expect(loop.stmt["line"], target, line)
            return self._for_back_edge(loop, method, after, depth, target)
        return self._continue_after(node, method, after, depth, target)

    def _continue_after(self, node: _Node, method: _Method, after: Frame, depth: int,
                        target: int) -> None:
        """Fall through out of `node`, collecting the blocks that end, until
        control reaches a statement, a loop back-edge or the method end."""
        current, ended = node, []
        while True:
            if current.position + 1 < len(current.container):
                # The next statement has its own source position: no brace stop.
                return self._expect(_entry(current.container[current.position + 1]), target,
                                    current.stmt["line"])
            ended.append(current)
            parent = current.parent
            if parent is None:
                # The implicit return always sits on the method's brace.
                return self._stop_on_brace(method.data["endLine"], "frame_end", None, method,
                                           depth, target, current.stmt["line"])
            kind = parent.stmt["kind"]
            if kind == "for" and current.role == "body":
                # The update has its own position, so it overrides any brace.
                return self._for_back_edge(parent, method, after, depth, target)
            if kind == "while" and current.role == "body":
                brace = self._outermost_declaring_brace(ended, method)
                if brace is None:
                    return self._expect(parent.stmt["line"], target, current.stmt["line"])
                return self._stop_on_brace(brace, "header", parent, method, depth, target,
                                           current.stmt["line"])
            if kind == "if" and current.role == "then" and parent.stmt["else"]:
                brace = self._outermost_declaring_brace(ended, method)
                if brace is not None:
                    return self._stop_on_brace(brace, "after", parent, method, depth, target,
                                               current.stmt["line"])
            current = parent

    def _outermost_declaring_brace(self, ended: list[_Node], method: _Method) -> int | None:
        brace = None
        for node in ended:
            end = _container_end(node, method)
            if end is not None and _declares(node.container):
                brace = end
        if brace is not None and brace in method.by_line:
            raise DerivationError(f"brace line {brace} also starts a statement")
        return brace

    def _stop_on_brace(self, brace: int, kind: str, node: _Node | None, method: _Method,
                       depth: int, target: int, line: int) -> None:
        if brace in method.by_line:
            raise DerivationError(f"brace line {brace} also starts a statement")
        self._expect(brace, target, line)
        self._resume[depth] = (brace, kind, node, method)

    def _after_brace(self, resume: tuple, depth: int, frames: list, next_frames: list,
                     following: dict) -> dict | None:
        """A stop on a closing brace executes no statement; only where it
        leads is checked (and, at a method's end, its exit is emitted)."""
        brace, kind, node, method = resume
        if kind == "frame_end":
            if len(next_frames) >= len(frames):
                raise DerivationError(f"line {brace}: the method's closing brace must end the frame")
            return self._void_exit(method, depth)
        if len(next_frames) != len(frames):
            raise DerivationError(f"line {brace}: a block's closing brace changed the frame")
        target = next_frames[-1]["line"]
        if kind == "header":
            self._expect(node.stmt["line"], target, brace)
            return None
        after = Frame(next_frames[-1], following["heap"])
        self._continue_after(node, method, after, depth, target)
        return None

    def _for_back_edge(self, loop: _Node, method: _Method, after: Frame, depth: int,
                       target: int) -> None:
        body = loop.stmt["body"]
        if loop.stmt["cond"] is None:
            self._enter_iteration(loop, after, depth)
            return self._expect(_entry(body[0]), target, loop.stmt["line"])
        taken = target == _entry(body[0])
        self._condition(loop, taken, depth)
        if taken:
            self._enter_iteration(loop, after, depth)
            return None
        return self._continue_after(loop, method, after, depth, target)

    def _condition(self, node: _Node, taken: bool, depth: int) -> None:
        self._emit("CONDITION_EVALUATED", depth, line_number=node.stmt["line"],
                   condition_result=taken,
                   description=f"{node.stmt['cond']} -> {'true' if taken else 'false'}")

    def _decide(self, node: _Node, target: int, line: int) -> bool:
        """True iff control entered the branch/body. When the false path is
        another statement (an `else`), the target must be exactly it."""
        stmt = node.stmt
        body = stmt["then"] if stmt["kind"] == "if" else stmt["body"]
        if target == _entry(body[0]):
            return True
        if stmt["kind"] == "if" and stmt["else"]:
            self._expect(_entry(stmt["else"][0]), target, line)
        return False

    def _enter_iteration(self, loop: _Node, after: Frame, depth: int) -> None:
        self._loop_counter += 1
        line = loop.stmt["line"]
        self._emit("LOOP_ITERATION", depth, line_number=line, iteration=self._loop_counter,
                   description=f"iteration {self._loop_counter}")
        for name in loop.stmt.get("counters", []):
            if not after.has(name):
                raise DerivationError(f"line {line}: loop counter {name} not observable")
            try:
                value = after.render(name)
            except ValueUnavailable as error:
                raise DerivationError(f"line {line}: {error}") from error
            self._emit_assign(name, None, value, depth, line)

    def _innermost_loop(self, node: _Node, line: int) -> _Node:
        if node.stmt.get("labelled"):
            raise DerivationError(f"line {line}: labelled break/continue is not derived")
        current = node.parent
        while current is not None and current.stmt["kind"] not in ("for", "while"):
            current = current.parent
        if current is None:
            raise DerivationError(f"line {line}: break/continue outside a loop")
        return current

    def _expect(self, predicted: int | None, target: int, line: int) -> None:
        if predicted != target:
            raise DerivationError(f"after line {line} the structure predicts line {predicted} "
                                  f"but the tracer observed line {target}")

    def _require_frame_end(self, node: _Node, line: int) -> None:
        if node.stmt["kind"] in ("if", "while", "for", "break", "continue"):
            raise DerivationError(f"line {line}: frame ended inside a control statement")
        current = node
        while current is not None:
            if current.position + 1 < len(current.container) or (
                    current.parent is not None
                    and current.parent.stmt["kind"] in ("for", "while")):
                raise DerivationError(f"line {line}: frame ended before the method's end "
                                      "(exception or exit), which is not derived")
            current = current.parent

    # ---- bookkeeping -----------------------------------------------------------

    def _method(self, frame: dict) -> _Method:
        class_name = PurePath(frame["file"].replace("\\", "/")).stem
        method = self._methods.get((class_name, frame["methodName"]))
        if method is None:
            raise DerivationError(f"no structure for {class_name}.{frame['methodName']}")
        if method.overloaded:
            raise DerivationError(f"{method.qualified} is overloaded; frames cannot be matched")
        return method

    def _node(self, method: _Method, line: int) -> _Node:
        if line in method.ambiguous_lines:
            raise DerivationError(f"line {line} holds more than one statement")
        node = method.by_line.get(line)
        if node is None:
            raise DerivationError(f"line {line} of {method.qualified} starts no statement "
                                  "(multi-line statements are not derived)")
        return node

    def _emit(self, event_type: str, depth: int, **fields: Any) -> dict:
        event: dict[str, Any] = {"event_type": event_type}
        if "line_number" in fields:
            event["line_number"] = fields.pop("line_number")
        event["call_depth"] = depth
        event.update(fields)
        self.events.append(event)
        return event


def _entry(stmt: dict) -> int:
    if stmt["kind"] == "block":
        if not stmt["body"]:
            raise DerivationError(f"line {stmt['line']}: empty block")
        return _entry(stmt["body"][0])
    return stmt["line"]


def _received_value(stmt: dict) -> str | None:
    if stmt["kind"] == "decl" and len(stmt["vars"]) == 1 and stmt["vars"][0]["initIsCall"]:
        return stmt["vars"][0]["name"]
    if stmt["kind"] == "assign" and stmt["target"]["kind"] == "name" and stmt["valueIsCall"]:
        return stmt["target"]["name"]
    return None


COLLECTION_TYPES = ("Map", "HashMap", "LinkedHashMap", "TreeMap", "Deque", "ArrayDeque", "Queue",
                    "Stack", "List", "ArrayList", "LinkedList", "Set", "HashSet")


def _is_collection(type_name: str) -> bool:
    return type_name.split("<", 1)[0].strip() in COLLECTION_TYPES
