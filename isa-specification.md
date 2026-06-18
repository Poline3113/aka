# ISA Specification

> Variant: `forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1`
> Basis: the approved Architecture Specification (single-accumulator von Neumann core, two memory stacks, port-mapped I/O, trap interrupts, subroutine-threaded Forth).
> Conventions used below:
> - **Word / cell** = 32 bits = 4 bytes. All instructions are exactly **one word**. Addresses are byte addresses, word-aligned (multiples of 4).
> - **DS** = data stack (pointer `SP`); **RS** = return stack (pointer `RSP`). Both pointers address the **next free slot** and grow by `+4`; the top of DS is `mem[SP-4]`.
> - **TOS-in-ACC convention:** the translator keeps the Forth top-of-stack in `ACC`; the in-memory DS holds the elements below it. The ISA does not enforce this — it only provides primitives that make it natural.
> - Flags: `Z` (result == 0), `N` (result < 0, sign bit), `C` (carry/borrow out of add/sub), `IE` (interrupt-enable).
> - Tick model: every instruction = `fetch` (1) + `decode` (1) + `execute` (N). **Exactly one main-memory or port access happens per tick**; register/ALU transfers are free within that tick (or take one tick if no memory access occurs). ALU operations (including `MUL`/`DIV`/`MOD`) are assumed to complete in one tick, as the task permits. Per-instruction tick counts are derived at register-transfer level in the Internal Execution Model document.

## Design Goals

1. **Accumulator-centric.** Every operation reads/writes `ACC`; there is no general register file. I/O passes through `ACC`.
2. **Minimal opcode count.** Stack duplication/discard reuse the spill/fill ops (`DUP = PUSH`, `DROP = POP`); one shared 2-bit *mode* field folds immediate/direct/stack variants into a single opcode per ALU operation.
3. **Forth-complete.** Primitives exist for literals, the data/return stacks, arithmetic/logic, control flow, word call/return, `>R`/`R>`, `@`/`!`, `'`/`EXECUTE`.
4. **Trap-ready.** Hardware interrupt entry plus a single `RETI` exit; explicit interrupt masking.
5. **Port-mapped.** Dedicated `IN`/`OUT` over a separate port address space.
6. **Fixed, regular encoding** suitable for a hardwired control unit and a tick-accurate model.

## Instruction Formats

All instructions are 32 bits wide, word-aligned. There is a single physical layout; fields not used by an opcode are zero.

```
 31      24 23   22 21                                   0
+----------+-------+--------------------------------------+
|  opcode  |  MM   |              operand (22)            |
|  (8)     |  (2)  |        address / immediate / port    |
+----------+-------+--------------------------------------+
```

- **opcode (8 bits):** selects the operation (~32 used).
- **MM (2 bits):** addressing mode, meaningful only for the mode-bearing instructions (`LD`, `ST`, and the ALU/logic group). Ignored otherwise.
- **operand (22 bits):** interpretation depends on opcode/mode:
  - byte **address** (word-aligned) for memory and control-transfer instructions,
  - sign-extended **immediate** (22 → 32 bits) for `IMM` mode,
  - **port number** for `IN`/`OUT`.

Format families (by how the operand is used):

| Family | Used by | Operand meaning |
|--------|---------|-----------------|
| **M** (mode-bearing) | `LD`, `ST`, `ADD`, `SUB`, `MUL`, `DIV`, `MOD`, `AND`, `OR`, `XOR` | per `MM` (IMM / DIR / STK) |
| **A** (address) | `JMP`, `JZ`, `JN`, `JC`, `CALL` | branch/call target address |
| **P** (port) | `IN`, `OUT` | port number |
| **Z** (implied) | `LDA`, `STA`, `PUSH`, `POP`, `SWAP`, `INV`, `CALLA`, `RET`, `TOR`, `FROMR`, `RETI`, `EI`, `DI`, `HLT`, `NOP` | none |

> The concrete bit value assigned to each opcode/mode is a packing detail finalized with the binary encoder; this document fixes the field structure and semantics, which is what ISA users and the codegen need.

## Addressing Modes

The `MM` field selects how a mode-bearing instruction obtains its operand value:

