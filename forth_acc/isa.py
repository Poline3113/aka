"""ISA constants, data structures, encoding, and decoding for the forth_acc processor.

Variant: forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1

Single source of truth shared by translator.py and machine.py.
Nothing here imports from those modules.

Instruction word layout (32 bits, big-endian):

  31      24 23 22 21                  0
  +---------+-----+--------------------+
  | opcode  | MM  |    operand (22)    |
  |  (8)    | (2) | addr / imm / port  |
  +---------+-----+--------------------+

  word = (opcode << 24) | (MM << 22) | (operand & 0x3F_FFFF)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Final

# ── Memory layout ──────────────────────────────────────────────────────────────
#
#   Address        Contents
#   0x0000_0000    RESET      — one word: JMP main  (PC starts here)
#   0x0000_0004    IVEC_IN    — input interrupt vector cell (handler address)
#   0x0000_0008    CODE_BASE  — prelude words, user words, main, handlers
#   0x0000_1000    DATA_BASE  — variables, strings, loop index/limit cells
#   0x0000_2000    DSTACK_BASE — data stack (SP), grows upward (+WORD per push)
#   0x0000_3000    RSTACK_BASE — return stack (RSP), grows upward (+WORD per push)
#   0x0000_4000    MEM_SIZE   — (exclusive upper bound; 16 KiB total)

RESET:       Final[int] = 0x0000_0000
IVEC_IN:     Final[int] = 0x0000_0004
CODE_BASE:   Final[int] = 0x0000_0008
DATA_BASE:   Final[int] = 0x0000_1000
DSTACK_BASE: Final[int] = 0x0000_2000
RSTACK_BASE: Final[int] = 0x0000_3000
MEM_SIZE:    Final[int] = 0x0000_4000  # 16 KiB = 4 096 32-bit words

WORD:     Final[int] = 4  # bytes per cell; pointer arithmetic always steps by WORD
IN_PORT:  Final[int] = 0  # port number for the input device
OUT_PORT: Final[int] = 1  # port number for the output device


# ── Opcodes ────────────────────────────────────────────────────────────────────
#
# Numeric assignments are fixed by the Translator Design document §Binary Generation.
# The 32 values fit in 8 bits (opcode field); values 0x00–0x1F are used.

class Op(IntEnum):
    # Data movement
    NOP   = 0x00  # no operation
    LD    = 0x01  # ACC ← imm / mem[addr]                      (IMM / DIR)
    ST    = 0x02  # mem[addr] ← ACC                            (DIR)
    LDA   = 0x03  # ACC ← mem[ACC]                             (@)
    STA   = 0x04  # mem[ACC] ← NOS, drop both, refill TOS      (!)
    PUSH  = 0x05  # mem[SP] ← ACC; SP += WORD                  (DUP)
    POP   = 0x06  # SP -= WORD; ACC ← mem[SP]                  (DROP)
    SWAP  = 0x07  # ACC ↔ mem[SP-WORD]

    # Arithmetic  (all: ACC ← X op ACC; X per MM)
    ADD   = 0x08  # +  ; sets Z, N, C
    SUB   = 0x09  # -  ; sets Z, N, C
    MUL   = 0x0A  # *  ; sets Z, N
    DIV   = 0x0B  # /  (signed truncate); sets Z, N
    MOD   = 0x0C  # mod (signed); sets Z, N

    # Logic  (all: ACC ← X op ACC; X per MM)
    AND   = 0x0D  # bitwise AND; sets Z, N
    OR    = 0x0E  # bitwise OR;  sets Z, N
    XOR   = 0x0F  # bitwise XOR; sets Z, N
    INV   = 0x10  # ACC ← ~ACC;  sets Z, N

    # Control flow
    JMP   = 0x11  # PC ← addr (unconditional)
    JZ    = 0x12  # if Z: PC ← addr
    JN    = 0x13  # if N: PC ← addr
    JC    = 0x14  # if C: PC ← addr  (double-precision carry)

    # Procedure
    CALL  = 0x15  # mem[RSP] ← PC_next; RSP += WORD; PC ← addr
    CALLA = 0x16  # indirect call to ACC (EXECUTE); consumes xt, refills TOS
    RET   = 0x17  # RSP -= WORD; PC ← mem[RSP]
    TOR   = 0x18  # move TOS to RS, refill TOS from DS  (>R)
    FROMR = 0x19  # spill TOS to DS, pull from RS into TOS  (R>)

    # Interrupt
    RETI  = 0x1A  # restore FLAGS, ACC, PC from RS; IE ← 1
    EI    = 0x1B  # IE ← 1
    DI    = 0x1C  # IE ← 0

    # Port I/O
    IN    = 0x1D  # ACC ← port[port]; sets Z, N
    OUT   = 0x1E  # port[port] ← ACC

    # System
    HLT   = 0x1F  # stop simulation


# ── Addressing modes (MM field, bits 23:22) ────────────────────────────────────

class MM(IntEnum):
    STK = 0b00  # second operand = mem[SP-WORD]; SP -= WORD  (Forth NOS)
    IMM = 0b01  # second operand = sign-extended 22-bit constant (no memory)
    DIR = 0b10  # second operand = mem[operand] (direct, static address)
    # 0b11 is reserved / unused


# ── Sequencer micro-steps ──────────────────────────────────────────────────────
#
# Every instruction: T0 → T1 → E1 … En → (interrupt check) → T0 …
# Interrupt entry replaces the next T0 with: I1 → I2 → I3 → I4 → T0 …

class Step(IntEnum):
    T0 = 0  # fetch:   IR ← mem[PC]; PC ← PC + WORD
    T1 = 1  # decode:  split IR fields; select execute sequence
    E1 = 2  # execute step 1
    E2 = 3  # execute step 2
    E3 = 4  # execute step 3
    I1 = 5  # interrupt: mem[RSP] ← PC;    RSP += WORD
    I2 = 6  # interrupt: mem[RSP] ← ACC;   RSP += WORD
    I3 = 7  # interrupt: mem[RSP] ← FLAGS; RSP += WORD
    I4 = 8  # interrupt: IE←0; IRQ←0; PC←mem[IVEC_IN]; in_trap←1


# ── Instruction timing ─────────────────────────────────────────────────────────
#
# Number of *execute* micro-steps per opcode.
# Total ticks = 1 (fetch) + 1 (decode) + EXEC_TICKS[op].
# Source: Timing Model §Execute Timing and ISA §Complete Instruction Table.
#
# Mode does NOT change the tick count: LD IMM and LD DIR both take 1 execute tick.

EXEC_TICKS: Final[dict[Op, int]] = {
    # 0 execute ticks (total 2)
    Op.NOP:   0,
    Op.HLT:   0,

    # 1 execute tick (total 3)
    Op.LD:    1,  # IMM: register move; DIR: one memory read
    Op.ST:    1,  # DIR: one memory write
    Op.LDA:   1,  # one memory read (address from ACC)
    Op.PUSH:  1,  # one memory write (spill ACC to DS)
    Op.POP:   1,  # one memory read  (refill ACC from DS)
    Op.ADD:   1,  # IMM: ALU only; DIR/STK: one memory read
    Op.SUB:   1,
    Op.MUL:   1,
    Op.DIV:   1,
    Op.MOD:   1,
    Op.AND:   1,
    Op.OR:    1,
    Op.XOR:   1,
    Op.INV:   1,  # pure ALU (no memory)
    Op.JMP:   1,  # pure register (PC update, no memory)
    Op.JZ:    1,
    Op.JN:    1,
    Op.JC:    1,
    Op.CALL:  1,  # one memory write (push return address to RS)
    Op.RET:   1,  # one memory read  (pop return address from RS)
    Op.EI:    1,  # pure register
    Op.DI:    1,
    Op.IN:    1,  # one port read
    Op.OUT:   1,  # one port write

    # 2 execute ticks (total 4)
    Op.SWAP:  2,  # E1: read mem[SP-WORD]; E2: write mem[SP-WORD]
    Op.CALLA: 2,  # E1: write return addr to RS; E2: read new TOS from DS
    Op.TOR:   2,  # E1: write ACC to RS; E2: read new TOS from DS
    Op.FROMR: 2,  # E1: write ACC to DS; E2: read from RS into ACC

    # 3 execute ticks (total 5)
    Op.STA:   3,  # E1: read value (NOS); E2: write to address; E3: refill TOS
    Op.RETI:  3,  # E1: read FLAGS; E2: read ACC; E3: read PC
}

# Cost of the hardware interrupt-entry sequence (not an instruction).
INTERRUPT_ENTRY_TICKS: Final[int] = 4


# ── Valid addressing modes per opcode ─────────────────────────────────────────
#
# Used by the translator to validate generated instructions and by tests.
# An empty frozenset means the opcode ignores MM (implied / Z-format).

_M_LD:  Final[frozenset[MM]] = frozenset({MM.IMM, MM.DIR})
_M_ST:  Final[frozenset[MM]] = frozenset({MM.DIR})
_M_ALU: Final[frozenset[MM]] = frozenset({MM.IMM, MM.DIR, MM.STK})
_M_NIL: Final[frozenset[MM]] = frozenset()

VALID_MODES: Final[dict[Op, frozenset[MM]]] = {
    Op.NOP:   _M_NIL,
    Op.LD:    _M_LD,
    Op.ST:    _M_ST,
    Op.LDA:   _M_NIL,
    Op.STA:   _M_NIL,
    Op.PUSH:  _M_NIL,
    Op.POP:   _M_NIL,
    Op.SWAP:  _M_NIL,
    Op.ADD:   _M_ALU,
    Op.SUB:   _M_ALU,
    Op.MUL:   _M_ALU,
    Op.DIV:   _M_ALU,
    Op.MOD:   _M_ALU,
    Op.AND:   _M_ALU,
    Op.OR:    _M_ALU,
    Op.XOR:   _M_ALU,
    Op.INV:   _M_NIL,
    Op.JMP:   _M_NIL,
    Op.JZ:    _M_NIL,
    Op.JN:    _M_NIL,
    Op.JC:    _M_NIL,
    Op.CALL:  _M_NIL,
    Op.CALLA: _M_NIL,
    Op.RET:   _M_NIL,
    Op.TOR:   _M_NIL,
    Op.FROMR: _M_NIL,
    Op.RETI:  _M_NIL,
    Op.EI:    _M_NIL,
    Op.DI:    _M_NIL,
    Op.IN:    _M_NIL,
    Op.OUT:   _M_NIL,
    Op.HLT:   _M_NIL,
}


# ── Arithmetic word helpers ────────────────────────────────────────────────────

def to_word(v: int) -> int:
    """Mask to unsigned 32-bit (discard bits above bit 31)."""
    return v & 0xFFFF_FFFF


def to_signed(v: int) -> int:
    """Reinterpret a 32-bit unsigned value as two's-complement signed."""
    v = to_word(v)
    return v - 0x1_0000_0000 if v >= 0x8000_0000 else v


