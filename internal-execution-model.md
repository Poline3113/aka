# Internal Execution Model

> Variant: `forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1`
> Basis: the approved ISA Specification. Control unit is **hardwired** — control signals are produced by combinational logic from `(opcode, MM, step, FLAGS)`. **No microcode**, no microinstruction memory.
> This document defines, at register-transfer level, how every instruction is fetched, decoded, and executed, tick by tick.

## Conventions and Internal State

Architecturally visible registers (from the spec): `ACC`, `PC`, `SP`, `RSP`, `FLAGS{Z,N,C,IE}`.

Internal (datapath) registers, not visible to the programmer:

| Register | Role |
|----------|------|
| `IR` | instruction register: holds the fetched 32-bit word; supplies `opcode`, `MM`, `operand` to the decoder (combinationally). |
| `TMP` | one-word scratch latch used by multi-step instructions (`SWAP`, `STA`, `CALLA`). |

Memory and ports (single-port von Neumann memory, separate port space):

- `mem[a]` — one main-memory word at byte address `a` (word-aligned). **Exactly one `mem` access per tick.**
- `port[p]` — one I/O port; one port access per tick.
- The **address** presented to memory each tick is selected (by a hardwired MUX) from one of `{PC, operand, ACC, SP, SP-4, RSP, RSP-4}`.
- The **write-data** presented to memory is selected from `{ACC, PC, FLAGS, TMP}`.
- A memory **read** result is latched into one of `{IR, ACC, TMP, PC, FLAGS}`.
- `SP±4`, `RSP±4`, `PC+4` are produced by the address adder/incrementer combinationally and may be latched back into the pointer in the same tick.

Sequencing:

- A small **step counter** `step` (T0, T1, E1, E2, …) drives the hardwired sequencer. It is reset to T0 after the last step of each instruction.
- `FLAGS` update notation `Z,N ← f(ACC)` means `Z = (ACC==0)`, `N = sign(ACC)`. `C` is the carry/borrow out of the adder, set only by `ADD`/`SUB`.
- `PC` already points to the **next** instruction once fetch completes; therefore `CALL`/`CALLA` push `PC` as the return address with no extra arithmetic.
- Notation `a ← x; b ← y` on one line = concurrent transfers within the **same** tick.

## Fetch Cycle

One tick, identical for every instruction. The address source is `PC` (wired straight to memory), so no address-setup tick is needed.

```
T0 (fetch):   IR ← mem[PC];  PC ← PC + 4
```

- One memory read (the instruction word) → `IR`.
- `PC` is incremented by the address adder in the same tick.
- No `FLAGS` change.

## Decode Cycle

One tick, combinational (hardwired). No memory access, no architectural state change.

```
T1 (decode):  opcode, MM, operand  ←  fields of IR        (combinational split)
              control unit selects the execute micro-sequence for this opcode/MM
              immediate path:  IMM_VAL  = SEXT(operand)    (ready on operand bus)
              branch path:     TARGET   = operand          (ready for E1)
              condition path:  evaluate FLAGS for JZ/JN/JC (ready for E1)
```

- The decoder is pure combinational logic; the "tick" exists only so the control signals settle and to keep the model tick-accurate.
- `HLT` and `NOP` complete here: they have **no execute step** (total = 2 ticks). `HLT` asserts a halt latch that stops the sequencer; `NOP` simply returns the step counter to T0.

## Execute Cycle

Execute consists of `E1 … En`, one tick each, where `n` = number of memory/port accesses (or `1` for a purely register/ALU operation, or `0` for `HLT`/`NOP`). The hardwired control asserts, per step, the MUX selects and register-load enables shown in the tables below. After the final execute step the step counter returns to T0 and an **interrupt check** is performed (see Interrupt Entry).

Total ticks per instruction = `1 (fetch) + 1 (decode) + n (execute)`, matching the ISA tick column.

### Instruction Execution Tables

Legend: `IMM_VAL = SEXT(operand)`; reads/writes shown as `← mem[…]` / `mem[…] ←`; `Z,N(,C)` after a step = flags updated that step.

#### Data movement

| Instruction | Step | Register transfers | Mem |
|-------------|------|--------------------|-----|
| `LD` IMM | E1 | `ACC ← IMM_VAL`; `Z,N ← f(ACC)` | — |
| `LD` DIR | E1 | `ACC ← mem[operand]`; `Z,N ← f(ACC)` | R |
| `ST` DIR | E1 | `mem[operand] ← ACC` | W |
| `LDA` (`@`) | E1 | `ACC ← mem[ACC]`; `Z,N ← f(ACC)` | R |
| `STA` (`!`) | E1 | `SP ← SP-4`; `TMP ← mem[SP]` (read value) | R |
|  | E2 | `mem[ACC] ← TMP` (store at address) | W |
|  | E3 | `SP ← SP-4`; `ACC ← mem[SP]`; `Z,N ← f(ACC)` (refill TOS) | R |
| `PUSH` (`DUP`) | E1 | `mem[SP] ← ACC`; `SP ← SP+4` | W |
| `POP` (`DROP`) | E1 | `SP ← SP-4`; `ACC ← mem[SP]`; `Z,N ← f(ACC)` | R |
| `SWAP` | E1 | `TMP ← mem[SP-4]` | R |
|  | E2 | `mem[SP-4] ← ACC`; `ACC ← TMP` | W |

#### Arithmetic and logic

