"""Golden (end-to-end) tests: translate + simulate → expected output.

All tests use the full prelude.fth so the standard library is available.
"""
import pathlib
import pytest

from forth_acc.machine import simulate
from forth_acc.translator import translate

PRELUDE = (pathlib.Path(__file__).parent.parent / "prelude.fth").read_text(encoding="utf-8")


def run(src: str, inp=None, ticks: int = 2_000_000) -> tuple[str, bool]:
    img = translate(src, prelude=PRELUDE)
    out, _, st = simulate(img, input_schedule=inp, max_ticks=ticks)
    return "".join(chr(b) for b in out), st.halted


# ── Basic output ──────────────────────────────────────────────────────────────

class TestOutput:
    def test_emit_single(self):
        r, ok = run("65 emit")
        assert r == "A" and ok

    def test_emit_sequence(self):
        r, ok = run("72 101 108 108 111 emit emit emit emit emit")
        assert r == "olleH" and ok   # emitted in TOS-first order

    def test_type_hello(self):
        r, ok = run('c" Hello, World!" type')
        assert r == "Hello, World!" and ok

    def test_type_empty(self):
        r, ok = run('c" " type')
        assert r == "" and ok

    def test_cr(self):
        r, ok = run("65 emit cr 66 emit")
        assert r == "A\nB" and ok

    def test_print_string(self):
        r, ok = run('." Hi"')
        assert r == "Hi" and ok


# ── Decimal output (.digit) ───────────────────────────────────────────────────

class TestDot:
    @pytest.mark.parametrize("n,expected", [
        (0,      "0"),
        (1,      "1"),
        (9,      "9"),
        (10,     "10"),
        (42,     "42"),
        (100,    "100"),
        (999,    "999"),
        (-1,     "-1"),
        (-7,     "-7"),
        (-100,   "-100"),
        (1000,   "1000"),
    ])
    def test_dot(self, n: int, expected: str) -> None:
        r, ok = run(f"{n} .")
        assert r == expected and ok


# ── Stack words ───────────────────────────────────────────────────────────────

class TestStackOps:
    def test_dup(self):
        r, ok = run("65 dup emit emit")
        assert r == "AA" and ok

    def test_drop(self):
        r, ok = run("65 66 drop emit")
        assert r == "A" and ok

    def test_swap(self):
        r, ok = run("65 66 swap emit emit")
        assert r == "AB" and ok

    def test_over(self):
        r, ok = run("65 66 over emit emit emit")
        assert r == "ABA" and ok

    def test_rot(self):
        r, ok = run("65 66 67 rot emit emit emit")
        assert r == "ACB" and ok

    def test_negate(self):
        r, ok = run("5 negate .")
        assert r == "-5" and ok

    def test_strlen_ascii(self):
        r, ok = run(': f c" Hello" strlen . ; f')
        assert r == "5" and ok

    def test_strlen_empty(self):
        r, ok = run(': f c" " strlen . ; f')
        assert r == "0" and ok


# ── Arithmetic ────────────────────────────────────────────────────────────────

class TestArithmetic:
    def test_add(self):
        r, ok = run("3 4 + .")
        assert r == "7" and ok

    def test_sub(self):
        r, ok = run("10 3 - .")
        assert r == "7" and ok

    def test_mul(self):
        r, ok = run("6 7 * .")
        assert r == "42" and ok

    def test_div_truncates(self):
        r, ok = run("10 3 / .")
        assert r == "3" and ok

    def test_mod(self):
        r, ok = run("10 3 mod .")
        assert r == "1" and ok

    def test_cell_plus(self):
        r, ok = run("4096 cell+ .")
        assert r == "4100" and ok   # 4096 + 4 = 4100


# ── Comparisons ───────────────────────────────────────────────────────────────

