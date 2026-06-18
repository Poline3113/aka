"""Unit tests for the tick-accurate machine simulator."""
import pytest

from forth_acc.isa import (
    BinaryImage, CODE_BASE, DATA_BASE, DSTACK_BASE, MM, Op, RSTACK_BASE, WORD,
    encode, to_signed,
)
from forth_acc.machine import Machine, simulate
from forth_acc.translator import translate


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_image(*code: int, data: tuple[int, ...] = ()) -> BinaryImage:
    """Wrap raw code words in a BinaryImage; appends HLT automatically."""
    words = list(code) + [encode(Op.HLT, MM.STK, 0)]
    reset = encode(Op.JMP, MM.STK, CODE_BASE)
    # JMP lands at CODE_BASE+WORD (after RESET vector), so put actual code there
    full_code = [words[0]] + words  # CODE_BASE = JMP target = CODE_BASE (=first word)
    # Simpler: just use translate to get past the reset-vector indirection
    return BinaryImage(
        reset_word=encode(Op.JMP, MM.STK, CODE_BASE),
        ivec_in=0,
        code=tuple(words),
        data=data,
    )


def _run(*code: int, inp=None, ticks: int = 10_000) -> tuple[list[int], object]:
    img = _make_image(*code)
    out, _, st = simulate(img, input_schedule=inp, max_ticks=ticks)
    return out, st


# ── Data movement ─────────────────────────────────────────────────────────────

class TestDataMovement:
    def test_ld_imm_and_out(self):
        out, st = _run(
            encode(Op.LD,  MM.IMM, 65),
            encode(Op.OUT, MM.STK,  1),
        )
        assert out == [65] and st.halted

    def test_push_and_pop(self):
        out, st = _run(
            encode(Op.LD,   MM.IMM, 65),
            encode(Op.PUSH, MM.STK,  0),
            encode(Op.LD,   MM.IMM,  0),
            encode(Op.POP,  MM.STK,  0),
            encode(Op.OUT,  MM.STK,  1),
        )
        assert out == [65] and st.halted

    def test_swap(self):
        out, st = _run(
            encode(Op.LD,   MM.IMM, 65),   # ACC=65
            encode(Op.PUSH, MM.STK,  0),   # DS=[65]
            encode(Op.LD,   MM.IMM, 66),   # ACC=66
            encode(Op.SWAP, MM.STK,  0),   # ACC↔NOS → ACC=65, DS=[66]
            encode(Op.OUT,  MM.STK,  1),   # output 65
        )
        assert out == [65] and st.halted

    def test_lda(self):
        # Store 99 at DATA_BASE then load it via LDA
        img = BinaryImage(
            reset_word=encode(Op.JMP, MM.STK, CODE_BASE),
            ivec_in=0,
            code=(
                encode(Op.LD,  MM.IMM, DATA_BASE),  # ACC = addr
                encode(Op.LDA, MM.STK, 0),           # ACC = mem[addr]
                encode(Op.OUT, MM.STK, 1),
                encode(Op.HLT, MM.STK, 0),
            ),
            data=(99,),
        )
        out, _, st = simulate(img)
        assert out == [99] and st.halted

    def test_sta(self):
        # Write via STA then read back with ST/LD
        code = translate(
            "variable x  42 x !  x @ emit"
        )
        out, _, st = simulate(code)
        assert out == [42] and st.halted


# ── ALU ──────────────────────────────────────────────────────────────────────

class TestALU:
    def test_add_stk(self):
        out, st = _run(
            encode(Op.LD,   MM.IMM, 3),
            encode(Op.PUSH, MM.STK, 0),
            encode(Op.LD,   MM.IMM, 4),
            encode(Op.ADD,  MM.STK, 0),
            encode(Op.OUT,  MM.STK, 1),
        )
        assert out == [7]

    def test_sub_stk(self):
        out, _ = _run(
            encode(Op.LD,   MM.IMM, 10),
            encode(Op.PUSH, MM.STK,  0),
            encode(Op.LD,   MM.IMM,  3),
            encode(Op.SUB,  MM.STK,  0),   # NOS-TOS = 10-3 = 7
            encode(Op.OUT,  MM.STK,  1),
        )
        assert out == [7]

    def test_sub_imm(self):
        # SUB IMM: x=imm, a=ACC → result = imm - ACC = 3 - 10 = -7
        _, st = _run(
            encode(Op.LD,  MM.IMM, 10),
            encode(Op.SUB, MM.IMM,  3),
        )
        assert to_signed(st.acc) == -7

    def test_mul(self):
        out, _ = _run(
            encode(Op.LD,   MM.IMM, 6),
            encode(Op.PUSH, MM.STK, 0),
            encode(Op.LD,   MM.IMM, 7),
            encode(Op.MUL,  MM.STK, 0),
            encode(Op.OUT,  MM.STK, 1),
        )
        assert out == [42]

    def test_div_truncates(self):
        out, _ = _run(
            encode(Op.LD,   MM.IMM, 10),
            encode(Op.PUSH, MM.STK,  0),
            encode(Op.LD,   MM.IMM,  3),
            encode(Op.DIV,  MM.STK,  0),   # 10/3 = 3 (truncate)
            encode(Op.OUT,  MM.STK,  1),
        )
        assert out == [3]

    def test_mod(self):
        out, _ = _run(
            encode(Op.LD,   MM.IMM, 10),
            encode(Op.PUSH, MM.STK,  0),
            encode(Op.LD,   MM.IMM,  3),
            encode(Op.MOD,  MM.STK,  0),   # 10 mod 3 = 1
            encode(Op.OUT,  MM.STK,  1),
        )
        assert out == [1]

    def test_and(self):
        out, _ = _run(
            encode(Op.LD,   MM.IMM, 0xFF),
            encode(Op.PUSH, MM.STK,   0),
            encode(Op.LD,   MM.IMM, 0x0F),
            encode(Op.AND,  MM.STK,   0),
            encode(Op.OUT,  MM.STK,   1),
        )
        assert out == [0x0F]

    def test_or(self):
        out, _ = _run(
            encode(Op.LD,   MM.IMM, 0xF0),
            encode(Op.PUSH, MM.STK,   0),
            encode(Op.LD,   MM.IMM, 0x0F),
            encode(Op.OR,   MM.STK,   0),
            encode(Op.OUT,  MM.STK,   1),
        )
        assert out == [0xFF]

    def test_inv(self):
        out, _ = _run(
            encode(Op.LD,  MM.IMM, 0),
            encode(Op.INV, MM.STK, 0),    # ~0 = 0xFFFF_FFFF
            encode(Op.OUT, MM.STK, 1),
        )
        assert out == [0xFF]   # OUT masks to low 8 bits


