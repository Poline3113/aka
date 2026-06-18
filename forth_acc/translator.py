"""Forth → binary translator.

Variant: forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1

Two-pass pipeline:
  Pass 1 — tokenize, build dictionary, emit IR with symbolic operands.
  Pass 2 — resolve symbols to integers, encode to 32-bit words.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from forth_acc.isa import IN_PORT, IVEC_IN, MM, OUT_PORT, Op

# ── Tokens ─────────────────────────────────────────────────────────────────────

class TKind(Enum):
    NUM   = auto()  # integer literal          val: int
    WORD  = auto()  # identifier (lowercased)  val: str
    STR   = auto()  # c" text"                 val: str  (text without terminator)
    PRINT = auto()  # ." text"                 val: str
    CHAR  = auto()  # [char] X                 val: int  (ord of X)
    TICK  = auto()  # ' name                   val: str  (lowercased name)


@dataclass
class Token:
    kind: TKind
    val: str | int


def tokenize(src: str) -> list[Token]:
    """Lex Forth source into a flat token stream."""
    tokens: list[Token] = []
    pos = 0
    n = len(src)

    def skip_ws() -> None:
        nonlocal pos
        while pos < n and src[pos] in " \t\r\n":
            pos += 1

    def next_raw() -> str:
        nonlocal pos
        skip_ws()
        start = pos
        while pos < n and src[pos] not in " \t\r\n":
            pos += 1
        return src[start:pos]

    def read_until(ch: str) -> str:
        nonlocal pos
        start = pos
        while pos < n and src[pos] != ch:
            pos += 1
        text = src[start:pos]
        if pos < n:
            pos += 1  # consume delimiter
        return text

    while pos < n:
        skip_ws()
        if pos >= n:
            break
        word = next_raw()
        if not word:
            break
        wl = word.lower()

        if word == "\\":
            read_until("\n")

        elif word == "(":
            read_until(")")

        elif wl in ('c"', '."'):
            # skip the one mandatory space between delimiter and content
            if pos < n and src[pos] == " ":
                pos += 1
            text = read_until('"')
            tokens.append(Token(TKind.STR if wl == 'c"' else TKind.PRINT, text))

        elif wl == "[char]":
            skip_ws()
            if pos < n:
                tokens.append(Token(TKind.CHAR, ord(src[pos])))
                pos += 1

        elif word == "'":
            tokens.append(Token(TKind.TICK, next_raw().lower()))

        elif _is_num(word):
            tokens.append(Token(TKind.NUM, int(word)))

        else:
            tokens.append(Token(TKind.WORD, wl))

    return tokens


def _is_num(s: str) -> bool:
    if not s:
        return False
    start = 1 if s[0] == "-" else 0
    return len(s) > start and s[start:].isdigit()


# ── Dictionary entries ─────────────────────────────────────────────────────────

class EKind(Enum):
    PRIM  = auto()  # primitive: compiled inline via template
    WORD  = auto()  # user word: compiled as CALL; value = code address
    VAR   = auto()  # variable:  compiled as push-address; value = data address
    CONST = auto()  # constant:  compiled as push-imm;     value = literal


@dataclass
class DictEntry:
    kind: EKind
    value: int = 0  # code/data addr for WORD/VAR; literal for CONST; 0 for PRIM


# ── Primitive templates ────────────────────────────────────────────────────────
#
# A PrimItem is either a (Op, MM, operand) instruction spec or a bare label
# anchor string.  In instruction specs a str operand is a label reference.
#
# A Template is called with a fresh-label factory to instantiate the template;
# label anchors (bare strings) in the returned list mark where labels land.

type PrimItem = tuple[Op, MM, int | str] | str
type Template = Callable[[Callable[[], str], ], list[PrimItem]]


def _fixed(*items: PrimItem) -> Template:
    """Template for primitives that need no fresh labels."""
    def _gen(_: Callable[[], str]) -> list[PrimItem]:
        return list(items)
    return _gen


def _bool_op(branch: Op) -> Template:
    """Template for 0= (branch=JZ) and 0< (branch=JN).

    OR #0 refreshes Z/N from ACC without altering the stack.
    Produces Forth boolean: -1 (true) or 0 (false).

    0=:  OR #0 ; JZ L1 ; LD #0 ; JMP L2 ; L1: LD #-1 ; L2:
    0<:  OR #0 ; JN L1 ; LD #0 ; JMP L2 ; L1: LD #-1 ; L2:
    """
    def _gen(fresh: Callable[[], str]) -> list[PrimItem]:
        l1, l2 = fresh(), fresh()
        return [
            (Op.OR,  MM.IMM, 0),
            (branch, MM.STK, l1),
            (Op.LD,  MM.IMM, 0),
            (Op.JMP, MM.STK, l2),
            l1,
            (Op.LD,  MM.IMM, -1),
            l2,
        ]
    return _gen


# Primitive table: name → template.
# Templates match the Translator Design §Primitive inline templates exactly.
# Note: 1- maps to SUB #1 per spec (computes 1-ACC in IMM mode).
PRIMITIVES: dict[str, Template] = {
    # Arithmetic (ACC ← X op ACC, X=NOS for STK)
    "+":       _fixed((Op.ADD,   MM.STK, 0)),
    "-":       _fixed((Op.SUB,   MM.STK, 0)),
    "*":       _fixed((Op.MUL,   MM.STK, 0)),
    "/":       _fixed((Op.DIV,   MM.STK, 0)),
    "mod":     _fixed((Op.MOD,   MM.STK, 0)),
    "1+":      _fixed((Op.ADD,   MM.IMM, 1)),
    "1-":      _fixed((Op.SUB,   MM.IMM, 1)),   # per spec: SUB #1

    # Logic
    "and":     _fixed((Op.AND,   MM.STK, 0)),
    "or":      _fixed((Op.OR,    MM.STK, 0)),
    "xor":     _fixed((Op.XOR,   MM.STK, 0)),
    "invert":  _fixed((Op.INV,   MM.STK, 0)),

    # Stack
    "dup":     _fixed((Op.PUSH,  MM.STK, 0)),
    "drop":    _fixed((Op.POP,   MM.STK, 0)),
    "swap":    _fixed((Op.SWAP,  MM.STK, 0)),

    # Memory
    "@":       _fixed((Op.LDA,   MM.STK, 0)),
    "!":       _fixed((Op.STA,   MM.STK, 0)),

    # Return stack
    ">r":      _fixed((Op.TOR,   MM.STK, 0)),
    "r>":      _fixed((Op.FROMR, MM.STK, 0)),

    # Port I/O
    "in@":     _fixed((Op.IN,    MM.STK, IN_PORT)),
    "emit":    _fixed((Op.OUT,   MM.STK, OUT_PORT), (Op.POP, MM.STK, 0)),

    # Interrupt control
    "ei":      _fixed((Op.EI,    MM.STK, 0)),
    "di":      _fixed((Op.DI,    MM.STK, 0)),
    "reti":    _fixed((Op.RETI,  MM.STK, 0)),
    "set-isr": _fixed((Op.ST,    MM.DIR, IVEC_IN), (Op.POP, MM.STK, 0)),

    # Execution tokens
    "execute": _fixed((Op.CALLA, MM.STK, 0)),

    # Boolean results
    "0=":      _bool_op(Op.JZ),
    "0<":      _bool_op(Op.JN),

    # Misc
    "nop":     _fixed((Op.NOP,   MM.STK, 0)),
}


# ── Dictionary ─────────────────────────────────────────────────────────────────

class Dictionary:
    """Global word dictionary.

    Primitives are looked up in the module-level PRIMITIVES table.
    User-defined words, variables, and constants are stored in _user.
    All names are expected to be already lowercased by the tokenizer.
    """

    def __init__(self) -> None:
        self._user: dict[str, DictEntry] = {}

    # ── Primitive interface ──────────────────────────────────────────────────

    def is_primitive(self, name: str) -> bool:
        return name in PRIMITIVES

    def get_template(self, name: str) -> Template:
        return PRIMITIVES[name]

    # ── User-definition interface ────────────────────────────────────────────

    def define_word(self, name: str, addr: int) -> None:
        """Register a compiled Forth word at the given code address."""
        self._user[name] = DictEntry(EKind.WORD, addr)

    def define_var(self, name: str, addr: int) -> None:
        """Register a variable cell at the given data address."""
        self._user[name] = DictEntry(EKind.VAR, addr)

    def define_const(self, name: str, value: int) -> None:
        """Register a compile-time constant (no data cell allocated)."""
        self._user[name] = DictEntry(EKind.CONST, value)

    # ── Lookup ───────────────────────────────────────────────────────────────

    def lookup(self, name: str) -> DictEntry | None:
        """Return the user-defined entry for name, or None if not found.

        Does not search primitives; use is_primitive() for that check.
        """
        return self._user.get(name)

    def entries(self) -> dict[str, DictEntry]:
        return dict(self._user)


# ── Pass 1: layout + codegen ──────────────────────────────────────────────────

from forth_acc.isa import (  # noqa: E402
    CODE_BASE,
    DATA_BASE,
    WORD,
    IRInstr,
    to_word,
)


def _fits22(v: int) -> bool:
    return -0x20_0000 <= v <= 0x1F_FFFF


class TranslateError(Exception):
    pass


class Pass1:
    """Single-pass layout: emits IR with symbolic operands, allocates data."""

    def __init__(self) -> None:
        self.ir: list[IRInstr] = []
        self.syms: dict[str, int] = {}   # label/name → address or value
        self.data: list[int] = []         # data-segment words (index 0 = DATA_BASE)
        self.cp: int = CODE_BASE
        self.dp: int = DATA_BASE
        self._lc: int = 0
        self.dict: Dictionary = Dictionary()
        self._loop_cells: list[tuple[int, int]] = []   # per nesting depth (idx, lim)
        self._prim_stubs: dict[str, str] = {}          # prim name → label (deferred)
        self._tokens: list[Token] = []
        self._pos: int = 0
        self._top_toks: list[Token] = []

    # ── helpers ──────────────────────────────────────────────────────────────

    def fresh(self) -> str:
        self._lc += 1
        return f"__L{self._lc}"

    def _emit(self, op: Op, mm: MM = MM.STK, operand: int | str = 0) -> None:
        self.ir.append(IRInstr(addr=self.cp, op=op, mm=mm, operand=operand))
        self.cp += WORD

    def _anchor(self, label: str) -> None:
        self.syms[label] = self.cp

    def _alloc_data(self, n: int = 1) -> int:
        addr = self.dp
        self.data.extend([0] * n)
        self.dp += n * WORD
        return addr

    def _set_data(self, addr: int, val: int) -> None:
        self.data[(addr - DATA_BASE) // WORD] = to_word(val)

    def _alloc_string(self, text: str) -> int:
        addr = self._alloc_data(len(text) + 1)
        for i, ch in enumerate(text):
            self._set_data(addr + i * WORD, ord(ch))
        return addr

    def _spill(self, val: int) -> int:
        addr = self._alloc_data()
        self._set_data(addr, val)
        return addr

    def _push_val(self, val: int | str) -> None:
        """Emit PUSH ; LD #val (or LD addr if val overflows 22 bits)."""
        self._emit(Op.PUSH)
        if isinstance(val, str):
            self._emit(Op.LD, MM.IMM, val)
        elif _fits22(val):
            self._emit(Op.LD, MM.IMM, val)
        else:
            self._emit(Op.LD, MM.DIR, self._spill(val))

    def _expand(self, tmpl: Template) -> None:
        for item in tmpl(self.fresh):
            if isinstance(item, str):
                self._anchor(item)
            else:
                op, mm, operand = item
                self._emit(op, mm, operand)

    def _loop_pair(self, depth: int) -> tuple[int, int]:
        while len(self._loop_cells) <= depth:
            idx = self._alloc_data()
            lim = self._alloc_data()
            self._loop_cells.append((idx, lim))
        return self._loop_cells[depth]

    def _prim_stub_label(self, name: str) -> str:
        if name not in self._prim_stubs:
            lbl = f"__stub_{self._lc}"
            self._lc += 1
            self._prim_stubs[name] = lbl
        return self._prim_stubs[name]

    # ── token stream ─────────────────────────────────────────────────────────

    def _peek(self) -> Token | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def _peek2_is(self, word: str) -> bool:
        if self._pos + 1 < len(self._tokens):
            t = self._tokens[self._pos + 1]
            return t.kind == TKind.WORD and str(t.val) == word
        return False

    # ── compilation ──────────────────────────────────────────────────────────

    def _compile_stmts(self, stops: frozenset[str] | None, loop_depth: int) -> str:
        """Compile tokens until a stop word or EOF (stops=None means run to EOF)."""
        while True:
            tok = self._peek()
            if tok is None:
                if stops is None:
                    return ""
                raise TranslateError(f"unexpected EOF; expected one of {stops}")
            if stops is not None and tok.kind == TKind.WORD and str(tok.val) in stops:
                self._next()
                return str(tok.val)
            self._compile_one(loop_depth)

    def _compile_one(self, loop_depth: int) -> None:
        tok = self._next()
        if tok.kind == TKind.NUM:
            self._push_val(int(tok.val))
        elif tok.kind == TKind.CHAR:
            self._push_val(int(tok.val))
        elif tok.kind == TKind.STR:
            self._push_val(self._alloc_string(str(tok.val)))
        elif tok.kind == TKind.PRINT:
            self._push_val(self._alloc_string(str(tok.val)))
            self._emit(Op.CALL, MM.STK, "type")
        elif tok.kind == TKind.TICK:
            name = str(tok.val)
            if self.dict.is_primitive(name):
                self._push_val(self._prim_stub_label(name))
            else:
                entry = self.dict.lookup(name)
                if entry is None:
                    raise TranslateError(f"undefined word in tick: '{name}'")
                self._push_val(name)
        elif tok.kind == TKind.WORD:
            self._compile_word(str(tok.val), loop_depth)
        else:
            raise TranslateError(f"unexpected token: {tok}")

    def _compile_word(self, name: str, loop_depth: int) -> None:  # noqa: C901
        if name == "if":
            lf, le = self.fresh(), self.fresh()
            self._emit(Op.OR, MM.IMM, 0)
            self._emit(Op.JZ, MM.STK, lf)
            self._emit(Op.POP)
            stop = self._compile_stmts(frozenset({"else", "then"}), loop_depth)
            if stop == "else":
                self._emit(Op.JMP, MM.STK, le)
                self._anchor(lf)
                self._emit(Op.POP)
                self._compile_stmts(frozenset({"then"}), loop_depth)
                self._anchor(le)
            else:  # then (no else branch)
                self._emit(Op.JMP, MM.STK, le)
                self._anchor(lf)
                self._emit(Op.POP)
                self._anchor(le)

        elif name == "begin":
            lb = self.fresh()
            self._anchor(lb)
            self._compile_stmts(frozenset({"until"}), loop_depth)
            ll, ld = self.fresh(), self.fresh()
            self._emit(Op.OR, MM.IMM, 0)
            self._emit(Op.JZ, MM.STK, ll)   # false → ll (keep looping)
            self._emit(Op.POP)               # true → consume flag
            self._emit(Op.JMP, MM.STK, ld)  # exit
            self._anchor(ll)
            self._emit(Op.POP)               # consume flag (false)
            self._emit(Op.JMP, MM.STK, lb)  # back to begin
            self._anchor(ld)

        elif name == "do":
            idx, lim = self._loop_pair(loop_depth)
            lb, ld = self.fresh(), self.fresh()
            # TOS = start (ACC), NOS = limit
            self._emit(Op.ST,  MM.DIR, idx)  # mem[IDX] = start
            self._emit(Op.POP)               # ACC = limit
            self._emit(Op.ST,  MM.DIR, lim)  # mem[LIM] = limit
            self._emit(Op.POP)               # restore previous TOS
            self._anchor(lb)
            self._compile_stmts(frozenset({"loop"}), loop_depth + 1)
            # loop epilogue
            self._emit(Op.PUSH)
            self._emit(Op.LD,  MM.DIR, idx)
            self._emit(Op.ADD, MM.IMM, 1)
            self._emit(Op.ST,  MM.DIR, idx)
            self._emit(Op.SUB, MM.DIR, lim)  # ACC = LIM − (IDX+1)
            self._emit(Op.JZ,  MM.STK, ld)
            self._emit(Op.JN,  MM.STK, ld)
            self._emit(Op.POP)
            self._emit(Op.JMP, MM.STK, lb)
            self._anchor(ld)
            self._emit(Op.POP)

        elif name == "i":
            idx, _ = self._loop_pair(loop_depth - 1)
            self._emit(Op.PUSH)
            self._emit(Op.LD, MM.DIR, idx)

        elif self.dict.is_primitive(name):
            self._expand(self.dict.get_template(name))

        else:
            entry = self.dict.lookup(name)
            if entry is None:
                raise TranslateError(f"undefined word: '{name}'")
            if entry.kind == EKind.WORD:
                self._emit(Op.CALL, MM.STK, name)
            elif entry.kind == EKind.VAR:
                self._push_val(name)
            elif entry.kind == EKind.CONST:
                self._push_val(entry.value)

    def _compile_def(self) -> None:
        name = str(self._next().val)
        self._anchor(name)
        self.dict.define_word(name, self.cp)
        self._compile_stmts(frozenset({";"}), 0)
        self._emit(Op.RET)

    def _compile_main(self) -> None:
        self._anchor("__entry__")
        saved_tokens, saved_pos = self._tokens, self._pos
        self._tokens, self._pos = self._top_toks, 0
        self._compile_stmts(None, 0)
        self._emit(Op.HLT)
        self._tokens, self._pos = saved_tokens, saved_pos
        # Emit deferred primitive stubs after HLT — unreachable by fall-through
        # but callable by address (xt). Order determined by first-encountered.
        for pname, lbl in self._prim_stubs.items():
            self._anchor(lbl)
            self._expand(self.dict.get_template(pname))
            self._emit(Op.RET)

    # ── public entry point ────────────────────────────────────────────────────

    def run(self, tokens: list[Token]) -> None:
        """Layout pass: scan tokens, compile word defs, collect top-level stmts."""
        self._tokens = tokens
        self._pos = 0

        while self._pos < len(self._tokens):
            tok = self._peek()
            if tok is None:
                break

            if tok.kind == TKind.WORD and str(tok.val) == ":":
                self._next()
                self._compile_def()

            elif tok.kind == TKind.WORD and str(tok.val) == "variable":
                self._next()
                varname = str(self._next().val)
                addr = self._alloc_data()
                self.syms[varname] = addr
                self.dict.define_var(varname, addr)

            elif tok.kind == TKind.NUM and self._peek2_is("constant"):
                n = int(self._next().val)
                self._next()                             # consume 'constant'
                constname = str(self._next().val)
                self.dict.define_const(constname, n)

            else:
                self._top_toks.append(self._next())

        self._compile_main()