def sext22(v: int) -> int:
    """Sign-extend a 22-bit unsigned value to a full Python int.

    Bit 21 is the sign bit.  Values in [0x20_0000, 0x3F_FFFF] are negative.
    """
    v &= 0x3F_FFFF
    return v - 0x40_0000 if v >= 0x20_0000 else v


# ── Binary encoding ────────────────────────────────────────────────────────────

def encode(op: Op, mm: MM, operand: int) -> int:
    """Pack one instruction into a 32-bit word.

    operand is truncated to 22 bits; for immediates pass the raw signed Python
    int and the low 22 bits carry the two's-complement representation naturally.
    """
    return (int(op) << 24) | (int(mm) << 22) | (operand & 0x3F_FFFF)


# ── Binary decoding ────────────────────────────────────────────────────────────

def decode(word: int) -> tuple[Op, MM, int, int]:
    """Unpack a 32-bit instruction word into its four fields.

    Returns (op, mm, raw22, imm_signed):
      op         -- opcode enum value (raises ValueError for unknown opcode).
      mm         -- addressing mode enum value.
      raw22      -- unsigned 22-bit operand field.
      imm_signed -- raw22 sign-extended to a signed Python int (for IMM mode).
    """
    op  = Op((word >> 24) & 0xFF)
    mm  = MM((word >> 22) & 0x03)
    raw = word & 0x3F_FFFF
    return op, mm, raw, sext22(raw)


