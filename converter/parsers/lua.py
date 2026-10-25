"""Parse the constant subset of Lua without loading a Lua runtime.

Tables use dictionaries, including Lua's one-based array keys. No input code is
executed. Known numeric helpers are implemented here as pure Python functions.
Unknown statements and calls are errors, including code after the return value.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from converter.parsers import transf_math, vehicleutil


class LuaParseError(ValueError):
    def __init__(self, message, filename="<string>", line=1, column=1):
        self.filename, self.line, self.column = filename, line, column
        super().__init__(f"{filename}:{line}:{column}: {message}")


@dataclass(frozen=True)
class _Token:
    kind: str
    value: object
    offset: int


@dataclass(frozen=True)
class _Module:
    name: str


_NUMBER = re.compile(r"(?:0[xX][0-9a-fA-F]+(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?\d+)?|(?:\d+(?:\.(?!\.)\d*)?|\.\d+)(?:[eE][+-]?\d+)?)")
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_LONG = re.compile(r"\[(=*)\[")
_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}
# math.* helpers that are evaluated while reading constant resources: name -> (function, min args, max args)
_MATH_FUNCTIONS = {
    "math.pow": (lambda a, b: a ** b, 2, 2), "math.max": (max, 1, 64), "math.min": (min, 1, 64),
    "math.log": (lambda a, b=None: math.log(a) if b is None else math.log(a, b), 1, 2), "math.exp": (math.exp, 1, 1),
    "math.atan": (lambda a, b=1.0: math.atan2(a, b), 1, 2), "math.atan2": (math.atan2, 2, 2),
    "math.asin": (math.asin, 1, 1), "math.acos": (math.acos, 1, 1), "math.fmod": (math.fmod, 2, 2),
    "math.sinh": (math.sinh, 1, 1), "math.cosh": (math.cosh, 1, 1), "math.tanh": (math.tanh, 1, 1),
    "math.log10": (math.log10, 1, 1),
}
MAX_TOKENS = 500_000
MAX_NUMBER_CHARS = 128
MAX_INTEGER_BITS = 4096


class _Parser:
    def __init__(self, text, filename, metadata=False, undefined=None, lex_only=False):
        # When a list is given, a plain undefined global evaluates to nil (as in Lua) and is recorded there.
        self.undefined = undefined
        self.lex_only = lex_only
        self.text, self.filename, self.metadata = text.lstrip("\ufeff"), filename, metadata
        self.tokens = []
        self.env = {"math": _Module("math")}
        self.index, self.depth = 0, 0
        if len(text) > 32 * 1024 * 1024:
            self.error("Lua resource exceeds the 32 MiB limit", offset=0)
        self.lex()

    def error(self, message, token=None, offset=None):
        pos = offset if offset is not None else (token or self.peek()).offset
        line = self.text.count("\n", 0, pos) + 1
        col = pos - self.text.rfind("\n", 0, pos)
        raise LuaParseError(message, self.filename, line, col)

    def lex(self):
        src, n, i = self.text, len(self.text), 0
        while i < n:
            if len(self.tokens) >= MAX_TOKENS:
                self.error("Lua token count exceeds 500000", offset=i)
            if src[i].isspace():
                i += 1
                continue
            if src.startswith("--", i):
                match = _LONG.match(src, i + 2)
                if match:
                    closing = "]" + match[1] + "]"
                    end = src.find(closing, match.end())
                    if end < 0:
                        self.error("Unterminated long comment", offset=i)
                    i = end + len(closing)
                else:
                    end = src.find("\n", i)
                    i = n if end < 0 else end
                continue
            start = i
            match = _LONG.match(src, i)
            if match:
                closing = "]" + match[1] + "]"
                end = src.find(closing, match.end())
                if end < 0:
                    self.error("Unterminated long string", offset=i)
                value = src[match.end():end].replace("\r\n", "\n").replace("\r", "\n")
                if value.startswith("\n"):
                    value = value[1:]
                i = end + len(closing)
                self.tokens.append(_Token("string", value, start))
            elif src[i] in "\"'":
                quote, chars = src[i], []
                i += 1
                while i < n and src[i] != quote:
                    if src[i] in "\r\n":
                        self.error("Unescaped newline in string", offset=i)
                    if src[i] != "\\":
                        chars.append(src[i]); i += 1
                        continue
                    i += 1
                    if i >= n:
                        self.error("Unterminated escape", offset=start)
                    c = src[i]
                    mapping = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "\"": "\"", "'": "'"}
                    if c in mapping:
                        chars.append(mapping[c]); i += 1
                    elif c in "\r\n":
                        chars.append("\n"); i += 1
                        if c == "\r" and i < n and src[i] == "\n":
                            i += 1
                    elif c == "z":
                        i += 1
                        while i < n and src[i].isspace():
                            i += 1
                    elif c.isdigit():
                        match = re.match(r"[0-9]{1,3}", src[i:])
                        value = int(match[0])
                        if value > 255:
                            self.error("Decimal string escape exceeds 255", offset=i)
                        chars.append(chr(value)); i += len(match[0])
                    elif c == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", src[i + 1:i + 3]):
                        chars.append(chr(int(src[i + 1:i + 3], 16))); i += 3
                    elif c == "u":
                        match = re.match(r"u\{([0-9a-fA-F]+)\}", src[i:])
                        if not match or int(match[1], 16) > 0x10FFFF:
                            self.error("Invalid Unicode escape", offset=i)
                        chars.append(chr(int(match[1], 16))); i += len(match[0])
                    else:
                        self.error("Unsupported string escape", offset=i)
                if i >= n:
                    self.error("Unterminated string", offset=start)
                i += 1
                self.tokens.append(_Token("string", "".join(chars), start))
            elif (match := _NUMBER.match(src, i)):
                literal = match[0]
                if len(literal) > MAX_NUMBER_CHARS:
                    self.error("Numeric literal exceeds 128 characters", offset=i)
                if self.lex_only:
                    # Reference discovery needs strings, not numeric evaluation.
                    self.tokens.append(_Token("number", literal, i)); i = match.end()
                    continue
                try:
                    value = float.fromhex(literal) if literal.lower().startswith("0x") and ("." in literal or "p" in literal.lower()) else int(literal, 16) if literal.lower().startswith("0x") else float(literal) if any(c in literal for c in ".eE") else int(literal)
                except (ValueError, OverflowError):
                    self.error("Invalid number", offset=i)
                if isinstance(value, float) and not math.isfinite(value):
                    self.error("Non-finite number", offset=i)
                self.tokens.append(_Token("number", value, i)); i = match.end()
            elif (match := _NAME.match(src, i)):
                self.tokens.append(_Token("name", match[0], i)); i = match.end()
            else:
                op = next((s for s in ("...", "..", "//", "==", "~=", "<=", ">=") if src.startswith(s, i)), src[i])
                if op not in {"...", "..", "//", "==", "~=", "<=", ">=", "{", "}", "[", "]", "(", ")", ",", ";", "=", ".", "+", "-", "*", "/", "%", "^", "#", "<", ">", ":"}:
                    self.error(f"Unsupported character {op!r}", offset=i)
                self.tokens.append(_Token("symbol", op, i)); i += len(op)
        self.tokens.append(_Token("eof", "<eof>", n))

    def peek(self, ahead=0):
        return self.tokens[min(self.index + ahead, len(self.tokens) - 1)]

    def take(self):
        token = self.peek(); self.index += 1
        return token

    def accept(self, value):
        if self.peek().value == value:
            return self.take()
        return None

    def expect(self, value):
        token = self.take()
        if token.value != value:
            self.error(f"Expected {value!r}, found {token.value!r}", token)
        return token

    MAX_LOOP_ITERATIONS = 100_000

    def locals(self):
        """Top-of-resource statements: constants, bounded numeric `for` loops and table element assignments."""
        while True:
            if self.peek().value == "for":
                self.for_statement()
            elif self.peek().kind == "name" and self.peek().value not in _KEYWORDS and self.peek(1).value == "[":
                self.assignment()
            elif self.peek().value == "local":
                self.local_statement()
            else:
                break

    def assignment(self):
        target = self.take()
        table = self.env.get(target.value)
        if not isinstance(table, dict):
            self.error(f"Assignment to {target.value!r}, which is not a table constant", target)
        self.expect("[")
        key = self.expression()
        self.expect("]")
        self.expect("=")
        value = self.expression()
        if type(key) not in (int, float, str):
            self.error("Unsupported table key", target)
        if value is None:
            table.pop(key, None)
        else:
            table[key] = value
        self.accept(";")

    def block_end(self, start):
        """Token index of the `end` that closes a block whose body begins at `start`."""
        blocks, index = 1, start
        while True:
            token = self.tokens[index]
            if token.kind == "eof":
                self.error("Unterminated block", token)
            if token.kind == "name" and token.value in {"function", "if", "do", "repeat"}:
                blocks += 1
            elif token.kind == "name" and token.value in {"end", "until"}:
                blocks -= 1
                if blocks == 0:
                    return index
            index += 1

    def for_statement(self):
        keyword = self.expect("for")
        variable = self.take()
        if variable.kind != "name" or variable.value in _KEYWORDS:
            self.error("Expected loop variable", variable)
        self.expect("=")
        start = self.number(self.expression(), keyword)
        self.expect(",")
        stop = self.number(self.expression(), keyword)
        step = self.number(self.expression(), keyword) if self.accept(",") else 1
        if not step:
            self.error("Loop step of zero", keyword)
        self.expect("do")
        body, end = self.index, self.block_end(self.index)
        count = max(0, int((stop - start) // step) + 1) if (step > 0) == (stop >= start) or stop == start else 0
        self.loop_iterations = getattr(self, "loop_iterations", 0) + count
        if self.loop_iterations > self.MAX_LOOP_ITERATIONS:
            self.error("Loop evaluation exceeds the iteration limit", keyword)
        saved = self.env.get(variable.value, None)
        for i in range(count):
            self.env[variable.value] = start + i * step
            self.index = body
            self.locals()
            if self.index != end:
                self.error("Only table assignments and nested loops are supported inside loops", self.peek())
        if saved is None:
            self.env.pop(variable.value, None)
        else:
            self.env[variable.value] = saved
        self.index = end
        self.expect("end")

    def local_statement(self):
        while self.accept("local"):
            name = self.take()
            if name.kind != "name" or name.value in _KEYWORDS:
                self.error("Expected local constant name", name)
            self.expect("=")
            if self.accept("require"):
                parenthesized = bool(self.accept("("))
                module = self.take()
                if module.kind != "string" or module.value not in {"transf", "vec3", "vec2", "math", "vehicleutil"}:
                    self.error("Only declarative transf, vec2, vec3, vehicleutil and math imports are supported", module)
                self.env[name.value] = _Module(module.value)
                if parenthesized:
                    self.expect(")")
            else:
                self.env[name.value] = self.expression()
            self.accept(";")

    def skip_undefined_if(self):
        """Skip `if <undefined global> ... then ... end` at the top level: in TPF2 the global is nil, so it never runs."""
        while self.peek().value == "if" and self.peek(1).kind == "name" and self.peek(1).value not in self.env \
                and self.peek(1).value not in _KEYWORDS:
            blocks = 0
            while True:
                token = self.take()
                if token.kind == "eof":
                    self.error("Unterminated if block", token)
                if token.kind == "name" and token.value in {"if", "function", "do"}:
                    blocks += 1
                elif token.kind == "name" and token.value == "repeat":
                    blocks += 1
                elif token.kind == "name" and token.value in {"end", "until"}:
                    blocks -= 1
                    if blocks == 0:
                        break
            self.accept(";")
            self.locals()

    def document(self):
        if self.lex_only:
            raise ValueError("A token-only scan cannot parse a Lua document")
        self.locals()
        self.skip_undefined_if()
        wrapped = bool(self.accept("function"))
        if wrapped:
            self.expect("data"); self.expect("("); self.expect(")")
            self.locals()
            self.expect("return")
        else:
            self.accept("return")
        value = self.expression()
        self.accept(";")
        if wrapped:
            self.expect("end")
        self.accept(";")
        # a file may define data() more than once; in Lua the last definition is the one that runs
        while wrapped and self.peek().value == "function" and self.peek(1).value == "data":
            self.take(); self.expect("data"); self.expect("("); self.expect(")")
            self.locals()
            self.expect("return")
            value = self.expression()
            self.accept(";")
            self.expect("end")
            self.accept(";")
        if self.peek().kind != "eof":
            self.error("Dynamic or trailing Lua code is unsupported")
        if not isinstance(value, dict):
            self.error("Resource must return a table")
        return value

    def expression(self, min_bp=0):
        self.depth += 1
        if self.depth > 150:
            self.error("Expression nesting exceeds 150")
        try:
            token = self.take()
            if token.kind in {"number", "string"}:
                left = token.value
            elif token.value in {"true", "false", "nil"}:
                left = {"true": True, "false": False, "nil": None}[token.value]
            elif token.value == "{":
                left = self.table()
            elif token.value == "(":
                left = self.expression(); self.expect(")")
            elif token.value == "#":
                operand = self.expression(8)
                if not isinstance(operand, dict):
                    self.error("Length of a non-table", token)
                left = 0
                while (left + 1) in operand:
                    left += 1
            elif token.value in {"-", "+"}:
                left = self.number(self.expression(7), token)
                if token.value == "-":
                    left = -left
            elif token.value == "function":
                left = self.function_value(token)
            elif token.kind == "name":
                name = token.value
                while self.accept("."):
                    part = self.take()
                    if part.kind != "name":
                        self.error("Expected member name", part)
                    name += "." + part.value
                if self.accept("("):
                    args = []
                    if not self.accept(")"):
                        args.append(self.expression())
                        while self.accept(","):
                            args.append(self.expression())
                        self.expect(")")
                    left = self.call(name, args, token)
                elif name == "math.pi":
                    left = math.pi
                elif name in self.env and not isinstance(self.env[name], _Module):
                    left = self.env[name]
                elif self.undefined is not None and "." not in name and name not in self.env:
                    self.undefined.append((name, self.text.count("\n", 0, token.offset) + 1))
                    left = None
                else:
                    self.error(f"Unknown constant or unsupported dynamic name {name!r}", token)
            else:
                self.error(f"Unsupported expression {token.value!r}", token)
            bindings = {"..": (4, 4), "+": (5, 6), "-": (5, 6), "*": (6, 7), "/": (6, 7), "//": (6, 7), "%": (6, 7), "^": (8, 8)}
            while self.peek().value in bindings:
                op_token = self.peek(); op = op_token.value
                lb, rb = bindings[op]
                if lb < min_bp:
                    break
                self.take(); right = self.expression(rb)
                try:
                    if op == "..":
                        if type(left) not in (str, int, float) or type(right) not in (str, int, float):
                            self.error("Concatenation requires strings or numbers", op_token)
                        left = str(left) + str(right)
                        if len(left) > 32 * 1024 * 1024:
                            self.error("Concatenated string exceeds limit", op_token)
                    else:
                        a, b = self.number(left, op_token), self.number(right, op_token)
                        if op == "^" and (abs(b) > 1024 or abs(a) > 1e100):
                            self.error("Exponent exceeds safe evaluation limit", op_token)
                        left = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a / b, "//": lambda: a // b, "%": lambda: a % b, "^": lambda: a ** b}[op]()
                        self.number(left, op_token)
                except (ArithmeticError, ValueError, TypeError) as exc:
                    self.error(f"Invalid arithmetic: {exc}", op_token)
            return left
        finally:
            self.depth -= 1

    def number(self, value, token):
        if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
            self.error("Expected a finite numeric constant", token)
        if isinstance(value, int) and value.bit_length() > MAX_INTEGER_BITS:
            self.error("Integer exceeds 4096-bit evaluation limit", token)
        return value

    def table(self):
        result, next_index = {}, 1
        while not self.accept("}"):
            if self.accept("["):
                key = self.expression(); self.expect("]"); self.expect("=")
            elif self.peek().kind == "name" and self.peek(1).value == "=":
                key = self.take().value; self.take()
            else:
                key, next_index = next_index, next_index + 1
            value = self.expression()
            if type(key) not in (int, float, str) or key is None:
                self.error("Unsupported table key")
            if value is None:
                result.pop(key, None)
            else:
                result[key] = value
            if self.peek().value != "}":
                if not (self.accept(",") or self.accept(";")):
                    self.error("Expected table field separator")
        return result

    def function_value(self, start):
        self.expect("(")
        while not self.accept(")"):
            token = self.take()
            if token.kind == "eof":
                self.error("Unterminated function", start)
            if token.kind != "name" and token.value not in {",", "..."}:
                self.error("Invalid function parameter", token)
        body_start = self.peek().offset
        if self.accept("end"):
            return {"__empty_function__": True}
        if not self.metadata:
            self.error("Non-empty function requires manual conversion; it is never executed", start)
        blocks = 1
        while blocks:
            token = self.take()
            if token.kind == "eof":
                self.error("Unterminated function", start)
            if token.kind == "name" and token.value in {"function", "if", "do", "repeat"}:
                blocks += 1
            elif token.kind == "name" and token.value in {"end", "until"}:
                blocks -= 1
        return {"__lua_function__": self.text[start.offset:token.offset + 3], "__source_line__": self.text.count("\n", 0, body_start) + 1}

    def call(self, name, args, token):
        root, _, member = name.partition(".")
        if root in self.env and isinstance(self.env[root], _Module):
            name = self.env[root].name + ("." + member if member else "")
        try:
            if name == "_" and len(args) == 1 and (isinstance(args[0], str) or args[0] is None):
                return args[0]
            if name in {"math.rad", "math.deg", "math.sin", "math.cos", "math.tan", "math.abs", "math.sqrt", "math.floor", "math.ceil"} and len(args) == 1:
                fn = {"rad": math.radians, "deg": math.degrees, "abs": abs, "sin": math.sin, "cos": math.cos, "tan": math.tan, "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil}[name.split(".")[1]]
                return self.number(fn(self.number(args[0], token)), token)
            if name in transf_math.FUNCTIONS and len(args) == transf_math.FUNCTIONS[name][1]:
                return transf_math.FUNCTIONS[name][0](*args)
            if name in _MATH_FUNCTIONS:
                values = [self.number(v, token) for v in args]
                minimum, maximum = _MATH_FUNCTIONS[name][1], _MATH_FUNCTIONS[name][2]
                if minimum <= len(values) <= maximum:
                    return self.number(_MATH_FUNCTIONS[name][0](*values), token)
            if name == "vec3.new" and len(args) == 3:
                return {i + 1: self.number(v, token) for i, v in enumerate(args)}
            if name == "vec2.new" and len(args) == 2:
                return {i + 1: self.number(v, token) for i, v in enumerate(args)}
            if name in vehicleutil.FUNCTIONS and len(args) == vehicleutil.FUNCTIONS[name][1]:
                return vehicleutil.FUNCTIONS[name][0](*args)
            if name == "transf.scaleRotZTransl" and len(args) == 3:
                scale, angle = self.number(args[0], token), self.number(args[1], token)
                vector = args[2]
                if not isinstance(vector, dict) or set(vector) != {1, 2, 3}:
                    self.error("Expected three-component translation vector", token)
                x, y, z = (self.number(vector[i], token) for i in (1, 2, 3))
                c, s = math.cos(angle) * scale, math.sin(angle) * scale
                return {i: self.number(v, token) for i, v in enumerate((c, s, 0, 0, -s, c, 0, 0, 0, 0, scale, 0, x, y, z, 1), 1)}
        except (ValueError, ArithmeticError, TypeError) as exc:
            self.error(f"Invalid helper arguments: {exc}", token)
        self.error(f"Unsupported dynamic function call {name!r}", token)


def parse(text: str, filename="<string>", undefined=None) -> dict:
    """Read a constant data() resource; input functions are never executed.

    With `undefined` (a list), an undefined global such as a misspelt `false` becomes nil instead of an error,
    and (name, line) is appended; TPF2 itself loads such resources, with the value missing.
    """
    return _Parser(text, filename, undefined=undefined).document()


def parse_mod_metadata(text: str, filename="<string>", undefined=None) -> dict:
    """Extract mod metadata, recording function bodies as inert source sentinels.

    Callers must report non-empty __lua_function__ values as unsupported scripts.
    """
    return _Parser(text, filename, metadata=True, undefined=undefined).document()


def dumps(value) -> str:
    """Write a deterministic constant data() resource."""
    def string(s):
        escaped = []
        for c in s:
            if c == "\\": escaped.append("\\\\")
            elif c == '"': escaped.append('\\"')
            elif c == "\n": escaped.append("\\n")
            elif c == "\r": escaped.append("\\r")
            elif c == "\t": escaped.append("\\t")
            elif ord(c) < 32 or ord(c) == 127: escaped.append(f"\\{ord(c):03d}")
            else: escaped.append(c)
        return '"' + "".join(escaped) + '"'

    def emit(obj, depth=0):
        if obj is None: return "nil"
        if obj is True: return "true"
        if obj is False: return "false"
        if isinstance(obj, str): return string(obj)
        if type(obj) in (int, float):
            if isinstance(obj, float) and not math.isfinite(obj):
                raise ValueError("Cannot serialize a non-finite number")
            return repr(obj)
        if isinstance(obj, (list, tuple)):
            obj = dict(enumerate(obj, 1))
        if isinstance(obj, dict):
            if obj == {"__empty_function__": True}: return "function() end"
            if "__lua_function__" in obj:
                raise ValueError("Cannot serialize an unconverted dynamic function")
            if not obj: return "{}"
            indent = "  " * (depth + 1)
            fields = []
            for key, item in obj.items():
                if type(key) not in (str, int, float):
                    raise ValueError("Unsupported Lua table key")
                label = key if isinstance(key, str) and _NAME.fullmatch(key) and key not in _KEYWORDS else "[" + emit(key) + "]"
                fields.append(indent + label + " = " + emit(item, depth + 1) + ",")
            return "{\n" + "\n".join(fields) + "\n" + "  " * depth + "}"
        raise ValueError(f"Unsupported Lua value {type(obj).__name__}")
    return "function data()\n  return " + emit(value, 1) + "\nend\n"
