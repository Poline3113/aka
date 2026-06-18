# Translator Design

> Variant: `forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1`
> Basis: the approved Architecture, ISA, Internal Execution Model, Timing, and Language specifications.
> Goal: the **simplest** translator that turns Forth source directly into **binary machine code** plus a debug listing. No optimizing compiler: naive code generation, no constant folding, no peephole, no register allocation beyond the fixed `ACC`/stack conventions. Every construct expands to a fixed instruction template.

## Overview

- **Input:** one text file of Forth source.
- **Output:** a **binary image** (`.bin`, real binary words) and a **debug listing** (`.lst`, `<address> - <HEXCODE> - <mnemonic>`), as required by the `binary` variant.
- **Strategy:** a **two-pass assembler-style** translator.
  - *Pass 1 — layout:* prepend the prelude, tokenize, scan definitions into a single global dictionary, assign every instruction an address (all instructions are one 32-bit word, so addresses are just `CODE_BASE + 4·index`), allocate data (variables, strings, loop cells), and emit an intermediate instruction list with **symbolic** operands.
  - *Pass 2 — encode:* resolve every symbol to a number, pack `opcode|MM|operand` into a 32-bit word, write the binary and the listing.
- **Code generation is template-driven:** each primitive word, literal, control word, and definition maps to a hardcoded instruction template (the ISA "Forth → ISA mapping", expanded below). The translator never reasons about values; it only substitutes addresses.
- **No runtime, no threading interpreter:** subroutine-threaded — Forth words become callable routines; calling a word is a `CALL`.

Memory segments are **hardcoded** (allowed by the spec). They are fixed and independent of program size, which keeps address assignment trivial:

| Symbol | Address | Contents |
|--------|---------|----------|
| `RESET` | `0x00000000` | one instruction: `JMP main` (PC resets to 0) |
| `IVEC_IN` | `0x00000004` | input-interrupt vector cell (handler address) |
| `CODE_BASE` | `0x00000008` | code: prelude words, user words, `main` |
| `DATA_BASE` | `0x00001000` | variables, in-memory constants, strings, loop cells, runtime vars |
| `DSTACK_BASE` | `0x00002000` | data stack (grows up `+4`) |
| `RSTACK_BASE` | `0x00003000` | return stack (grows up `+4`) |

The loader initializes `PC=0`, `SP=DSTACK_BASE`, `RSP=RSTACK_BASE`, and `IE` per the startup convention.

## Translation Pipeline

```
source.fth
   │
   ▼  (0) prepend prelude source (library words: over rot = < > 0= 0< negate cr . type strlen key …)
[ full source text ]
   │
   ▼  (1) tokenize  ───────────────► token stream (numbers, words, strings, ' , control)
   │
   ▼  (2) scan definitions ────────► global dictionary (name → kind, slot)
   │
   ▼  (3) layout + codegen ────────► IR list  [(addr, opcode, MM, operand|symbol)]
   │        · assign instruction addresses (CODE_BASE + 4·i)
   │        · allocate variables/strings/loop-cells in DATA segment
   │        · expand each token via its fixed template (symbolic operands)
   │
   ▼  (4) resolve symbols ─────────► every operand is now a number
   │
   ▼  (5) encode ──────────────────► 32-bit words
   │
   ├──► out.bin   (binary image: RESET word, IVEC cell, code words, data words)
   └──► out.lst   (listing: address - hexcode - mnemonic)
```

Passes 1–3 are "Pass 1"; passes 4–5 are "Pass 2". The split exists only to resolve **forward references** (a `CALL` to a word defined later, forward control-flow labels, an `'` taking a not-yet-seen word). Everything else is computed in a single linear walk.

## Source Processing

### Source format
- Plain UTF-8/ASCII text, whitespace-separated tokens, case-insensitive word names (Language Spec).
- A small **prelude** (library words written in this same Forth) is concatenated in front of the user source before tokenizing, so library words become ordinary compiled words.

### Tokenization
A hand-written lexer (no parser generator — "no overengineering") produces a flat token stream. Rules, applied left to right:

| Input | Action |
|-------|--------|
| spaces / tabs / newlines | token separators (discarded) |
| `( … )` | skip through the matching `)` (comment) |
| `\ …` | skip to end of line (comment) |
| `c"` then text then `"` | one **string token** (`STR`, kind = push-address) carrying the literal text |
| `." ` then text then `"` | one **print token** (`PRINT`) carrying the literal text |
| `[char] X` | one **char token** carrying code of `X` |
| `'` followed by a word | one **tick token** carrying the word name |
| digits (optionally leading `-`) | **number token** |
| anything else | **word token** (a name) |

