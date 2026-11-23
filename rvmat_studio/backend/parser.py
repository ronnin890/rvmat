"""Lossless RVMAT parser / serializer (config-style syntax).

Грамматика::

    file     := item*
    item     := property | class | comment
    property := IDENT ('[' ']')? '=' value ';'
    class    := 'class' IDENT (':' IDENT)? '{' item* '}' ';'
    value    := NUMBER | STRING | IDENT | '{' [value (',' value)*] '}'

Числа хранятся как исходный текст (``raw``), поэтому ``0.89999998`` не
превращается в ``0.9`` при повторной записи. Неизвестные свойства, вложенные
классы и комментарии сохраняются в порядке следования.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Optional, Union

NEWLINE = "\r\n"   # формат файлов DayZ Tools (проверено на образцах)
INDENT = "\t"


class RvmatParseError(ValueError):
    def __init__(self, msg: str, line: int, col: int):
        super().__init__(f"{msg} (строка {line}, столбец {col})")
        self.line, self.col = line, col


# --------------------------------------------------------------------- AST
@dataclass
class Value:
    kind: str                     # 'num' | 'str' | 'ident' | 'array'
    raw: str = ""                 # для array не используется
    items: list["Value"] = field(default_factory=list)

    # конструкторы
    @staticmethod
    def num(x: Union[int, float, str]) -> "Value":
        return Value("num", x if isinstance(x, str) else format_number(x))

    @staticmethod
    def string(s: str) -> "Value":
        return Value("str", s.replace('"', '""'))

    @staticmethod
    def array(xs) -> "Value":
        return Value("array", items=[x if isinstance(x, Value) else Value.num(x) for x in xs])

    # чтение
    @property
    def text(self) -> str:        # значение строки без экранирования
        return self.raw.replace('""', '"') if self.kind == "str" else self.raw

    def floats(self) -> list[float]:
        return [float(v.raw) for v in self.items]

    def __str__(self) -> str:
        return serialize_value(self)


@dataclass
class Property:
    name: str
    value: Value
    is_array: bool = False


@dataclass
class Comment:
    text: str                     # включая // или /* */


@dataclass
class ClassNode:
    name: str
    base: Optional[str] = None
    body: list["Node"] = field(default_factory=list)

    def props(self) -> Iterator[Property]:
        return (n for n in self.body if isinstance(n, Property))

    def classes(self) -> Iterator["ClassNode"]:
        return (n for n in self.body if isinstance(n, ClassNode))

    def get_prop(self, name: str) -> Optional[Property]:
        for p in self.props():
            if p.name.lower() == name.lower():   # config-имена нечувствительны к регистру
                return p
        return None

    def get_class(self, name: str) -> Optional["ClassNode"]:
        for c in self.classes():
            if c.name.lower() == name.lower():
                return c
        return None

    def set_prop(self, name: str, value: Value, is_array: Optional[bool] = None,
                 after: Optional[str] = None) -> Property:
        p = self.get_prop(name)
        if p:
            p.value = value
            if is_array is not None:
                p.is_array = is_array
            return p
        p = Property(name, value, value.kind == "array" if is_array is None else is_array)
        idx = len(self.body)
        if after:
            for i, n in enumerate(self.body):
                if isinstance(n, Property) and n.name.lower() == after.lower():
                    idx = i + 1
        self.body.insert(idx, p)
        return p

    def remove(self, name: str) -> bool:
        for i, n in enumerate(self.body):
            if isinstance(n, (Property, ClassNode)) and n.name.lower() == name.lower():
                del self.body[i]
                return True
        return False


Node = Union[Property, ClassNode, Comment]


# --------------------------------------------------------------- limits
MAX_DEPTH = 64              # вложенность классов/массивов (значения синхронизированы с core/limits.py)
MAX_TOKENS = 100_000


# --------------------------------------------------------------- tokenizer
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<lcom>//[^\r\n]*)
  | (?P<bcom>/\*.*?\*/)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<num>[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<p>[{}\[\]=;,:])
""", re.X | re.S)


def _tokenize(src: str):
    pos, line, col = 0, 1, 1
    out = []
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise RvmatParseError(f"Неожиданный символ {src[pos]!r}", line, col)
        kind, text = m.lastgroup, m.group()
        if kind != "ws":
            out.append((kind, text, line, col))
            if len(out) > MAX_TOKENS:
                raise RvmatParseError(f"Слишком много токенов (> {MAX_TOKENS})", line, col)
        nl = text.count("\n")
        if nl:
            line += nl
            col = len(text) - text.rfind("\n")
        else:
            col += len(text)
        pos = m.end()
    out.append(("eof", "", line, col))
    return out