| Mode | `MM` | Effective operand | Notes |
|------|------|-------------------|-------|
| `IMM` (immediate) | `01` | sign-extended 22-bit constant | no data-memory access; used for literals and `n +` style ops |
| `DIR` (direct) | `10` | `mem[operand]` | static variables/constants at a known address |
| `STK` (stack) | `00` | `mem[SP-4]`, then `SP -= 4` (pop NOS) | the Forth-native form: combine `ACC` with the element below it |

Additional **implicit** addressing used by fixed opcodes (no `MM` field needed):

- **ACC-indirect:** `LDA`/`STA` use `ACC` as the effective address (`@`/`!`).
- **Indirect call via ACC:** `CALLA` transfers control to the address in `ACC` (`EXECUTE`).
- **Stack-implied:** `PUSH`/`POP`/`SWAP`/`TOR`/`FROMR` act on the DS/RS tops via `SP`/`RSP`.
- **Absolute (control):** `JMP`/`JZ`/`JN`/`JC`/`CALL` take an absolute target address in the operand field.

`LD` supports `IMM`/`DIR`; `ST` supports `DIR`; the ALU/logic group supports `IMM`/`DIR`/`STK`.

## Data Movement Instructions

| | |
|---|---|
| **`LD` (mode IMM/DIR)** | |
| operands | `#imm` (IMM) or `addr` (DIR) |
| semantics | `IMM`: `ACC ← sext(imm)`. `DIR`: `ACC ← mem[addr]`. |
| side effects | sets `Z`,`N`. (Overwrites `ACC`; to preserve the current TOS the translator emits `PUSH` first.) |
| length | 1 word |
| ticks | IMM: 3, DIR: 3 |

| | |
|---|---|
| **`ST` (mode DIR)** | |
| operands | `addr` |
| semantics | `mem[addr] ← ACC` |
| side effects | none (flags unchanged) |
| length | 1 word |
| ticks | 3 |

| | |
|---|---|
| **`LDA`** (Forth `@`) | |
| operands | — (address in `ACC`) |
| semantics | `ACC ← mem[ACC]` |
| side effects | sets `Z`,`N` |
| length | 1 word |
| ticks | 3 |

| | |
|---|---|
| **`STA`** (Forth `!`, effect `( val addr -- )`) | |
| operands | — (address in `ACC` = TOS, value = NOS) |
| semantics | `t ← mem[SP-4]; SP -= 4; mem[ACC] ← t; SP -= 4; ACC ← mem[SP]` (store value at address, then refill TOS from the element below) |
| side effects | sets `Z`,`N` from refilled `ACC`; consumes value and address |
| length | 1 word |
| ticks | 5 |

| | |
|---|---|
| **`PUSH`** (also Forth `DUP`) | |
| operands | — |
| semantics | `mem[SP] ← ACC; SP += 4` (`ACC` unchanged) |
| side effects | DS grows by one cell |
| length | 1 word |
| ticks | 3 |

| | |
|---|---|
| **`POP`** (also Forth `DROP`) | |
| operands | — |
| semantics | `SP -= 4; ACC ← mem[SP]` |
| side effects | sets `Z`,`N`; DS shrinks by one cell |
| length | 1 word |
| ticks | 3 |

| | |
|---|---|
| **`SWAP`** (Forth `SWAP`) | |
| operands | — |
| semantics | `ACC ↔ mem[SP-4]` |
| side effects | none |
| length | 1 word |
| ticks | 4 |

## Arithmetic Instructions

All compute `ACC ← (operand) op ACC` so that, in `STK` mode with `b` in `ACC` and `a = NOS`, the result matches Forth `( a b -- a op b )`. They set `Z`,`N`; `ADD`/`SUB` also set `C`.

| Mnemonic | modes | semantics (by mode) | side effects | length | ticks (IMM / DIR / STK) |
|----------|-------|---------------------|--------------|--------|--------------------------|
| `ADD` | IMM/DIR/STK | `ACC ← X + ACC` | `Z,N,C` | 1 | 3 / 3 / 3 |
| `SUB` | IMM/DIR/STK | `ACC ← X - ACC` | `Z,N,C` | 1 | 3 / 3 / 3 |
| `MUL` | IMM/DIR/STK | `ACC ← X * ACC` | `Z,N` | 1 | 3 / 3 / 3 |
| `DIV` | IMM/DIR/STK | `ACC ← X / ACC` (signed, trunc) | `Z,N` | 1 | 3 / 3 / 3 |
| `MOD` | IMM/DIR/STK | `ACC ← X mod ACC` | `Z,N` | 1 | 3 / 3 / 3 |

