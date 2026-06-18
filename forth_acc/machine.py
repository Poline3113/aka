"""Tick-accurate processor simulator for forth_acc.

Variant: forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1
"""
from __future__ import annotations

import math

from forth_acc.isa import (
    DSTACK_BASE,
    IN_PORT,
    IVEC_IN,
    MM,
    OUT_PORT,
    RSTACK_BASE,
    WORD,
    BinaryImage,
    Flags,
    Instr,
    MachineState,
    Memory,
    Op,
    Step,
    to_signed,
    to_word,
)


def _alu(op: Op, x: int, a: int) -> tuple[int, bool]:
    """Execute one ALU operation: x op a → (result_word, carry).

    x and a are raw 32-bit unsigned words.
    Arithmetic uses signed interpretation; bitwise uses raw bits.
    """
    if op == Op.ADD:
        r = x + a
        return to_word(r), r > 0xFFFF_FFFF
    if op == Op.SUB:
        return to_word(x - a), x < a          # borrow when x < a (unsigned)
    xs, as_ = to_signed(x), to_signed(a)
    if op == Op.MUL:
        return to_word(xs * as_), False
    if op == Op.DIV:
        if as_ == 0:
            return 0, False
        return to_word(math.trunc(xs / as_)), False
    if op == Op.MOD:
        if as_ == 0:
            return 0, False
        q = math.trunc(xs / as_)
        return to_word(xs - q * as_), False
    if op == Op.AND:
        return to_word(x & a), False
    if op == Op.OR:
        return to_word(x | a), False
    # XOR
    return to_word(x ^ a), False


