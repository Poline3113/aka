"""Unit tests for the Forth translator (tokenizer + Pass 1/2)."""
import pytest

from forth_acc.isa import CODE_BASE, DATA_BASE, MM, Op, WORD, decode, encode, sext22
from forth_acc.translator import TKind, TranslateError, tokenize, translate


# ── Tokenizer ─────────────────────────────────────────────────────────────────

class TestTokenizer:
    def test_integer(self):
        toks = tokenize("42")
        assert len(toks) == 1 and toks[0].kind == TKind.NUM and toks[0].val == 42

    def test_negative(self):
        toks = tokenize("-7")
        assert toks[0].val == -7

    def test_word_lowercased(self):
        toks = tokenize("EMIT")
        assert toks[0].kind == TKind.WORD and toks[0].val == "emit"

    def test_string(self):
        toks = tokenize('c" hello"')
        assert toks[0].kind == TKind.STR and toks[0].val == "hello"

    def test_print_string(self):
        toks = tokenize('." hello"')
        assert toks[0].kind == TKind.PRINT and toks[0].val == "hello"

    def test_char(self):
        toks = tokenize("[char] A")
        assert toks[0].kind == TKind.CHAR and toks[0].val == 65

    def test_tick(self):
        toks = tokenize("' emit")
        assert toks[0].kind == TKind.TICK and toks[0].val == "emit"

    def test_paren_comment_skipped(self):
        toks = tokenize("( this is a comment ) 42")
        assert len(toks) == 1 and toks[0].val == 42

    def test_backslash_comment_skipped(self):
        toks = tokenize("\\ line comment\n42")
        assert len(toks) == 1 and toks[0].val == 42

    def test_multiple_tokens(self):
        toks = tokenize("1 2 +")
        assert [t.val for t in toks] == [1, 2, "+"]

    def test_empty_string_literal(self):
        toks = tokenize('c" "')
        assert toks[0].kind == TKind.STR and toks[0].val == ""


# ── ISA encode/decode roundtrip ───────────────────────────────────────────────

class TestIsaEncodeDecode:
    @pytest.mark.parametrize("op,mm,operand", [
        (Op.LD,   MM.IMM, 65),
        (Op.LD,   MM.DIR, 0x1000),
        (Op.ADD,  MM.STK, 0),
        (Op.JMP,  MM.STK, 0x200),
        (Op.CALL, MM.STK, 0x400),
        (Op.HLT,  MM.STK, 0),
        (Op.OUT,  MM.STK, 1),
        (Op.RETI, MM.STK, 0),
    ])
    def test_roundtrip(self, op: Op, mm: MM, operand: int) -> None:
        word = encode(op, mm, operand)
        op2, mm2, raw, imm = decode(word)
        assert op2 == op
        assert mm2 == mm
        assert raw == (operand & 0x3F_FFFF)

    def test_imm_sign_extension(self):
        word = encode(Op.LD, MM.IMM, -1)
        _, _, _, imm = decode(word)
        assert imm == -1

    def test_sext22_positive(self):
        assert sext22(0x1_0000) == 0x1_0000

    def test_sext22_negative(self):
        assert sext22(0x3F_FFFF) == -1


# ── Translate → BinaryImage ───────────────────────────────────────────────────

class TestTranslate:
    def test_minimal_program_compiles(self):
        img = translate("65 emit")
        assert len(img.code) > 0

    def test_reset_vector_is_jmp(self):
        img = translate("65 emit")
        op, _, _, _ = decode(img.reset_word)
        assert op == Op.JMP

    def test_reset_vector_points_into_code(self):
        img = translate("65 emit")
        _, _, addr, _ = decode(img.reset_word)
        assert addr >= CODE_BASE // WORD  # raw field is word-aligned byte addr

    def test_string_allocates_data(self):
        img = translate('c" AB" drop')
        assert 65 in img.data   # ord('A')
        assert 66 in img.data   # ord('B')
        assert 0 in img.data    # null terminator

    def test_variable_allocates_data_word(self):
        img = translate("variable x")
        assert len(img.data) >= 1

    def test_two_variables_distinct_addresses(self):
        # Each variable allocates one data word; two variables → at least 2 data words
        img = translate("variable x  variable y")
        assert len(img.data) >= 2

    def test_constant_does_not_allocate_data(self):
        img_var  = translate("variable x")
        img_const = translate("42 constant answer")
        assert len(img_const.data) == 0

    def test_word_definition_compiles(self):
        img = translate(": double dup + ; 3 double emit")
        assert len(img.code) > 0

    def test_undefined_word_raises(self):
        with pytest.raises(TranslateError):
            translate("no-such-word")

    def test_unmatched_then_raises(self):
        with pytest.raises(TranslateError):
            translate(": f then ; f")

    def test_unmatched_loop_raises(self):
        with pytest.raises(TranslateError):
            translate(": f loop ; f")

    def test_variable_in_word_raises(self):
        with pytest.raises(TranslateError):
            translate(": f variable x ; f")

    def test_listing_format(self):
        img = translate("65 emit")
        lst = img.listing()
        lines = lst.splitlines()
        assert lines[0].startswith("00000000")   # RESET at addr 0
        assert "JMP" in lines[0]
        assert lines[1].startswith("00000004")   # IVEC_IN at addr 4
        assert "vector" in lines[1]

    def test_to_bytes_roundtrip(self):
        from forth_acc.isa import BinaryImage
        img = translate("65 emit")
        raw = img.to_bytes()
        img2 = BinaryImage.from_bytes(raw)
        assert img2.reset_word == img.reset_word
        assert img2.code[:len(img.code)] == img.code

    def test_prelude_words_available(self):
        prelude = ": over >r dup r> swap ;"
        img = translate("65 66 over emit emit emit", prelude=prelude)
        from forth_acc.machine import simulate
        out, _, _ = simulate(img)
        assert out == [65, 66, 65]

    def test_user_word_named_main(self):
        # User-defined word named 'main' must not conflict with internal __entry__
        img = translate(": main 65 emit ; main")
        from forth_acc.machine import simulate
        out, _, st = simulate(img)
        assert out == [65] and st.halted
