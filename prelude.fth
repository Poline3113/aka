\ prelude.fth — library words for forth_acc
\ Variant: forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1

\ ── Stack ────────────────────────────────────────────────────────────────────

: over  ( a b -- a b a )  >r dup r> swap ;

: rot   ( a b c -- b c a )  >r swap r> swap ;

\ ── Arithmetic ────────────────────────────────────────────────────────────────

: negate  ( n -- -n )  0 swap - ;

\ ── Comparison (produce -1/0 booleans) ────────────────────────────────────────

: =   ( a b -- f )  - 0= ;

: <   ( a b -- f )  - 0< ;

: >   ( a b -- f )  swap < ;

\ ── Memory ───────────────────────────────────────────────────────────────────

: cell+  ( addr -- addr+4 )  4 + ;

\ ── I/O ──────────────────────────────────────────────────────────────────────

: cr  ( -- )  10 emit ;

\ type: print null-terminated string  ( addr -- )
: type
  begin
    dup @
    dup if
      emit cell+
      0
    else
      drop -1
    then
  until ;

\ strlen: character count excluding null terminator  ( addr -- n )
: strlen
  dup >r
  begin
    dup @
    dup if
      drop cell+
      0
    else
      drop -1
    then
  until
  r> - 4 / ;

\ ── Interrupt-driven input ───────────────────────────────────────────────────

variable ch-ready
variable ch

\ key: wait for a character delivered by the interrupt handler  ( -- c )
: key
  begin ch-ready @ until
  0 ch-ready !
  ch @ ;

\ ── Decimal output ───────────────────────────────────────────────────────────

\ .digits: print decimal digits of positive n  ( n -- )  [internal]
: .digits
  dup 10 /
  dup if .digits else drop then
  10 mod 48 + emit ;

\ . (dot): print signed decimal number followed by space  ( n -- )
: .
  dup 0< if
    45 emit
    negate
  then
  dup if
    .digits
  else
    48 emit
  then ;