where `X` = `sext(imm)` (IMM) / `mem[addr]` (DIR) / popped `NOS` (STK).
For `STK` mode, the source element is popped (`SP -= 4`) as part of execution.
`C` from `ADD`/`SUB` is the carry/borrow used by the double-precision routines (paired with `JC`).

## Logical Instructions

Bitwise; set `Z`,`N` (`C` unaffected). Same mode set as arithmetic, except `INV` is unary.

| Mnemonic | modes | semantics | side effects | length | ticks (IMM / DIR / STK) |
|----------|-------|-----------|--------------|--------|--------------------------|
| `AND` | IMM/DIR/STK | `ACC ← X & ACC` | `Z,N` | 1 | 3 / 3 / 3 |
| `OR`  | IMM/DIR/STK | `ACC ← X \| ACC` | `Z,N` | 1 | 3 / 3 / 3 |
| `XOR` | IMM/DIR/STK | `ACC ← X ^ ACC` | `Z,N` | 1 | 3 / 3 / 3 |
| `INV` | — | `ACC ← ~ACC` | `Z,N` | 1 | 3 |

`STK` mode pops `NOS` as the source `X`. Forth boolean false = `0`, true = `-1` (all ones), produced via `SUB`/comparison + branch sequences (see Branch Instructions).

## Branch Instructions

Absolute targets (operand = byte address). Branches test the current flags; they do not modify flags or `ACC`. Conditional branches cost the same whether taken or not.

| Mnemonic | operand | semantics | side effects | length | ticks |
|----------|---------|-----------|--------------|--------|-------|
| `JMP` | `addr` | `PC ← addr` | — | 1 | 3 |
| `JZ`  | `addr` | `if Z: PC ← addr` | — | 1 | 3 |
| `JN`  | `addr` | `if N: PC ← addr` | — | 1 | 3 |
| `JC`  | `addr` | `if C: PC ← addr` | — | 1 | 3 |

Compilation notes (no dedicated compare instructions, by minimality):
- `IF … THEN`: evaluate predicate into `ACC`; `JZ` to the `THEN`/`ELSE` target (false = 0).
- `BEGIN … UNTIL`: `JZ` back to `BEGIN` while the flag value is false.
- Signed comparison `a < b`: `SUB.STK` then `JN`; materialize a Forth boolean with a short `LD #-1 / LD #0` + `JMP` sequence when the value itself must be pushed.
- Carry `JC` drives multi-word (double-precision) add/sub.

## Procedure Instructions

Calls/returns use the return stack (`RSP`). `CALL`/`CALLA` push the address of the **next** instruction.

| | |
|---|---|
| **`CALL`** (compiled Forth word call) | |
| operands | `addr` |
| semantics | `mem[RSP] ← PC_next; RSP += 4; PC ← addr` |
| side effects | RS grows by one cell |
| length | 1 word |
| ticks | 3 |

| | |
|---|---|
| **`CALLA`** (Forth `EXECUTE`, effect `( xt -- )`) | |
| operands | — (`xt` in `ACC` = TOS) |
| semantics | `tmp ← ACC; mem[RSP] ← PC_next; RSP += 4; SP -= 4; ACC ← mem[SP]; PC ← tmp` (jump to `xt`, consume it, refill TOS from DS) |
| side effects | RS grows by one cell; DS shrinks by one cell; sets `Z`,`N` from refilled `ACC` |
| length | 1 word |
| ticks | 4 |

| | |
|---|---|
| **`RET`** (Forth `;`) | |
| operands | — |
| semantics | `RSP -= 4; PC ← mem[RSP]` |
| side effects | RS shrinks by one cell |
| length | 1 word |
| ticks | 3 |

| | |
|---|---|
| **`TOR`** (Forth `>R`, effect `( x -- )`) | |
| operands | — |
| semantics | `mem[RSP] ← ACC; RSP += 4; SP -= 4; ACC ← mem[SP]` (move TOS to RS, refill TOS) |
| side effects | RS grows, DS shrinks; sets `Z`,`N` |
| length | 1 word |
| ticks | 4 |

| | |
|---|---|
| **`FROMR`** (Forth `R>`, effect `( -- x )`) | |
| operands | — |
| semantics | `mem[SP] ← ACC; SP += 4; RSP -= 4; ACC ← mem[RSP]` (spill TOS to DS, pull from RS into TOS) |
| side effects | DS grows, RS shrinks; sets `Z`,`N` |
| length | 1 word |
| ticks | 4 |

