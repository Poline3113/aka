# Timing Model

> Variant: `forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1`
> Basis: the approved ISA Specification and Internal Execution Model.
> Goal: a **tick-accurate** model in which (1) the processor advances exactly one tick per step, (2) simulation can be **paused/observed on any tick**, and (3) **trap interrupts** are scheduled and delivered deterministically by tick.

## Tick Semantics

- A **tick** is one clock cycle — the smallest unit of simulated time. A global counter `TICK` increments by 1 each tick and is the time base for everything (instruction timing, the input schedule, the trap latch).
- A tick is **atomic**: the register transfers assigned to a tick (Internal Execution Model) all take effect at the tick's edge. Within a tick the model performs **at most one** main-memory access **or** one port access.
- State is **observable between ticks**: after each tick the full machine state (`ACC`, `PC`, `SP`, `RSP`, `FLAGS`, `IR`, `TMP`, `step`, `TICK`, `in_trap`) is consistent and may be logged or the simulation halted. There is no "half-executed" instruction visible to the observer — only completed ticks.
- The model is driven by a single loop: `while running: do_one_tick()`. `do_one_tick()` performs exactly the transfers of the current `step`, advances `step` (or returns it to `T0`), and increments `TICK`. Pausing = stopping the loop after any `do_one_tick()`; resuming continues from the saved state.
- Every instruction occupies a whole number of ticks: `fetch(1) + decode(1) + execute(n)`. The processor is never between instructions except at an instruction boundary (after the last execute tick), which is also where interrupts are taken.

Sequencer states (the `step` register): `T0` (fetch), `T1` (decode), `E1…En` (execute), plus the interrupt-entry micro-states `I1…I4`. One tick advances one `step`.

## Timing Rules

1. **One memory/port access per tick.** The number of execute ticks `n` of an instruction equals its number of memory/port accesses, or `1` for a register/ALU-only instruction, or `0` for `HLT`/`NOP`.
2. **Fetch = 1 tick, decode = 1 tick**, unconditionally, for every instruction.
3. **ALU is single-tick.** Any arithmetic/logic result (including `MUL`, `DIV`, `MOD`) is produced within the tick of its execute step; no multi-tick arithmetic.
4. **No overlap.** There is no pipelining or prefetch — fetch of instruction *k+1* never overlaps execution of instruction *k*. (Consistent with `hw` + minimal design; the `superscalar`/`pipeline` complications are out of scope for this variant.)
5. **Branches cost the same taken or not.** A conditional branch is one execute tick whether or not the condition holds.
6. **Interrupt acceptance is free of an extra "poll" tick.** The boundary check is combinational; if a trap is taken, the cost is the 4-tick entry sequence, otherwise fetch of the next instruction proceeds normally.
7. **Halt freezes the clock.** After `HLT` the loop stops; `TICK` does not advance further.

## Fetch Timing

| Tick | `step` | Action | Cost |
|------|--------|--------|------|
| t | `T0` | `IR ← mem[PC]; PC ← PC+4` (one memory read, address = `PC`) | 1 tick |

- Always exactly one tick: a single instruction-memory read.
- `PC` is incremented within the same tick by the address adder; no separate tick.

## Decode Timing

| Tick | `step` | Action | Cost |
|------|--------|--------|------|
| t | `T1` | split `IR` → `opcode/MM/operand`; select execute sequence; prepare `IMM_VAL`, branch `TARGET`, branch condition | 1 tick |

- Always exactly one tick; purely combinational, no memory access, no architectural state change.
- `HLT`/`NOP` terminate at decode (their `execute` length is 0): total 2 ticks.

## Execute Timing

- Execute runs `E1…En`, one tick per step, following the Internal Execution Model tables.
- `n` by class:
  - `n = 0`: `HLT`, `NOP`.
  - `n = 1`: `LD`, `ST`, `LDA`, `PUSH`, `POP`, all arithmetic/logic, `INV`, `JMP`/`JZ`/`JN`/`JC`, `CALL`, `RET`, `EI`, `DI`, `IN`, `OUT`.
  - `n = 2`: `SWAP`, `CALLA`, `TOR`, `FROMR`.
  - `n = 3`: `STA`, `RETI`.
- Each execute tick that touches memory/port consumes the single per-tick access budget; register/ALU-only execute ticks consume none.
- After `En`, `step` returns to `T0` and the **interrupt boundary check** runs (see Interrupt Timing).

## Memory Access Timing

- Main memory is **single-port**: one read **or** one write per tick. Two memory accesses can never occur in the same tick, which is why multi-access instructions (`STA`, `SWAP`, `RETI`, `CALLA`, `TOR`, `FROMR`) span multiple execute ticks.
- A memory access completes within its tick: address is presented (from the address MUX: `PC`/`operand`/`ACC`/`SP±4`/`RSP±4`), and the datum is latched at the tick edge. Access latency is **fixed at 1 tick** (no wait states; the `cache` complication with 10-tick memory is out of scope for this variant).
- Instruction fetch and data access share the one port but never in the same tick (no overlap), so no structural conflict arises.
- Word alignment: every access uses a word-aligned byte address; the model treats one cell as one indivisible 32-bit transfer.

## Port Access Timing

