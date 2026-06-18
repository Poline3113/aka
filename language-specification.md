# Language Specification

> Variant: `forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1`
> A minimal Forth dialect targeting the accumulator/von Neumann machine defined in the ISA and architecture specs. It is intentionally small: reverse-Polish, one global dictionary, untyped 32-bit cells, null-terminated strings. Exotic Forth features (immediate words, `DOES>`, custom defining words, `CREATE … ,` data words, vocabularies, multitasking, floating point) are **out of scope**.

## Design Goals

1. **Cover the mandatory features only:** procedures, execution tokens, variables, loops, conditionals, strings.
2. **Minimal surprise:** behave like a small classic Forth where it costs nothing; deviate only to simplify (documented below).
3. **Direct mapping to the ISA:** every primitive word compiles to a short, fixed instruction sequence (see ISA "Forth → ISA mapping"). No hidden runtime.
4. **One data type:** the signed 32-bit cell. Addresses, characters, booleans, and execution tokens are all cells.
5. **Stack-based evaluation:** parameters and results flow through the data stack; the return stack holds return addresses and loop indices.

Documented simplifications vs. standard Forth:
- A word's name becomes visible **at the start** of its own definition, so a word may call itself directly (no `RECURSE`).
- No compile-time/immediate-word machinery is exposed; `IF`/`DO`/etc. are recognized by the translator as syntax, not as user-redefinable immediate words.
- Booleans are `0` (false) and `-1` (true), as in Forth.

## Lexical Rules

- **Tokens** are separated by whitespace (space, tab, newline). Whitespace is otherwise insignificant. The language is **case-insensitive** for word names (`DUP` = `dup`).
- **Comments:**
  - `( … )` — inline comment, terminated by `)` (used for stack-effect notes).
  - `\ …` — line comment to end of line.
- **Numbers:** optional `-` then decimal digits, e.g. `0`, `42`, `-7`. A token that parses as a number is a numeric literal; otherwise it is a word name.
- **Characters:** `[char] X` pushes the code of the next character `X` (one cell). (Single, simple form; no multi-char char literals.)
- **String tokens** (the `"` is a delimiter, not whitespace-bound):
  - `c" text"` — define a null-terminated string in data memory; pushes its start address.
  - `." text"` — print `text` immediately.
- **Word names:** any non-whitespace token that is not a number and not a string/char form. Allowed to contain symbols (`+`, `!`, `@`, `<`, `1+`, `0=`, …).
- **Definition delimiters:** `:` and `;` are ordinary tokens recognized by the parser.

## Grammar

EBNF (`{ }` = zero or more, `[ ]` = optional):

```ebnf
program      = { item } ;
item         = definition | variable-def | constant-def | statement ;

definition   = ":" word { statement } ";" ;
variable-def = "variable" word ;
constant-def = number "constant" word ;

statement    = word
             | number
             | tick
             | char-lit
             | string-lit
             | print-lit
             | if-stmt
             | begin-stmt
             | do-stmt ;

tick         = "'" word ;                         (* push execution token *)
char-lit     = "[char]" any-char ;
string-lit   = "c\"" text "\"" ;                  (* push address of c-string *)
print-lit    = ".\"" text "\"" ;                  (* print literal text       *)

if-stmt      = "if" { statement } [ "else" { statement } ] "then" ;
begin-stmt   = "begin" { statement } "until" ;
do-stmt      = "do" { statement } "loop" ;

word         = identifier ;
number       = [ "-" ] digit { digit } ;
```

Semantics:
- **Evaluation strategy:** strict, left-to-right. A `number` pushes its value; a `word` executes; `'` `word` pushes the word's xt.
- **Scope:** a single global dictionary; all definitions, variables, and constants are global and visible after their definition (a word also visible inside itself).
- **Definitions** may appear at top level only (no nested `:` … `;`).
- Control words (`if`/`else`/`then`, `begin`/`until`, `do`/`loop`) are valid only inside a definition or top-level code and must be balanced.

## Variables

- `variable name` allocates **one cell** in the data section and binds `name` to its address.
- Using `name` **pushes the address** ( `-- addr` ). Read/write with `@`/`!`:

```forth
variable counter
0 counter !        \ counter := 0
counter @ 1 + counter !   \ counter := counter + 1
```