`X` is the second operand by mode: `IMM → IMM_VAL`, `DIR → mem[operand]`, `STK → mem[SP-4]` (with `SP ← SP-4`). All write `ACC` and set `Z,N`; `ADD`/`SUB` also set `C`.

| Instruction | Step (mode) | Register transfers | Mem |
|-------------|-------------|--------------------|-----|
| `ADD`/`SUB`/`MUL`/`DIV`/`MOD`/`AND`/`OR`/`XOR` | E1 (IMM) | `ACC ← IMM_VAL op ACC`; `Z,N(,C)` | — |
|  | E1 (DIR) | `ACC ← mem[operand] op ACC`; `Z,N(,C)` | R |
|  | E1 (STK) | `SP ← SP-4`; `ACC ← mem[SP] op ACC`; `Z,N(,C)` | R |
| `INV` | E1 | `ACC ← ~ACC`; `Z,N ← f(ACC)` | — |

(`op` and the carry behavior are fixed per opcode; the ALU performs the operation in one tick, including `MUL`/`DIV`/`MOD`, as permitted.)

#### Branch

The branch target `TARGET = operand` and the condition were prepared in decode.

| Instruction | Step | Register transfers | Mem |
|-------------|------|--------------------|-----|
| `JMP` | E1 | `PC ← operand` | — |
| `JZ` | E1 | `if Z: PC ← operand` | — |
| `JN` | E1 | `if N: PC ← operand` | — |
| `JC` | E1 | `if C: PC ← operand` | — |

#### Procedure

`PC` already holds the return address (next instruction) entering execute.

| Instruction | Step | Register transfers | Mem |
|-------------|------|--------------------|-----|
| `CALL` | E1 | `mem[RSP] ← PC`; `RSP ← RSP+4`; `PC ← operand` | W |
| `CALLA` (`EXECUTE`) | E1 | `TMP ← ACC`; `mem[RSP] ← PC`; `RSP ← RSP+4` | W |
|  | E2 | `SP ← SP-4`; `ACC ← mem[SP]`; `PC ← TMP`; `Z,N ← f(ACC)` | R |
| `RET` (`;`) | E1 | `RSP ← RSP-4`; `PC ← mem[RSP]` | R |
| `TOR` (`>R`) | E1 | `mem[RSP] ← ACC`; `RSP ← RSP+4` | W |
|  | E2 | `SP ← SP-4`; `ACC ← mem[SP]`; `Z,N ← f(ACC)` | R |
| `FROMR` (`R>`) | E1 | `mem[SP] ← ACC`; `SP ← SP+4` | W |
|  | E2 | `RSP ← RSP-4`; `ACC ← mem[RSP]`; `Z,N ← f(ACC)` | R |

#### Interrupt and system

| Instruction | Step | Register transfers | Mem |
|-------------|------|--------------------|-----|
| `RETI` | E1 | `RSP ← RSP-4`; `FLAGS ← mem[RSP]` | R |
|  | E2 | `RSP ← RSP-4`; `ACC ← mem[RSP]` | R |
|  | E3 | `RSP ← RSP-4`; `PC ← mem[RSP]`; `IE ← 1` | R |
| `EI` | E1 | `IE ← 1` | — |
| `DI` | E1 | `IE ← 0` | — |
| `HLT` | — | (no execute step) assert halt latch in decode; sequencer stops | — |
| `NOP` | — | (no execute step) `step ← T0` | — |

#### Port I/O

| Instruction | Step | Register transfers | Mem/Port |
|-------------|------|--------------------|----------|
| `IN` | E1 | `ACC ← port[operand]`; `Z,N ← f(ACC)` | port R |
| `OUT` | E1 | `port[operand] ← ACC` | port W |

### Interrupt Entry (hardware, between instructions)

Not an instruction. After the last execute step, with `IE = 1` and a trap pending, the sequencer runs this fixed 4-tick sequence instead of the next fetch, then resumes normal fetch at the handler:

```
I1:  mem[RSP] ← PC;    RSP ← RSP+4        (save return address)        W
I2:  mem[RSP] ← ACC;   RSP ← RSP+4        (save accumulator)           W
I3:  mem[RSP] ← FLAGS; RSP ← RSP+4        (save flags)                 W
I4:  IE ← 0;  PC ← mem[vector]            (mask + load handler entry)  R
```

- Recognized **only at an instruction boundary** (never mid-instruction), satisfying the tick-accurate requirement.
- `IE ← 0` masks further interrupts; the single pending latch holds at most one trap, serviced after `RETI` re-enables `IE`.
- `RETI` (above) is the exact inverse, popping `FLAGS`, `ACC`, `PC` in reverse order.

### Tick summary (cross-check with ISA)

| n (execute) | Instructions | Total ticks |
|-------------|--------------|-------------|
| 0 | `HLT`, `NOP` | 2 |
| 1 | `LD`, `ST`, `LDA`, `PUSH`, `POP`, all ALU/logic, `INV`, `JMP`, `JZ`, `JN`, `JC`, `CALL`, `RET`, `EI`, `DI`, `IN`, `OUT` | 3 |
| 2 | `SWAP`, `CALLA`, `TOR`, `FROMR` | 4 |
| 3 | `STA`, `RETI` | 5 |
| (4, entry) | hardware interrupt entry | 4 (event, not an instruction) |

All counts equal `fetch(1) + decode(1) + execute(n)`, consistent with the ISA Complete Instruction Table.