- Ports form a separate space accessed only by `IN`/`OUT`, **one port access per tick**, latency **1 tick** (same as memory).
- **Input availability (schedule-driven):** the model holds an input schedule `[(tick, byte), …]` per input port. At the start of each tick, if `TICK` has reached a scheduled entry, that byte becomes the current value latched in the input port (and remains readable until the next scheduled byte replaces it). Reaching a scheduled tick is also what **raises the trap** (see Interrupt Timing).
  - `IN port` at tick t reads whatever byte is currently latched in that port; it does **not** itself wait for or consume the schedule. Reading data and taking the interrupt are distinct events.
  - If the program reads a port before any byte has been scheduled, it reads the port's defined initial value (implementation-fixed, e.g. 0); there is no blocking and no magic queue.
- **Output:** `OUT port` writes `ACC` to the output port in one tick; the byte is appended to that port's output buffer and reported at end of simulation. Output is polled (no interrupt).

## Interrupt Timing

- **Sources & latch.** For trap input, a scheduled tick raises a request that is held in a single **pending latch** (`IRQ`). The latch records "a trap is waiting"; it is **not** a queue — a second request arriving while one is pending (or while masked) does not stack up beyond the single slot.
- **Recognition point.** The pending latch is examined **only at an instruction boundary** (after `En`, before the next `T0`). Mid-instruction ticks never enter a handler, preserving tick-accurate, restartable state.
- **Acceptance condition.** A trap is accepted when `IRQ = 1` **and** `IE = 1` at the boundary. Otherwise the next instruction is fetched normally and `IRQ` stays latched until a later boundary with `IE = 1`.
- **Entry sequence (4 ticks).** On acceptance the sequencer runs `I1…I4` instead of the next fetch:

  | Tick | `step` | Action | Access |
  |------|--------|--------|--------|
  | t   | `I1` | `mem[RSP] ← PC; RSP ← RSP+4` | mem W |
  | t+1 | `I2` | `mem[RSP] ← ACC; RSP ← RSP+4` | mem W |
  | t+2 | `I3` | `mem[RSP] ← FLAGS; RSP ← RSP+4` | mem W |
  | t+3 | `I4` | `IE ← 0; IRQ ← 0; PC ← mem[vector]; in_trap ← 1` | mem R |

  Entry costs 4 ticks; afterwards normal fetch resumes at the handler's first instruction.
- **Masking & nesting.** `IE = 0` during the handler masks further traps; a trap raised while masked waits in the single pending latch and is taken after the handler returns. Nested handlers do not occur (single slot, masked), which is the chosen "realism" rule.
- **Return (`RETI`, 3 ticks).** Pops `FLAGS`, `ACC`, `PC` in reverse order and sets `IE ← 1`, `in_trap ← 0`. A pending trap (if any) is then taken at the **next** instruction boundary.
- **Observability.** `in_trap` is part of the logged state every tick, so the journal makes clear whether execution is inside a handler. The tick a trap is raised, accepted, and returned from are all distinguishable in the log.
- **Latency.** Worst-case acceptance latency from "raised" to "entry begins" = the remaining ticks of the instruction in progress (bounded by the longest instruction, 5 ticks) plus any time spent with `IE = 0`.

## Instruction Timing Table

`Total = fetch(1) + decode(1) + execute(n)`.

| Instruction | Fetch | Decode | Execute (n) | Total ticks |
|-------------|:-----:|:------:|:-----------:|:-----------:|
| `LD` (IMM / DIR) | 1 | 1 | 1 | 3 |
| `ST` (DIR) | 1 | 1 | 1 | 3 |
| `LDA` (`@`) | 1 | 1 | 1 | 3 |
| `STA` (`!`) | 1 | 1 | 3 | 5 |
| `PUSH` (`DUP`) | 1 | 1 | 1 | 3 |
| `POP` (`DROP`) | 1 | 1 | 1 | 3 |
| `SWAP` | 1 | 1 | 2 | 4 |
| `ADD`/`SUB`/`MUL`/`DIV`/`MOD` (IMM/DIR/STK) | 1 | 1 | 1 | 3 |
| `AND`/`OR`/`XOR` (IMM/DIR/STK) | 1 | 1 | 1 | 3 |
| `INV` | 1 | 1 | 1 | 3 |
| `JMP`/`JZ`/`JN`/`JC` | 1 | 1 | 1 | 3 |
| `CALL` | 1 | 1 | 1 | 3 |
| `CALLA` (`EXECUTE`) | 1 | 1 | 2 | 4 |
| `RET` (`;`) | 1 | 1 | 1 | 3 |
| `TOR` (`>R`) | 1 | 1 | 2 | 4 |
| `FROMR` (`R>`) | 1 | 1 | 2 | 4 |
| `RETI` | 1 | 1 | 3 | 5 |
| `EI` / `DI` | 1 | 1 | 1 | 3 |
| `IN` / `OUT` | 1 | 1 | 1 | 3 |
| `HLT` | 1 | 1 | 0 | 2 |
| `NOP` | 1 | 1 | 0 | 2 |
| *interrupt entry* (hardware event) | — | — | — | 4 |

These totals match the ISA Complete Instruction Table and the Internal Execution Model tick summary.