- `n constant name` binds `name` to the literal value `n`; using `name` **pushes the value** ( `-- n` ). Constants are compile-time, stored as immediates or in static data by the translator.
- There are no local variables and no arrays in the core language; multi-cell data is built from consecutive `variable`s or addressed manually (the data section is a flat cell array).

## Procedures

- `: name … ;` defines a procedure (a Forth "word"). The body is any sequence of statements; `;` compiles a return.
- Invoking `name` performs a call (`CALL`); `;` performs `RET`. Parameters and results pass on the data stack, described by a stack-effect comment `( in -- out )`.
- **Recursion** is allowed directly (the name is visible within its own body):

```forth
: fact ( n -- n! )
  dup 1 > if
    dup 1 - fact *
  then ;
```

- Procedures may be stored and called indirectly via their execution token (see below).
- The interrupt handler is an ordinary procedure installed with `set-isr` (see Built-in Words / Interrupts).

## Execution Tokens

- An **execution token (xt)** is the entry address of a word, a single cell.
- `' name` ( `-- xt` ) pushes the xt of `name` (compiles to "push the word's address").
- `execute` ( `xt --` ) calls the word whose token is on top of the stack (compiles to `CALLA`).
- xts are ordinary cells: they may be stored in variables, passed as parameters, or compared. This enables simple dynamic dispatch without arrays:

```forth
variable op           \ holds an xt
' + op !              \ select addition
3 4 op @ execute .    \ prints 7
' * op !              \ select multiplication
3 4 op @ execute .    \ prints 12
```

## String Support

- Strings are **null-terminated (C strings)**: a run of character cells ending in a `0` cell, stored in the data section, one character per 32-bit cell.
- `c" text"` ( `-- addr` ) stores the characters of `text` followed by a `0` terminator and pushes the start address.
- `." text"` prints `text` immediately (compiles to a sequence of character outputs / a call to `type` on a stored literal).
- `type` ( `addr --` ) prints the c-string at `addr` (emits each cell as a character until the `0` terminator).
- `strlen` ( `addr -- n` ) returns the length excluding the terminator.
- String traversal advances by **one cell** (`cell+` = `4 +`), consistent with one char per word. The character value `0` cannot appear inside a string (it is the terminator).
- All other string handling (compare, copy, parse-number) is written as user procedures using `@`, `!`, `+`, and the loops below.

## Built-in Words

`P` = primitive (compiles directly to ISA instructions); `L` = library word (defined in a small prelude written in this language, on top of primitives).

### Arithmetic / logic

| Word | Stack effect | Kind | Meaning |
|------|--------------|------|---------|
| `+` `-` `*` `/` `mod` | `( a b -- r )` | P | arithmetic (`/`,`mod` signed) |
| `negate` | `( a -- -a )` | L | `0 swap -` |
| `1+` `1-` | `( a -- r )` | P | `ADD #1` / `SUB #1` |
| `and` `or` `xor` | `( a b -- r )` | P | bitwise |
| `invert` | `( a -- r )` | P | bitwise NOT |
| `=` `<` `>` | `( a b -- f )` | L | comparison → `0`/`-1` |
| `0=` | `( a -- f )` | L | true if zero |

### Stack

| Word | Stack effect | Kind |
|------|--------------|------|
| `dup` | `( a -- a a )` | P |
| `drop` | `( a -- )` | P |
| `swap` | `( a b -- b a )` | P |
| `over` | `( a b -- a b a )` | L |
| `rot` | `( a b c -- b c a )` | L |
| `>r` | `( x -- )` (to return stack) | P |
| `r>` | `( -- x )` (from return stack) | P |

### Memory / variables

| Word | Stack effect | Kind |
|------|--------------|------|
| `@` | `( addr -- v )` | P |
| `!` | `( v addr -- )` | P |
| `variable` `constant` | defining | P |
| `cell+` | `( addr -- addr+4 )` | L |

### I/O (port-mapped, trap input)