# ── Pass 2: symbol resolution + encoding ─────────────────────────────────────

from forth_acc.isa import BinaryImage, encode  # noqa: E402


def _resolve(ir: list[IRInstr], syms: dict[str, int]) -> list[int]:
    """Resolve symbolic operands and encode each IRInstr to a 32-bit word."""
    words: list[int] = []
    for instr in ir:
        operand = instr.operand
        if isinstance(operand, str):
            if operand not in syms:
                raise TranslateError(f"undefined symbol: '{operand}'")
            operand = syms[operand]
        words.append(encode(instr.op, instr.mm, operand))
    return words


# ── Public translate API ──────────────────────────────────────────────────────

def translate(src: str, prelude: str = "") -> BinaryImage:
    """Translate Forth source (with optional prepended prelude) to a BinaryImage."""
    combined = (prelude + "\n" + src) if prelude else src
    tokens = tokenize(combined)
    p1 = Pass1()
    p1.run(tokens)

    if "__entry__" not in p1.syms:
        raise TranslateError("no main entry point found")

    main_addr = p1.syms["__entry__"]
    reset_word = encode(Op.JMP, MM.STK, main_addr)
    code_words = tuple(_resolve(p1.ir, p1.syms))
    data_words = tuple(p1.data)
    return BinaryImage(
        reset_word=reset_word,
        ivec_in=0,
        code=code_words,
        data=data_words,
    )