# ── Branches ─────────────────────────────────────────────────────────────────

class TestBranches:
    def test_jz_taken(self):
        img = translate("0 if 65 emit then 66 emit")
        out, _, st = simulate(img)
        assert out == [66] and st.halted

    def test_jz_not_taken(self):
        img = translate("1 if 65 emit then 66 emit")
        out, _, st = simulate(img)
        assert out == [65, 66] and st.halted

    def test_jn_taken(self):
        img = translate("-1 0< if 65 emit then")
        out, _, st = simulate(img)
        assert out == [65] and st.halted

    def test_jn_not_taken(self):
        img = translate("1 0< if 65 emit then")
        out, _, st = simulate(img)
        assert out == [] and st.halted


# ── CALL / RET / stack ────────────────────────────────────────────────────────

class TestCallRet:
    def test_simple_call(self):
        img = translate(": double dup + ; 21 double emit")
        out, _, st = simulate(img)
        assert out == [42] and st.halted

    def test_nested_call(self):
        img = translate(": inc 1 + ; : inc2 inc inc ; 63 inc2 emit")
        out, _, st = simulate(img)
        assert out == [65] and st.halted

    def test_tor_fromr(self):
        # 65 66 >r: saves 66 to RS, ACC=65. emit: outputs 65. r>: restores 66. emit: outputs 66.
        img = translate("65 66 >r emit r> emit")
        out, _, st = simulate(img)
        assert out == [65, 66] and st.halted

    def test_sp_balanced_after_emit(self):
        # emit consumes TOS, so SP returns to DSTACK_BASE after "N emit"
        img = translate("65 emit")
        _, _, st = simulate(img)
        assert st.halted and st.sp == DSTACK_BASE


# ── Tick counting ─────────────────────────────────────────────────────────────

class TestTicks:
    def test_hlt_takes_2_ticks(self):
        img = BinaryImage(
            reset_word=encode(Op.JMP, MM.STK, CODE_BASE),
            ivec_in=0,
            code=(encode(Op.HLT, MM.STK, 0),),
            data=(),
        )
        # JMP(3) + HLT(2) = 5 ticks
        _, _, st = simulate(img)
        assert st.tick == 5

    def test_ld_out_hlt_ticks(self):
        img = BinaryImage(
            reset_word=encode(Op.JMP, MM.STK, CODE_BASE),
            ivec_in=0,
            code=(
                encode(Op.LD,  MM.IMM, 65),
                encode(Op.OUT, MM.STK,  1),
                encode(Op.HLT, MM.STK,  0),
            ),
            data=(),
        )
        # JMP(3) + LD(3) + OUT(3) + HLT(2) = 11
        _, _, st = simulate(img)
        assert st.tick == 11


# ── Interrupt ─────────────────────────────────────────────────────────────────

class TestInterrupt:
    def test_irq_fires_when_ie_set(self):
        src = """
variable flag
variable val

: isr  in@ val !  -1 flag !  reti ;
' isr set-isr
ei
begin flag @ until
val @ emit
"""
        img = translate(src)
        out, _, st = simulate(img, input_schedule=[(100, 65)])
        assert out == [65] and st.halted

    def test_irq_does_not_fire_when_ie_clear(self):
        # With IE=0 the interrupt should never trigger; program loops forever
        src = """
variable flag
: isr  -1 flag !  reti ;
' isr set-isr
begin flag @ until
65 emit
"""
        img = translate(src)
        _, _, st = simulate(img, input_schedule=[(50, 65)], max_ticks=5000)
        assert not st.halted   # ran out of ticks — never broke out of loop

    def test_reti_restores_ie(self):
        src = """
variable count

: isr  count @ 1 + count !  reti ;
' isr set-isr
ei
begin count @ 3 - 0= until
count @ 48 + emit
"""
        img = translate(src)
        schedule = [(100, 1), (500, 2), (900, 3)]
        out, _, st = simulate(img, input_schedule=schedule, max_ticks=5_000_000)
        assert out == [51] and st.halted   # 3 + 48 = 51 = '3'