def decode_safe(word: int) -> tuple[int, MM, int, int]:
    """Like decode(), but returns the raw opcode int for unknown values.

    Used by the disassembler/logger so an illegal instruction word doesn't crash
    the simulation log.
    """
    raw_op = (word >> 24) & 0xFF
    mm     = MM((word >> 22) & 0x03)
    raw    = word & 0x3F_FFFF
    try:
        op_enum = Op(raw_op)
    except ValueError:
        return raw_op, mm, raw, sext22(raw)
    return int(op_enum), mm, raw, sext22(raw)


# ── Disassembly ────────────────────────────────────────────────────────────────

# Instructions whose printed form depends on the MM field.
_MODE_OPS: Final[frozenset[Op]] = frozenset({
    Op.LD, Op.ST,
    Op.ADD, Op.SUB, Op.MUL, Op.DIV, Op.MOD,
    Op.AND, Op.OR, Op.XOR,
})
# Instructions that carry a branch / call target in the operand field.
_ADDR_OPS: Final[frozenset[Op]] = frozenset({Op.JMP, Op.JZ, Op.JN, Op.JC, Op.CALL})
# Instructions that carry a port number in the operand field.
_PORT_OPS: Final[frozenset[Op]] = frozenset({Op.IN, Op.OUT})


def disassemble(word: int) -> str:
    """Return a one-line mnemonic string for one 32-bit instruction word.

    Matches the .lst listing format used by BinaryImage.listing() and the
    simulation log.  Unknown opcodes are shown as '?? 0xNN'.
    """
    raw_op = (word >> 24) & 0xFF
    try:
        op = Op(raw_op)
    except ValueError:
        return f"?? 0x{raw_op:02X}"

    mm  = MM((word >> 22) & 0x03)
    raw = word & 0x3F_FFFF
    imm = sext22(raw)
    name = op.name

    if op in _MODE_OPS:
        if mm == MM.IMM:
            return f"{name} #{imm}"
        if mm == MM.DIR:
            return f"{name} 0x{raw:06X}"
        return name                    # STK: no printed operand
    if op in _ADDR_OPS:
        return f"{name} 0x{raw:06X}"
    if op in _PORT_OPS:
        return f"{name} {raw}"
    return name