class Machine:
    """Tick-accurate simulator.

    Drive with ``do_one_tick()`` or call ``run()`` for a full simulation.
    Observable state is in ``self.state``; output bytes are in ``self.output``.
    """

    def __init__(
        self,
        image: BinaryImage,
        input_schedule: list[tuple[int, int]] | None = None,
    ) -> None:
        self.mem: Memory = Memory()
        self.mem.load_image(image)
        self.state: MachineState = MachineState(
            sp=DSTACK_BASE,
            rsp=RSTACK_BASE,
        )
        self._sched: list[tuple[int, int]] = sorted(input_schedule or [])
        self._sched_pos: int = 0
        self._ports: dict[int, int] = {}     # port → current latched value
        self.output: list[int] = []          # bytes written to OUT_PORT in order

    # ── public interface ──────────────────────────────────────────────────────

    def do_one_tick(self) -> None:
        """Advance the machine by exactly one clock tick."""
        s = self.state
        if s.halted:
            return

        self._latch_input()

        match s.step:
            case Step.T0:
                s.ir = self.mem.load(s.pc)
                s.pc += WORD
                s.step = Step.T1

            case Step.T1:
                instr = self._cur
                if instr.op == Op.HLT:
                    s.halted = True
                    s.tick += 1
                    return
                if instr.op == Op.NOP:
                    self._end_exec()
                else:
                    s.step = Step.E1

            case Step.E1 | Step.E2 | Step.E3:
                self._execute()

            case Step.I1 | Step.I2 | Step.I3 | Step.I4:
                self._irq_entry()

        s.tick += 1

    def run(
        self,
        max_ticks: int = 10_000_000,
        trace: bool = False,
    ) -> list[str]:
        """Run until HLT or max_ticks.  Returns trace lines if trace=True."""
        log: list[str] = []
        while not self.state.halted and self.state.tick < max_ticks:
            if trace:
                log.append(self._trace_line())
            self.do_one_tick()
        return log

    # ── decode helper (IR always valid during E steps) ────────────────────────

    @property
    def _cur(self) -> Instr:
        return Instr.from_word(self.state.ir)

    # ── timing helpers ────────────────────────────────────────────────────────

    def _latch_input(self) -> None:
        """Latch any bytes scheduled at or before the current tick."""
        s = self.state
        while self._sched_pos < len(self._sched):
            t, b = self._sched[self._sched_pos]
            if t <= s.tick:
                self._ports[IN_PORT] = b & 0xFF
                s.irq = True
                self._sched_pos += 1
            else:
                break

    def _end_exec(self) -> None:
        """Transition after the last execute step: interrupt check or next fetch."""
        s = self.state
        s.step = Step.I1 if (s.irq and s.flags.ie) else Step.T0

    def _alu_src(self, mm: MM, imm: int, raw: int) -> int:
        """Read the second ALU operand according to addressing mode."""
        s = self.state
        if mm == MM.IMM:
            return to_word(imm)
        if mm == MM.DIR:
            return self.mem.load(raw)
        # STK: pop NOS
        s.sp -= WORD
        return self.mem.load(s.sp)

    # ── execute micro-steps ───────────────────────────────────────────────────

    def _execute(self) -> None:  # noqa: C901
        s = self.state
        instr = self._cur
        op, mm, imm, raw = instr.op, instr.mm, instr.imm, instr.raw
        step = s.step

        match op:
            # ── Data movement ─────────────────────────────────────────────────

            case Op.LD:
                s.acc = self.mem.load(raw) if mm == MM.DIR else to_word(imm)
                s.flags.update_zn(s.acc)
                self._end_exec()

            case Op.ST:
                self.mem.store(raw, s.acc)
                self._end_exec()

            case Op.LDA:
                s.acc = self.mem.load(s.acc)
                s.flags.update_zn(s.acc)
                self._end_exec()

            case Op.STA:
                # E1: read value (NOS) into TMP
                # E2: store TMP at address in ACC
                # E3: refill ACC (NNOS)
                if step == Step.E1:
                    s.sp -= WORD
                    s.tmp = self.mem.load(s.sp)
                    s.step = Step.E2
                elif step == Step.E2:
                    self.mem.store(s.acc, s.tmp)
                    s.step = Step.E3
                else:
                    s.sp -= WORD
                    s.acc = self.mem.load(s.sp)
                    s.flags.update_zn(s.acc)
                    self._end_exec()

            case Op.PUSH:
                self.mem.store(s.sp, s.acc)
                s.sp += WORD
                self._end_exec()

            case Op.POP:
                s.sp -= WORD
                s.acc = self.mem.load(s.sp)
                s.flags.update_zn(s.acc)
                self._end_exec()

            case Op.SWAP:
                # E1: read NOS into TMP
                # E2: write old TOS to NOS slot, load TMP into ACC
                if step == Step.E1:
                    s.tmp = self.mem.load(s.sp - WORD)
                    s.step = Step.E2
                else:
                    self.mem.store(s.sp - WORD, s.acc)
                    s.acc = s.tmp
                    self._end_exec()

            # ── Arithmetic / logic ────────────────────────────────────────────

            case Op.ADD | Op.SUB | Op.MUL | Op.DIV | Op.MOD | Op.AND | Op.OR | Op.XOR:
                x = self._alu_src(mm, imm, raw)
                s.acc, s.flags.c = _alu(op, x, s.acc)
                s.flags.update_zn(s.acc)
                self._end_exec()

            case Op.INV:
                s.acc = to_word(~s.acc)
                s.flags.update_zn(s.acc)
                self._end_exec()

            # ── Branch ───────────────────────────────────────────────────────

            case Op.JMP:
                s.pc = raw
                self._end_exec()

            case Op.JZ:
                if s.flags.z:
                    s.pc = raw
                self._end_exec()

            case Op.JN:
                if s.flags.n:
                    s.pc = raw
                self._end_exec()

            case Op.JC:
                if s.flags.c:
                    s.pc = raw
                self._end_exec()

            # ── Procedure ────────────────────────────────────────────────────

            case Op.CALL:
                self.mem.store(s.rsp, s.pc)
                s.rsp += WORD
                s.pc = raw
                self._end_exec()

            case Op.CALLA:
                # E1: save TMP=ACC (target), push return addr to RS
                # E2: pop NOS → new TOS in ACC, jump to TMP
                if step == Step.E1:
                    s.tmp = s.acc
                    self.mem.store(s.rsp, s.pc)
                    s.rsp += WORD
                    s.step = Step.E2
                else:
                    s.sp -= WORD
                    s.acc = self.mem.load(s.sp)
                    s.pc = s.tmp
                    s.flags.update_zn(s.acc)
                    self._end_exec()

            case Op.RET:
                s.rsp -= WORD
                s.pc = self.mem.load(s.rsp)
                self._end_exec()

            case Op.TOR:
                # E1: push ACC to RS
                # E2: pop NOS → new TOS in ACC
                if step == Step.E1:
                    self.mem.store(s.rsp, s.acc)
                    s.rsp += WORD
                    s.step = Step.E2
                else:
                    s.sp -= WORD
                    s.acc = self.mem.load(s.sp)
                    s.flags.update_zn(s.acc)
                    self._end_exec()

            case Op.FROMR:
                # E1: push current TOS to DS (make room for RS value)
                # E2: pop RS top → new TOS in ACC
                if step == Step.E1:
                    self.mem.store(s.sp, s.acc)
                    s.sp += WORD
                    s.step = Step.E2
                else:
                    s.rsp -= WORD
                    s.acc = self.mem.load(s.rsp)
                    s.flags.update_zn(s.acc)
                    self._end_exec()

            # ── Interrupt / system ────────────────────────────────────────────

            case Op.RETI:
                # E1: restore FLAGS; E2: restore ACC; E3: restore PC, set IE=1
                if step == Step.E1:
                    s.rsp -= WORD
                    s.flags = Flags.unpack(self.mem.load(s.rsp))
                    s.step = Step.E2
                elif step == Step.E2:
                    s.rsp -= WORD
                    s.acc = self.mem.load(s.rsp)
                    s.step = Step.E3
                else:
                    s.rsp -= WORD
                    s.pc = self.mem.load(s.rsp)
                    s.flags.ie = True
                    s.in_trap = False
                    self._end_exec()

            case Op.EI:
                s.flags.ie = True
                self._end_exec()

            case Op.DI:
                s.flags.ie = False
                self._end_exec()

            # ── Port I/O ──────────────────────────────────────────────────────

            case Op.IN:
                s.acc = to_word(self._ports.get(raw, 0))
                s.flags.update_zn(s.acc)
                self._end_exec()

            case Op.OUT:
                if raw == OUT_PORT:
                    self.output.append(s.acc & 0xFF)
                self._ports[raw] = s.acc
                self._end_exec()

    # ── interrupt entry (I1–I4) ───────────────────────────────────────────────

    def _irq_entry(self) -> None:
        s = self.state
        match s.step:
            case Step.I1:
                self.mem.store(s.rsp, s.pc)
                s.rsp += WORD
                s.step = Step.I2
            case Step.I2:
                self.mem.store(s.rsp, s.acc)
                s.rsp += WORD
                s.step = Step.I3
            case Step.I3:
                self.mem.store(s.rsp, s.flags.pack())
                s.rsp += WORD
                s.step = Step.I4
            case Step.I4:
                s.flags.ie = False
                s.irq = False
                s.pc = self.mem.load(IVEC_IN)
                s.in_trap = True
                s.step = Step.T0

    # ── trace ─────────────────────────────────────────────────────────────────

    def _trace_line(self) -> str:
        s = self.state
        f = s.flags
        return (
            f"tick={s.tick:6d} {s.step.name:2} "
            f"pc={s.pc:08X} acc={to_signed(s.acc):12d} "
            f"sp={s.sp:08X} rsp={s.rsp:08X} "
            f"Z={int(f.z)} N={int(f.n)} C={int(f.c)} IE={int(f.ie)} "
            f"irq={int(s.irq)} trap={int(s.in_trap)}"
        )


# ── module-level convenience ──────────────────────────────────────────────────

def simulate(
    image: BinaryImage,
    input_schedule: list[tuple[int, int]] | None = None,
    max_ticks: int = 10_000_000,
    trace: bool = False,
) -> tuple[list[int], list[str], MachineState]:
    """Run image to completion.  Returns (output_bytes, trace_lines, final_state)."""
    m = Machine(image, input_schedule)
    log = m.run(max_ticks=max_ticks, trace=trace)
    return m.output, log, m.state
