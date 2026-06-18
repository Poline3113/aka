"""Command-line entry points for forth_acc.

translator  <source.fth>  <out.bin>   — compile Forth source to binary image
machine     <image.bin>               — simulate binary image (stdin → input port)
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── translator entry point ────────────────────────────────────────────────────

def translate_main() -> None:
    if len(sys.argv) != 3:
        print("usage: translator <source.fth> <out.bin>", file=sys.stderr)
        sys.exit(1)

    src_path = Path(sys.argv[1])
    bin_path = Path(sys.argv[2])
    lst_path = bin_path.with_suffix(".lst")

    from forth_acc.translator import TranslateError, translate

    prelude_path = Path(__file__).parent.parent / "prelude.fth"
    prelude = prelude_path.read_text(encoding="utf-8") if prelude_path.exists() else ""

    try:
        src = src_path.read_text(encoding="utf-8")
        image = translate(src, prelude=prelude)
    except TranslateError as exc:
        print(f"translate error: {exc}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as exc:
        print(f"file not found: {exc}", file=sys.stderr)
        sys.exit(1)

    bin_path.write_bytes(image.to_bytes())
    lst_path.write_text(image.listing(), encoding="utf-8")
    print(f"wrote {bin_path} ({len(image.code)} code words, "
          f"{len(image.data)} data words)")
    print(f"wrote {lst_path}")


# ── machine/simulator entry point ─────────────────────────────────────────────

def simulate_main() -> None:
    if len(sys.argv) != 2:
        print("usage: machine <image.bin>", file=sys.stderr)
        sys.exit(1)

    bin_path = Path(sys.argv[1])
    try:
        raw = bin_path.read_bytes()
    except FileNotFoundError:
        print(f"file not found: {bin_path}", file=sys.stderr)
        sys.exit(1)

    from forth_acc.isa import BinaryImage
    from forth_acc.machine import simulate

    image = BinaryImage.from_bytes(raw)

    # Read stdin once upfront; schedule each byte with a 100-tick gap.
    # Tick 0 is the RESET fetch, so first char arrives at tick 100.
    stdin_bytes: list[int] = []
    if not sys.stdin.isatty():
        stdin_bytes = list(sys.stdin.buffer.read())
    schedule = [(100 + i * 100, b) for i, b in enumerate(stdin_bytes)]

    output, _trace, state = simulate(image, input_schedule=schedule)

    sys.stdout.buffer.write(bytes(output))
    sys.stdout.buffer.flush()

    if not state.halted:
        print("\n[simulation: tick limit reached]", file=sys.stderr)
        sys.exit(2)