# ── Flags ──────────────────────────────────────────────────────────────────────

@dataclass
class Flags:
    """Condition flags and interrupt-enable bit.

    Mutable: the machine calls update_zn() and sets c / ie directly each tick.
    pack() / unpack() convert to/from a single 32-bit word for interrupt context
    save / restore on the return stack.
    """

    z: bool = False   # zero    — result == 0
    n: bool = False   # negative — result < 0 (sign bit set)
    c: bool = False   # carry / borrow out of ADD / SUB (double-precision use)
    ie: bool = False  # interrupt enable

    def update_zn(self, val: int) -> None:
        """Set Z and N from a 32-bit ALU result."""
        s = to_signed(val)
        self.z = s == 0
        self.n = s < 0

    def pack(self) -> int:
        """Encode as a 32-bit word for saving to the return stack on interrupt entry.

        Bit layout: [3]=IE [2]=C [1]=N [0]=Z
        """
        return (
            int(self.z)
            | (int(self.n) << 1)
            | (int(self.c) << 2)
            | (int(self.ie) << 3)
        )

    @classmethod
    def unpack(cls, word: int) -> Flags:
        """Restore from a word produced by pack() (used by RETI)."""
        return cls(
            z=bool(word & 0x1),
            n=bool(word & 0x2),
            c=bool(word & 0x4),
            ie=bool(word & 0x8),
        )

    def copy(self) -> Flags:
        return Flags(z=self.z, n=self.n, c=self.c, ie=self.ie)


# ── Instruction representations ────────────────────────────────────────────────

@dataclass
class IRInstr:
    """Translator intermediate representation for one instruction.

    Pass 1 leaves operand as a label string for forward references;
    Pass 2 replaces every string with the resolved integer address / immediate.
    """

    addr: int                # byte address this instruction will occupy
    op: Op
    mm: MM = MM.STK
    operand: int | str = 0   # str = unresolved symbol name; int after resolution