class TestComparison:
    def test_eq_true(self):
        r, ok = run("3 3 = if 89 emit then")
        assert r == "Y" and ok

    def test_eq_false(self):
        r, ok = run("3 4 = if 89 emit then")
        assert r == "" and ok

    def test_lt_true(self):
        r, ok = run("3 4 < if 89 emit then")
        assert r == "Y" and ok

    def test_lt_false(self):
        r, ok = run("4 3 < if 89 emit then")
        assert r == "" and ok

    def test_gt_true(self):
        r, ok = run("4 3 > if 89 emit then")
        assert r == "Y" and ok

    def test_0eq_true(self):
        r, ok = run("0 0= if 89 emit then")
        assert r == "Y" and ok

    def test_0lt_true(self):
        r, ok = run("-1 0< if 89 emit then")
        assert r == "Y" and ok


# ── Control flow ──────────────────────────────────────────────────────────────

class TestControlFlow:
    def test_if_then_true(self):
        r, ok = run("1 if 65 emit then")
        assert r == "A" and ok

    def test_if_then_false(self):
        r, ok = run("0 if 65 emit then")
        assert r == "" and ok

    def test_if_else_then_true(self):
        r, ok = run("1 if 65 emit else 66 emit then")
        assert r == "A" and ok

    def test_if_else_then_false(self):
        r, ok = run("0 if 65 emit else 66 emit then")
        assert r == "B" and ok

    def test_do_loop_basic(self):
        r, ok = run("3 0 do 65 emit loop")
        assert r == "AAA" and ok

    def test_do_loop_uses_i(self):
        # i=0→'A', i=1→'B', i=2→'C'
        r, ok = run("3 0 do i 65 + emit loop")
        assert r == "ABC" and ok

    def test_do_loop_range(self):
        # 68 65 do → i=65,66,67 → 'A','B','C'
        r, ok = run("68 65 do i emit loop")
        assert r == "ABC" and ok

    def test_begin_until(self):
        # count 65 up to (not including) 68, emit each
        r, ok = run("65 begin dup emit 1 + dup 68 = until drop")
        assert r == "ABC" and ok

    def test_nested_if(self):
        r, ok = run("1 if 1 if 65 emit then then")
        assert r == "A" and ok

    def test_nested_do_loop(self):
        r, ok = run("2 0 do 2 0 do 65 emit loop loop")
        assert r == "AAAA" and ok

    def test_word_definition_recursive(self):
        # .digits is recursive; test it end-to-end through '.'
        r, ok = run("12345 .")
        assert r == "12345" and ok


# ── Variables and memory ──────────────────────────────────────────────────────

class TestMemory:
    def test_variable_store_fetch(self):
        r, ok = run("variable x  42 x !  x @ .")
        assert r == "42" and ok

    def test_constant(self):
        r, ok = run("42 constant answer  answer .")
        assert r == "42" and ok

    def test_store_and_type(self):
        # Write a char to a known address and read it back
        r, ok = run("variable buf  65 buf !  buf @ emit")
        assert r == "A" and ok


# ── Interrupt-driven I/O ──────────────────────────────────────────────────────

class TestInterrupt:
    def test_key_reads_char(self):
        src = """
: on-input  in@ ch !  -1 ch-ready !  reti ;
' on-input set-isr
ei
key emit
"""
        r, ok = run(src, inp=[(100, 72)])
        assert r == "H" and ok

    def test_key_multiple_chars(self):
        src = """
: on-input  in@ ch !  -1 ch-ready !  reti ;
' on-input set-isr
ei
key emit key emit key emit
"""
        r, ok = run(src, inp=[(100, 72), (500, 105), (900, 33)], ticks=5_000_000)
        assert r == "Hi!" and ok


# ── Euler Problem 1 ───────────────────────────────────────────────────────────

class TestEuler1:
    def test_sum_multiples_3_5_below_1000(self):
        src = """
variable total

: mult3or5  ( n -- flag )
  dup 3 mod 0=
  swap 5 mod 0=
  or ;

: solve
  0 total !
  1000 1
  do
    i mult3or5 if
      total @ i + total !
    then
  loop
  total @ . ;

solve
"""
        r, ok = run(src, ticks=5_000_000)
        assert r == "233168" and ok