## Interrupt Instructions

Interrupt **entry is performed by hardware**, not by an instruction: at an instruction boundary with `IE = 1` and a pending trap, the processor executes
`mem[RSP] ← PC; RSP += 4; mem[RSP] ← ACC; RSP += 4; mem[RSP] ← FLAGS; RSP += 4; IE ← 0; PC ← mem[vector]`
(≈ 4 ticks: 3 stack writes + 1 vector read). The handler is ordinary Forth code that ends with `RETI`.

| | |
|---|---|
| **`RETI`** (return from interrupt) | |
| operands | — |
| semantics | `RSP -= 4; FLAGS ← mem[RSP]; RSP -= 4; ACC ← mem[RSP]; RSP -= 4; PC ← mem[RSP]; IE ← 1` |
| side effects | restores saved context; re-enables interrupts |
| length | 1 word |
| ticks | 5 |

| | |
|---|---|
| **`EI`** / **`DI`** | |
| operands | — |
| semantics | `EI: IE ← 1` / `DI: IE ← 0` |
| side effects | changes interrupt masking |
| length | 1 word |
| ticks | 3 / 3 |

Notes: interrupts are masked inside a handler (`IE = 0`); at most one pending trap is latched and serviced after `RETI` (single pending slot, no queue). A handler must leave the **data stack** balanced; its own RS usage sits above the saved frame and must be unwound before `RETI`.

## Port I/O Instructions

Port-mapped I/O over a separate port address space; data passes through `ACC`.

| | |
|---|---|
| **`IN`** | |
| operands | `port` |
| semantics | `ACC ← port[port]` |
| side effects | sets `Z`,`N` |
| length | 1 word |
| ticks | 3 |

| | |
|---|---|
| **`OUT`** | |
| operands | `port` |
| semantics | `port[port] ← ACC` |
| side effects | emits one byte to the device |
| length | 1 word |
| ticks | 3 |

The trap signals data availability; the handler performs the actual `IN`. `OUT` is polled (no interrupt required).

## System Instructions

| | |
|---|---|
| **`HLT`** | |
| operands | — |
| semantics | stop simulation |
| side effects | halts the model |
| length | 1 word |
| ticks | 2 |

| | |
|---|---|
| **`NOP`** | |
| operands | — |
| semantics | no operation |
| side effects | none |
| length | 1 word |
| ticks | 2 |

## Complete Instruction Table