@dataclass(frozen=True)
class Instr:
    """Decoded instruction — immutable snapshot used by the machine.

    Created once per instruction fetch via Instr.from_word(); cached in state.ir_decoded
    for the decode and execute ticks.
    """

    op: Op
    mm: MM
    raw: int  # unsigned 22-bit field (addresses, port numbers, unsigned immediates)
    imm: int  # sext22(raw) — signed interpretation (meaningful in IMM mode)

    @classmethod
    def from_word(cls, word: int) -> Instr:
        """Decode one 32-bit instruction word."""
        op, mm, raw, imm = decode(word)
        return cls(op=op, mm=mm, raw=raw, imm=imm)

    def mnemonic(self) -> str:
        """Return the disassembled mnemonic string for this instruction."""
        return disassemble(encode(self.op, self.mm, self.raw))

    @property
    def exec_ticks(self) -> int:
        """Number of execute micro-steps for this instruction."""
        return EXEC_TICKS[self.op]

    @property
    def total_ticks(self) -> int:
        """Total clock ticks: 1 fetch + 1 decode + exec_ticks."""
        return 2 + self.exec_ticks


# ── Memory ─────────────────────────────────────────────────────────────────────

class Memory:
    """Single-port, byte-addressed, word-granular von Neumann memory.

    Access granularity is one 32-bit word at a word-aligned byte address.
    The single-port constraint (at most one access per tick) is enforced by
    the machine's tick loop, not by this class.
    """

    def __init__(self, size: int = MEM_SIZE) -> None:
        if size % WORD:
            raise ValueError(f"size must be a multiple of {WORD}: {size}")
        self._words: list[int] = [0] * (size // WORD)

    @property
    def size_bytes(self) -> int:
        return len(self._words) * WORD

    def load(self, addr: int) -> int:
        """Read one 32-bit word at a word-aligned byte address."""
        self._check(addr)
        return self._words[addr // WORD]

    def store(self, addr: int, value: int) -> None:
        """Write one 32-bit word at a word-aligned byte address.

        value is masked to 32 bits before storage.
        """
        self._check(addr)
        self._words[addr // WORD] = to_word(value)

    def load_image(self, image: BinaryImage) -> None:
        """Write all (address, word) pairs from a BinaryImage into memory."""
        for addr, word in image.words():
            self.store(addr, word)

    def load_bytes(self, raw: bytes) -> None:
        """Populate from a flat big-endian binary blob starting at byte address 0.

        Extra bytes beyond size_bytes are silently ignored.
        """
        n = min(len(raw) // WORD, len(self._words))
        self._words[:n] = list(struct.unpack_from(f">{n}I", raw))

    def _check(self, addr: int) -> None:
        if addr % WORD:
            raise ValueError(f"unaligned access at {addr:#010x}")
        if not (0 <= addr < self.size_bytes):
            raise ValueError(f"address out of range: {addr:#010x}")


# ── Binary image ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BinaryImage:
    """Complete memory image produced by the translator.

    Immutable: constructed once after Pass 2, then serialized to .bin / .lst.
    Tuples (not lists) are used for code and data to enforce immutability.
    """

    reset_word: int        # at RESET (0x0): JMP main
    ivec_in: int           # at IVEC_IN (0x4): handler address; 0 before set-isr
    code: tuple[int, ...]  # words at CODE_BASE, CODE_BASE+4, CODE_BASE+8, …
    data: tuple[int, ...]  # words at DATA_BASE, DATA_BASE+4, DATA_BASE+8, …

    def words(self) -> list[tuple[int, int]]:
        """All (byte_address, word) pairs in ascending address order."""
        pairs: list[tuple[int, int]] = [
            (RESET,   self.reset_word),
            (IVEC_IN, self.ivec_in),
        ]
        for i, w in enumerate(self.code):
            pairs.append((CODE_BASE + i * WORD, w))
        for i, w in enumerate(self.data):
            pairs.append((DATA_BASE + i * WORD, w))
        return pairs

    def to_bytes(self) -> bytes:
        """Serialize to a flat big-endian binary suitable for writing to .bin.

        The output spans [0, DATA_BASE + len(data)*WORD).  The gap between the
        end of code and DATA_BASE is zero-filled (uninitialized static cells).
        If data is empty, the output still covers the full code region up to
        DATA_BASE, keeping the loader logic uniform.
        """
        end = DATA_BASE + len(self.data) * WORD
        buf = bytearray(end)
        for addr, word in self.words():
            struct.pack_into(">I", buf, addr, word & 0xFFFF_FFFF)
        return bytes(buf)

    @classmethod
    def from_bytes(cls, raw: bytes) -> BinaryImage:
        """Deserialize a flat big-endian binary produced by to_bytes()."""
        n = len(raw) // WORD
        if n < 2:
            raise ValueError("binary too short to contain RESET and IVEC_IN")
        words = list(struct.unpack_from(f">{n}I", raw))
        reset_word = words[RESET // WORD]
        ivec_in    = words[IVEC_IN // WORD]
        code_start = CODE_BASE // WORD
        data_start = DATA_BASE // WORD
        code_end   = min(data_start, n)
        data_end   = n
        code = tuple(words[code_start:code_end])
        data = tuple(words[data_start:data_end]) if data_start < n else ()
        return cls(reset_word=reset_word, ivec_in=ivec_in, code=code, data=data)

    def listing(self) -> str:
        """Produce the full .lst debug listing.

        Each line: AAAAAAAA - HHHHHHHH - mnemonic
        The IVEC_IN cell and data cells get descriptive labels, not mnemonics.
        """
        lines: list[str] = []
        for addr, word in self.words():
            if addr == IVEC_IN:
                mnem = f"<vector 0x{word:08X}>"
            elif addr >= DATA_BASE:
                mnem = _data_label(word)
            else:
                mnem = disassemble(word)
            lines.append(f"{addr:08X} - {word:08X} - {mnem}")
        return "\n".join(lines)


def _data_label(word: int) -> str:
    """Format one data-section word for the .lst listing."""
    if 0x20 <= word < 0x7F:      # printable ASCII, stored one-char-per-word
        return f"<data '{chr(word)}'>"
    return f"<data {to_signed(word)}>"


# ── Machine state ──────────────────────────────────────────────────────────────

@dataclass
class MachineState:
    """Complete tick-accurate processor state.

    The machine's do_one_tick() reads and writes this object exactly once per
    tick.  The state is fully consistent between ticks and can be logged or
    inspected at any point without disturbing simulation.
    """

    # ── Architecture-visible registers (ISA §Register Set) ──────────────────

    acc: int = 0
    """Accumulator — single working register for ALU, I/O, and indirect calls."""

    pc: int = RESET
    """Program counter — byte address of the *next* instruction to fetch.
    After T0 it already points past the fetched instruction (PC+4 done in T0)."""

    sp: int = DSTACK_BASE
    """Data-stack pointer — byte address of the *next free slot*.
    Top of stack = mem[sp-WORD]; push: mem[sp]←ACC, sp+=WORD."""

    rsp: int = RSTACK_BASE
    """Return-stack pointer — byte address of the *next free slot*.
    Top of RS = mem[rsp-WORD]; push: mem[rsp]←x, rsp+=WORD."""

    flags: Flags = field(default_factory=Flags)
    """Condition flags: Z, N, C, IE."""

    # ── Internal datapath latches (ISA §Internal Execution Model) ───────────

    ir: int = 0
    """Instruction register — raw 32-bit word latched during T0 (fetch)."""

    tmp: int = 0
    """Scratch latch — used across execute ticks by SWAP, STA, and CALLA."""

    # ── Sequencer ────────────────────────────────────────────────────────────

    step: Step = Step.T0
    """Current micro-step; advances each tick; returns to T0 after last execute."""

    tick: int = 0
    """Global tick counter; incremented after every do_one_tick() call."""

    # ── Interrupt ────────────────────────────────────────────────────────────

    in_trap: bool = False
    """True while the CPU is executing inside an interrupt handler."""

    irq: bool = False
    """Single pending-interrupt latch; set when a scheduled input event fires,
    cleared by the hardware entry sequence (I4).  No queue: only one pending
    trap is held at a time."""

    # ── Simulation ───────────────────────────────────────────────────────────

    halted: bool = False
    """Set by HLT in the decode step; stops the simulation loop."""