class _Parser:
    def __init__(self, src: str):
        self.t = _tokenize(src)
        self.i = 0
        self.depth = 0

    def peek(self):
        return self.t[self.i]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, text: str):
        k, v, ln, c = self.next()
        if v != text or k in ("str",):
            raise RvmatParseError(f"Ожидалось {text!r}, найдено {v or 'конец файла'!r}", ln, c)

    def _enter(self, ln, c):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise RvmatParseError(f"Слишком глубокая вложенность (> {MAX_DEPTH})", ln, c)

    def parse_items(self, until_brace: bool) -> list[Node]:
        body: list[Node] = []
        while True:
            k, v, ln, c = self.peek()
            if k == "eof":
                if until_brace:
                    raise RvmatParseError("Не закрыта фигурная скобка '{'", ln, c)
                return body
            if k == "p" and v == "}":
                if not until_brace:
                    raise RvmatParseError("Лишняя '}'", ln, c)
                return body
            if k in ("lcom", "bcom"):
                self.next()
                body.append(Comment(v))
            elif k == "id" and v.lower() == "class":
                body.append(self.parse_class())
            elif k == "id":
                body.append(self.parse_property())
            else:
                raise RvmatParseError(f"Неожиданный токен {v!r}", ln, c)

    def parse_class(self) -> ClassNode:
        self.next()
        k, name, ln, c = self.next()
        if k != "id":
            raise RvmatParseError("Ожидалось имя класса", ln, c)
        base = None
        if self.peek()[1] == ":":
            self.next()
            k, base, ln, c = self.next()
            if k != "id":
                raise RvmatParseError("Ожидалось имя базового класса", ln, c)
        self.expect("{")
        self._enter(ln, c)
        body = self.parse_items(True)
        self.depth -= 1
        self.expect("}")
        self.expect(";")
        return ClassNode(name, base, body)

    def parse_property(self) -> Property:
        _, name, _, _ = self.next()
        is_array = False
        if self.peek()[1] == "[":
            self.next()
            self.expect("]")
            is_array = True
        self.expect("=")
        val = self.parse_value()
        self.expect(";")
        if is_array and val.kind != "array":
            raise RvmatParseError(f"{name}[] требует массив", *self.t[self.i - 1][2:])
        return Property(name, val, is_array)

    def parse_value(self) -> Value:
        k, v, ln, c = self.next()
        if k == "num":
            return Value("num", v)
        if k == "str":
            return Value("str", v[1:-1])
        if k == "id":
            return Value("ident", v)
        if k == "p" and v == "{":
            items: list[Value] = []
            self._enter(ln, c)
            if self.peek()[1] == "}":
                self.depth -= 1
                self.next()
                return Value("array", items=items)
            while True:
                items.append(self.parse_value())
                _, sep, ln2, c2 = self.next()
                if sep == "}":
                    self.depth -= 1
                    return Value("array", items=items)
                if sep != ",":
                    raise RvmatParseError(f"Ожидалось ',' или '}}', найдено {sep!r}", ln2, c2)
        raise RvmatParseError(f"Ожидалось значение, найдено {v or 'конец файла'!r}", ln, c)


def parse(src: str) -> ClassNode:
    """Разобрать текст RVMAT. Возвращает корневой ClassNode с пустым именем."""
    if src.startswith("﻿"):
        src = src[1:]
    return ClassNode("", None, _Parser(src).parse_items(False))


# --------------------------------------------------------------- serializer
def format_number(x: Union[int, float]) -> str:
    """Формат чисел как в DayZ Tools: float32, 8 значащих цифр (0.9 -> 0.89999998)."""
    import struct
    if isinstance(x, int):
        return str(x)
    f32 = struct.unpack("f", struct.pack("f", x))[0]
    s = "%.8g" % f32
    if "e" in s:
        s = ("%.10f" % f32).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def serialize_value(v: Value) -> str:
    if v.kind == "array":
        return "{" + ",".join(serialize_value(i) for i in v.items) + "}"
    if v.kind == "str":
        return f'"{v.raw}"'
    return v.raw


def _ser_nodes(nodes: list[Node], depth: int, out: list[str]):
    ind = INDENT * depth
    for n in nodes:
        if isinstance(n, Comment):
            out.append(ind + n.text)
        elif isinstance(n, Property):
            out.append(f"{ind}{n.name}{'[]' if n.is_array else ''}={serialize_value(n.value)};")
        else:
            head = f"{ind}class {n.name}" + (f" : {n.base}" if n.base else "")
            out.append(head)
            out.append(ind + "{")
            _ser_nodes(n.body, depth + 1, out)
            out.append(ind + "};")


def serialize(root: ClassNode) -> str:
    lines: list[str] = []
    _ser_nodes(root.body, 0, lines)
    return NEWLINE.join(lines) + NEWLINE