| # | Mnemonic | Operands | Format | Modes | Semantics (summary) | Flags | Len | Ticks |
|---|----------|----------|--------|-------|---------------------|-------|-----|-------|
| 1 | `LD` | `#imm`/`addr` | M | IMM,DIR | `ACC ← imm` / `mem[addr]` | Z,N | 1 | 3/3 |
| 2 | `ST` | `addr` | M | DIR | `mem[addr] ← ACC` | — | 1 | 3 |
| 3 | `LDA` | — | Z | — | `ACC ← mem[ACC]` (`@`) | Z,N | 1 | 3 |
| 4 | `STA` | — | Z | — | `mem[ACC] ← NOS`, drop both, refill TOS (`!`) | Z,N | 1 | 5 |
| 5 | `PUSH` | — | Z | — | `mem[SP]←ACC; SP+=4` (`DUP`) | — | 1 | 3 |
| 6 | `POP` | — | Z | — | `SP-=4; ACC←mem[SP]` (`DROP`) | Z,N | 1 | 3 |
| 7 | `SWAP` | — | Z | — | `ACC ↔ mem[SP-4]` | — | 1 | 4 |
| 8 | `ADD` | `#imm`/`addr`/— | M | IMM,DIR,STK | `ACC ← X + ACC` | Z,N,C | 1 | 3/3/3 |
| 9 | `SUB` | `#imm`/`addr`/— | M | IMM,DIR,STK | `ACC ← X - ACC` | Z,N,C | 1 | 3/3/3 |
| 10 | `MUL` | `#imm`/`addr`/— | M | IMM,DIR,STK | `ACC ← X * ACC` | Z,N | 1 | 3/3/3 |
| 11 | `DIV` | `#imm`/`addr`/— | M | IMM,DIR,STK | `ACC ← X / ACC` | Z,N | 1 | 3/3/3 |
| 12 | `MOD` | `#imm`/`addr`/— | M | IMM,DIR,STK | `ACC ← X mod ACC` | Z,N | 1 | 3/3/3 |
| 13 | `AND` | `#imm`/`addr`/— | M | IMM,DIR,STK | `ACC ← X & ACC` | Z,N | 1 | 3/3/3 |
| 14 | `OR` | `#imm`/`addr`/— | M | IMM,DIR,STK | `ACC ← X \| ACC` | Z,N | 1 | 3/3/3 |
| 15 | `XOR` | `#imm`/`addr`/— | M | IMM,DIR,STK | `ACC ← X ^ ACC` | Z,N | 1 | 3/3/3 |
| 16 | `INV` | — | Z | — | `ACC ← ~ACC` | Z,N | 1 | 3 |
| 17 | `JMP` | `addr` | A | — | `PC ← addr` | — | 1 | 3 |
| 18 | `JZ` | `addr` | A | — | `if Z: PC ← addr` | — | 1 | 3 |
| 19 | `JN` | `addr` | A | — | `if N: PC ← addr` | — | 1 | 3 |
| 20 | `JC` | `addr` | A | — | `if C: PC ← addr` | — | 1 | 3 |
| 21 | `CALL` | `addr` | A | — | push `PC_next` to RS; `PC ← addr` | — | 1 | 3 |
| 22 | `CALLA` | — | Z | — | indirect call to `ACC`; consume xt; refill TOS (`EXECUTE`) | Z,N | 1 | 4 |
| 23 | `RET` | — | Z | — | pop RS into `PC` | — | 1 | 3 |
| 24 | `TOR` | — | Z | — | TOS → RS, refill TOS (`>R`) | Z,N | 1 | 4 |
| 25 | `FROMR` | — | Z | — | RS → TOS, spill old TOS to DS (`R>`) | Z,N | 1 | 4 |
| 26 | `RETI` | — | Z | — | restore `FLAGS`,`ACC`,`PC` from RS; `IE←1` | all | 1 | 5 |
| 27 | `EI` | — | Z | — | `IE ← 1` | IE | 1 | 3 |
| 28 | `DI` | — | Z | — | `IE ← 0` | IE | 1 | 3 |
| 29 | `IN` | `port` | P | — | `ACC ← port[port]` | Z,N | 1 | 3 |
| 30 | `OUT` | `port` | P | — | `port[port] ← ACC` | — | 1 | 3 |
| 31 | `HLT` | — | Z | — | stop simulation | — | 1 | 2 |
| 32 | `NOP` | — | Z | — | no operation | — | 1 | 2 |

**Classification:** single-accumulator (Acc), fixed 1-word instructions, von Neumann, hardwired, tick-accurate; 32 opcodes with a 3-way addressing mode on the ALU/logic group. The set is complete for subroutine-threaded Forth (literals, DS/RS, `@`/`!`, `>R`/`R>`, call/return, `'`/`EXECUTE`), trap-driven port I/O, and C-string processing.

### Forth → ISA mapping (illustrative, not new instructions)

| Forth | Compiles to |
|-------|-------------|
| `n` (literal) | `PUSH ; LD #n` |
| `+` `-` `*` `/` `mod` | `ADD.STK` / `SUB.STK` / `MUL.STK` / `DIV.STK` / `MOD.STK` |
| `dup` `drop` `swap` | `PUSH` / `POP` / `SWAP` |
| `@` `!` | `LDA` / `STA` |
| `>r` `r>` | `TOR` / `FROMR` |
| `: word … ;` | label + body + `RET`; use site → `CALL word` |
| `'` `execute` | push xt (`PUSH ; LD #word`) / `CALLA` |
| `if … then` | predicate → `JZ` past block |
| `in@` ( -- c ) (input primitive) | `IN <in_port>` (fixed port; used inside the trap handler) |
| `emit` ( c -- ) (output) | `OUT <out_port> ; POP` (the `POP` discards the consumed char, since `OUT` does not pop TOS) |
| `set-isr` ( xt -- ) | `ST <vector> ; POP` (the `POP` discards the consumed xt, since `ST` does not pop TOS) |

> Note: bare `OUT`/`ST` leave TOS unchanged (only `port/mem ← ACC`). Any Forth word with a consuming stack effect built on them must append a `POP` to drop the spent operand, as shown for `emit`/`set-isr`.