The lexer emits `(type, lexeme)` pairs. It does **not** interpret meaning; that is the dictionary's job.

## Dictionary Construction

A single **global dictionary** (matches Forth's single namespace, Language Spec). Two parts:

1. **Built-in primitives** — a fixed table known to the translator, each mapping a name to an **inline instruction template** (it is *compiled in place*, not called). See Procedure Translation for the table.
2. **Defined names** — discovered while scanning the token stream:

| Source form | Dictionary entry | Slot assigned |
|-------------|------------------|---------------|
| `: name … ;` | `WORD name` | code address of its entry (a label) |
| `variable name` | `VAR name` | one cell in DATA segment |
| `n constant name` | `CONST name` | the literal value `n` (no cell; inlined) |

Construction rule (keeps forward references valid): during the layout walk, when a `:`/`variable`/`constant` head is seen, its **name is registered immediately** (kind known, address/value filled as the body is laid out). Because of this, a later token referring to an earlier-or-later name always resolves to a symbol; the numeric address is patched in Pass 2.

Name lookup order when compiling a word token: **number? → primitive? → defined name (WORD/VAR/CONST)? → else error** ("undefined word"). The error handling is intentionally minimal (Note 1 in the task allows poor diagnostics).

## Symbol Resolution

- Every operand that is not a small immediate is emitted **symbolically** in Pass 1: `CALL <word>`, `LD #<word>` (xt), `LD #<string>` (address), `JZ <Lk>` (control label), `LD #<var>` / `ST <var>` (data address), etc.
- The **symbol table** is filled during layout:
  - `WORD` and control labels → instruction addresses (`CODE_BASE + 4·index`).
  - `VAR`, `STR`, loop cells → DATA addresses (`DATA_BASE + offset`).
  - `CONST` → its literal value.
- **Pass 2** replaces each symbol with its number. A symbol still unknown at the end = "undefined word/label" error.
- All operands must fit the **22-bit** operand field: addresses are small positive values (`< 2^22`), immediates are sign-extended 22-bit. A constant or literal that does not fit is spilled to a DATA cell and accessed with `LD <addr>` (DIR) instead of `LD #imm` — the only value-dependent decision the translator makes, and it is a width check, not an optimization.

## Procedure Translation

### Primitive (inline) templates
Compiled directly to instructions, in place:

| Forth | Template | Forth | Template |
|-------|----------|-------|----------|
| `n` (literal) | `PUSH ; LD #n` | `dup` | `PUSH` |
| `+` | `ADD` (STK) | `drop` | `POP` |
| `-` | `SUB` (STK) | `swap` | `SWAP` |
| `*` | `MUL` (STK) | `@` | `LDA` |
| `/` | `DIV` (STK) | `!` | `STA` |
| `mod` | `MOD` (STK) | `>r` | `TOR` |
| `and` | `AND` (STK) | `r>` | `FROMR` |
| `or` | `OR` (STK) | `1+` | `ADD #1` |
| `xor` | `XOR` (STK) | `1-` | `SUB #1` |
| `invert` | `INV` | `in@` | `IN #in_port` |
| `emit` | `OUT #out_port ; POP` | `ei` / `di` | `EI` / `DI` |
| `set-isr` | `ST IVEC_IN ; POP` | `0=` | branch template ↓ |
| `0<` | branch template ↓ | | |

`0=` / `0<` produce a Forth boolean (`-1`/`0`) and are the only primitives needing a branch:

```
0=:  OR #0 ; JZ L1 ; LD #0 ; JMP L2 ; L1: LD #-1 ; L2:
0<:  OR #0 ; JN L1 ; LD #0 ; JMP L2 ; L1: LD #-1 ; L2:
```

(The `OR #0` refreshes the `Z`/`N` flag from TOS; `LD` then replaces the consumed operand with the boolean. Labels are freshly numbered per use.)

### User words
`: name … ;`:
```
name:            (label = current code address, registered before the body → direct recursion works)
   <compiled body>
   RET
```
A use of `name` compiles to `CALL name`.

### Control flow (fixed, unoptimized templates)
Compile-time control stack matches the structure words; labels are freshly numbered. Each consumes its flag with a per-arm `POP` (the honest cost of caching TOS in `ACC`: the branch must test before `POP` overwrites the flag).

`if … then`:
```
   <pred> ; OR #0 ; JZ Lf ; POP ; <then> ; JMP Le ; Lf: POP ; Le:
```
`if … else … then`:
```
   <pred> ; OR #0 ; JZ Lf ; POP ; <then> ; JMP Le ; Lf: POP ; <else> ; Le:
```
`begin … until` (loop while flag is false):
```
Lb: <body> ; OR #0 ; JZ Ll ; POP ; JMP Ld ; Ll: POP ; JMP Lb ; Ld:
```
`do … loop` (index/limit in translator-allocated DATA cells `IDX_k`/`LIM_k`, `k` = loop nesting depth; counts `start … limit-1`):
```
do:    ST IDX_k ; POP ; ST LIM_k ; POP ; Lb:
i:     PUSH ; LD IDX_k
loop:  PUSH ; LD IDX_k ; ADD #1 ; ST IDX_k ; SUB LIM_k ; JZ Ld ; JN Ld ; POP ; JMP Lb ; Ld: POP
```
(`SUB LIM_k` computes `LIM_k − (index+1)`; positive ⇒ keep looping. The leading `PUSH`/trailing `POP` save and restore the live data TOS, since `loop` itself is data-stack-neutral. Nested loops use distinct `IDX_k`/`LIM_k`; sequential loops at the same depth reuse them.)

## Execution Token Translation

- `' name` ( `-- xt` ): push the word's entry address as an immediate:
  ```
  PUSH ; LD #name        (xt = address of name's routine, resolved in Pass 2)
  ```
- `execute` ( `xt --` ): `CALLA` (indirect call through `ACC`, which consumes the xt and refills TOS).
- Because an xt is just a code address that fits the 22-bit immediate, no token table or special section is needed. xts can be stored (`variable op  ' + op !`) and called (`op @ execute`) like any cell.

## String Translation

Strings are null-terminated, **one character per 32-bit cell**, in the DATA segment.

- `c" text"`: allocate `len(text)+1` cells at the next DATA address, fill them with the character codes followed by a `0` terminator, and compile a push of the start address:
  ```
  PUSH ; LD #straddr
  ```
- `." text"`: store the string the same way, then print it via the library `type`:
  ```
  PUSH ; LD #straddr ; CALL type
  ```
  (`type` emits each cell as a character until the `0` terminator. Reusing `type` keeps `."` trivial and consistent.)
- String literals are pooled in order of appearance; identical strings are **not** deduplicated (no optimization).

## Variable Allocation

- A bump allocator over the DATA segment: `next_data` starts at `DATA_BASE` and advances by 4 bytes (one cell) per allocation.
- `variable name` → allocate one cell, bind `name → addr`. A use of `name` compiles to `PUSH ; LD #addr` (push the address).
- `n constant name` → no cell; bind `name → value`. A use compiles to `PUSH ; LD #n` (push the value), or, if `n` exceeds 22 bits, allocate a cell holding `n` and compile `PUSH ; LD addr` (DIR).
- **Loop cells:** for each nesting depth `k` actually used, allocate `IDX_k`, `LIM_k` once and reuse.
- **Runtime/library cells:** the prelude's `key`/handler machinery allocates its own `ch`, `ch-ready` via ordinary `variable`.
- No initialization beyond what literals/strings write; variables are whatever the loader leaves (programs initialize them, e.g. `0 counter !`).

## Binary Generation

### Word encoding
`opcode(8) | MM(2) | operand(22)`. Fixed opcode assignments:

| op | # | op | # | op | # | op | # |
|----|---|----|---|----|---|----|---|
| `NOP` | 00 | `XOR` | 0F | `JC` | 14 | `DI` | 1C |
| `LD` | 01 | `INV` | 10 | `CALL` | 15 | `IN` | 1D |
| `ST` | 02 | `JMP` | 11 | `CALLA` | 16 | `OUT` | 1E |
| `LDA` | 03 | `JZ` | 12 | `RET` | 17 | `HLT` | 1F |
| `STA` | 04 | `JN` | 13 | `TOR` | 18 | | |
| `PUSH` | 05 | `ADD` | 08 | `FROMR` | 19 | | |
| `POP` | 06 | `SUB` | 09 | `RETI` | 1A | | |
| `SWAP` | 07 | `MUL` | 0A | `EI` | 1B | | |
| | | `DIV` | 0B | `MOD` | 0C | `AND` | 0D | `OR` | 0E |

`MM`: `STK = 00`, `IMM = 01`, `DIR = 10` (ISA addressing modes). Non-mode instructions use `MM = 00`. The operand is the 22-bit address/immediate/port (immediates masked to 22 bits, two's-complement for negatives).

Encoding: `word = (opcode << 24) | (MM << 22) | (operand & 0x3FFFFF)`.

### Output files
- **`out.bin`** — the image as 32-bit **big-endian** words (so a hex dump matches the listing): the `RESET` word at `0x0`, the `IVEC_IN` cell at `0x4`, the code words from `CODE_BASE`, then the DATA words (strings, initialized cells). Uninitialized regions may be omitted or zero-filled per the loader.
- **`out.lst`** — one line per word: `AAAAAAAA - HHHHHHHH - mnemonic ( comment )`, e.g. `0000000C - 01400041 - LD #65`. Data cells are listed as `AAAAAAAA - HHHHHHHH - <data 'c'>`.

### CLI
```
translator <source.fth> <out.bin>      # also writes <out.bin>.lst
```
Matches the task's translator I/O contract (source in, machine code out; listing is the debug companion).

## Translation Examples

### Example 1 — literal, primitive, port output

Source:
```forth
65 emit        \ print 'A'
```
Expansion: `65` → `PUSH ; LD #65`; `emit` → `OUT #1 ; POP`; `main` ends with `HLT`. Listing (`out_port = 1`):
```
00000000 - 11000008 - JMP main
00000004 - 00000000 - <IVEC_IN = 0>
00000008 - 05000000 - PUSH            ( literal 65 )
0000000C - 01400041 - LD #65
00000010 - 1E000001 - OUT #1          ( emit )
00000014 - 06000000 - POP             ( emit )
00000018 - 1F000000 - HLT
```
(`01400041` = opcode `01`, `MM=01` (IMM) → bit pattern `0x00400000`, operand `0x41 = 65`.)

### Example 2 — a word, a call, recursion-safe layout

Source:
```forth
: double ( n -- 2n ) dup + ;
21 double emit
```
Codegen (addresses illustrative, after the prelude):
```
double:                       \ label registered before body
   PUSH                       ( dup )
   ADD                        ( +  , STK )
   RET
main:
   PUSH ; LD #21              ( literal 21 )
   CALL double
   OUT #1 ; POP               ( emit )
   HLT
```
`CALL double` and `JMP main` are the forward/back references resolved in Pass 2.

### Example 3 — conditional

Source:
```forth
: nonneg? ( n -- )  0< if ." neg" then ;
```
Expansion (symbolic; `0<` and `."` shown expanded):
```
nonneg?:
   OR #0 ; JN L1 ; LD #0 ; JMP L2 ; L1: LD #-1 ; L2:    ( 0< )
   OR #0 ; JZ L3 ; POP                                   ( if  )
   PUSH ; LD #s0 ; CALL type                             ( ." neg" )
   JMP L4 ; L3: POP ; L4:                                ( then )
   RET
```
with `s0` a DATA pool entry: cells `'n','e','g',0`.

### Example 4 — variable

Source:
```forth
variable counter
0 counter !
counter @ 1+ counter !
```
- `variable counter` → allocate `counter` at `DATA_BASE` (`0x1000`).
- `0 counter !` → `PUSH ; LD #0 ; PUSH ; LD #0x1000 ; STA`.
- `counter @ 1+ counter !` → `PUSH ; LD #0x1000 ; LDA ; ADD #1 ; PUSH ; LD #0x1000 ; STA`.

### Example 5 — execution token

Source:
```forth
variable op
' + op !
3 4 op @ execute
```
- `' +` → `+` is a **primitive**, so the translator wraps it as a tiny callable stub `plus:` (`ADD ; RET`) the first time its xt is taken, and `' +` compiles to `PUSH ; LD #plus`. (Taking the xt of a primitive is the one case where a primitive also gets a callable copy.)
- `op !` → `PUSH ; LD #op ; STA`.
- `op @ execute` → `PUSH ; LD #op ; LDA ; CALLA`, calling the stored xt with `3 4` already on the data path → result `7`.