| Word | Stack effect | Kind | Meaning |
|------|--------------|------|---------|
| `in@` | `( -- c )` | P | read the input port (`IN <in_port>`, fixed port) |
| `emit` | `( c -- )` | P | output one character (`OUT <out_port> ; POP`) |
| `cr` | `( -- )` | L | emit newline |
| `key` | `( -- c )` | L | next input character delivered by the trap handler (defined in the prelude over `in@`; see the interrupt example) |
| `.` | `( n -- )` | L | print signed number in decimal |
| `type` | `( addr -- )` | L | print c-string |
| `set-isr` | `( xt -- )` | P | install input interrupt handler (`ST <vector> ; POP`) |
| `ei` `di` | `( -- )` | P | enable / disable interrupts |

Notes: `key` is a library word that cooperates with the installed interrupt handler and a one-cell input buffer; the handler reads the port and stores the byte, `key` waits until a byte is available. `.` and `type` build on `emit`.

### Control (syntax, not callable words)

`if … else … then`, `begin … until`, `do … loop`, with `i` ( `-- index` ) inside a `do` loop.

- `if` consumes a flag; runs the `then`-block when true (compiles to `JZ` over the block).
- `begin … until` repeats the body until the flag at `until` is true (compiles to `JZ` back to `begin`).
- `limit start do … loop` counts `i` from `start` up to `limit-1`, `i` pushes the current index (the index and limit are held in translator-allocated memory cells; see Translator Design).

## Examples

### hello world

```forth
: hello ( -- ) ." Hello, World!" cr ;
hello
```

### cat (echo input forever)

```forth
: cat ( -- ) begin key emit 0 until ;   \ flag 0 = never stop
```

### ask name and greet

```forth
: read-line ( addr -- )     \ read chars until newline, store as c-string
  begin
    key dup 10 =            \ ( addr c flag ) flag set on newline
    if   drop 0 swap ! -1   \ store terminator, stop (flag -1)
    else over ! cell+ 0     \ store char, advance, continue (flag 0)
    then
  until ;

variable namebuf            \ buffer start (enough cells follow)
: greet ( -- )
  ." What is your name?" cr
  namebuf read-line
  ." Hello, " namebuf type ." !" cr ;
greet
```

### counted loop: sum 0..n-1

```forth
: sum ( n -- s )
  0 swap                \ s=0, n
  0 do                  \ i = 0 .. n-1
    i +
  loop ;
10 sum .                \ prints 45
```

### recursion: factorial

```forth
: fact ( n -- n! )
  dup 1 > if dup 1 - fact * then ;
5 fact .                \ prints 120
```

### variable + conditional

```forth
variable max
: keep-max ( n -- )     \ store n if it exceeds max
  dup max @ > if max ! else drop then ;
```

### execution tokens: dispatch

```forth
variable op
: apply ( a b -- r ) op @ execute ;
' + op !   3 4 apply .   \ 7
' * op !   3 4 apply .   \ 12
```

### strings

```forth
: greet-forth ( -- )
  c" forth"                  \ ( -- addr ) address of "forth\0"
  dup type cr                \ prints: forth
  strlen . ;                 \ prints: 5
greet-forth
```

(`c" … "` pushes an address inline; to keep a reusable handle, store it in a `variable`, e.g. `variable name`  `c" forth" name !`.)

### interrupt-driven input handler

```forth
variable ch-ready            \ 0 = empty, -1 = full
variable ch                  \ last received character

: on-input ( -- )            \ interrupt handler: read port, buffer it
  in@                        \ ( -- c ) read the input port (fixed port)
  ch !  -1 ch-ready ! ;      \ store char, mark ready

' on-input set-isr           \ install handler
ei                           \ enable interrupts
```

The library word `key` (Built-in Words table) is the consumer side of this handler: it spins on `ch-ready` and returns the buffered character. Its prelude definition is:

```forth
: key ( -- c )               \ wait for a buffered character
  begin ch-ready @ until     \ spin until handler signals
  0 ch-ready !  ch @ ;
```

This illustrates traps + ports + procedures together: `in@` is the `IN <in_port>` primitive (fixed port, `( -- c )`), the handler buffers the byte, and `key` delivers it. Note `in@` takes **no** stack argument — the port is encoded in the instruction, not supplied at runtime.

### variant algorithm sketch (Project Euler, multiples)

```forth
: euler ( limit -- sum )     \ sum of multiples of 3 or 5 below limit
  0 swap                     \ sum=0, limit
  0 do
    i 3 mod 0= i 5 mod 0= or
    if i + then
  loop ;
1000 euler .                 \ prints 233168
```
