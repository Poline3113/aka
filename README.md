# Лабораторная работа №4 — Forth-ACC процессор

**Вариант:** `forth | acc | neum | hw | tick | binary | trap | port | cstr | prob1`

## Описание варианта

| Признак | Значение |
|---------|----------|
| Язык    | Forth (подмножество) |
| Архитектура | Аккумулятор (один рабочий регистр ACC) |
| Память  | Фон Нейман (единое адресное пространство для команд и данных) |
| Схема управления | Hardwired (жёсткая логика) |
| Такты   | Tick-accurate (точная модель тактов) |
| Формат  | Binary (бинарный машинный код) |
| Ввод    | Trap-driven (прерывания по вводу) |
| Порты   | Port-mapped I/O |
| Строки  | C-style null-terminated (1 символ = 1 слово по 32 бита) |
| Задача  | prob1 — Euler #1: сумма кратных 3 или 5 меньше 1000 |

---

## Архитектура процессора

### Регистры и память

```
ACC   — аккумулятор (32 бит, знаковый); всегда содержит TOS (top of stack)
PC    — счётчик команд (байтовый адрес)
SP    — указатель стека данных (растёт вверх, +4 на каждый push)
RSP   — указатель стека возвратов (растёт вверх, +4 на каждый push)
FLAGS — Z (ноль), N (отрицательный), C (перенос), IE (разрешение прерываний)
IR    — регистр команды
TMP   — внутренний скрэтч-регистр (не виден программисту)
```

Карта памяти (16 КиБ, 4096 слов по 32 бита):

```
0x0000  RESET       — слово JMP main (точка входа после сброса)
0x0004  IVEC_IN     — адрес обработчика прерывания по вводу
0x0008  CODE_BASE   — код (prelude + пользовательские слова + main)
0x1000  DATA_BASE   — данные (переменные, строки, ячейки цикла)
0x2000  DSTACK_BASE — стек данных (SP начинается здесь)
0x3000  RSTACK_BASE — стек возвратов (RSP начинается здесь)
0x4000  конец памяти
```

### Кодирование команды

```
31      24 23 22 21                  0
+---------+-----+--------------------+
| opcode  | MM  |    operand (22)    |
|  (8)    | (2) |                    |
+---------+-----+--------------------+

word = (opcode << 24) | (MM << 22) | (operand & 0x3FFFFF)
```

Режимы адресации (MM):

| MM   | Значение | Описание                          |
|------|----------|-----------------------------------|
| 00   | STK      | второй операнд — NOS (mem[SP−4])  |
| 01   | IMM      | второй операнд — sign-extend(22)  |
| 10   | DIR      | второй операнд — mem[operand]     |

### Набор команд (ISA)

| Опкод | Мнемоника | Описание                              | Тактов |
|-------|-----------|---------------------------------------|--------|
| 0x00  | NOP       | нет операции                          | 2      |
| 0x01  | LD        | ACC ← imm / mem[addr]                 | 3      |
| 0x02  | ST        | mem[addr] ← ACC                       | 3      |
| 0x03  | LDA       | ACC ← mem[ACC]                        | 3      |
| 0x04  | STA       | mem[ACC] ← NOS; drop both; refill TOS | 5      |
| 0x05  | PUSH      | mem[SP] ← ACC; SP += 4                | 3      |
| 0x06  | POP       | SP -= 4; ACC ← mem[SP]                | 3      |
| 0x07  | SWAP      | ACC ↔ mem[SP−4]                       | 4      |
| 0x08  | ADD       | ACC ← X + ACC                         | 3      |
| 0x09  | SUB       | ACC ← X − ACC                         | 3      |
| 0x0A  | MUL       | ACC ← X × ACC (signed)                | 3      |
| 0x0B  | DIV       | ACC ← X / ACC (trunc)                 | 3      |
| 0x0C  | MOD       | ACC ← X mod ACC                       | 3      |
| 0x0D  | AND       | ACC ← X & ACC                         | 3      |
| 0x0E  | OR        | ACC ← X \| ACC                        | 3      |
| 0x0F  | XOR       | ACC ← X ^ ACC                         | 3      |
| 0x10  | INV       | ACC ← ~ACC                            | 3      |
| 0x11  | JMP       | PC ← addr                             | 3      |
| 0x12  | JZ        | если Z: PC ← addr                     | 3      |
| 0x13  | JN        | если N: PC ← addr                     | 3      |
| 0x14  | JC        | если C: PC ← addr                     | 3      |
| 0x15  | CALL      | RS[RSP] ← PC; RSP += 4; PC ← addr    | 3      |
| 0x16  | CALLA     | indirect CALL через ACC (execute)     | 4      |
| 0x17  | RET       | RSP -= 4; PC ← RS[RSP]               | 3      |
| 0x18  | TOR       | RS[RSP] ← ACC; RSP += 4; pop DS      | 4      |
| 0x19  | FROMR     | push DS ← ACC; ACC ← RS[RSP−4]       | 4      |
| 0x1A  | RETI      | pop FLAGS, ACC, PC из RS; IE ← 1     | 5      |
| 0x1B  | EI        | IE ← 1                                | 3      |
| 0x1C  | DI        | IE ← 0                                | 3      |
| 0x1D  | IN        | ACC ← port[n]                         | 3      |
| 0x1E  | OUT       | port[n] ← ACC                         | 3      |
| 0x1F  | HLT       | останов симуляции                     | 2      |

Счёт тактов: `total = 1 (T0 fetch) + 1 (T1 decode) + EXEC_TICKS`.

### Модель выполнения

Каждая команда проходит этапы: `T0 → T1 → E1 [→ E2 [→ E3]]`.  
После последнего шага — проверка прерывания: если `IRQ=1` и `IE=1`, вместо следующего `T0` выполняется вход в обработчик (`I1 → I2 → I3 → I4`, 4 такта).

Вход в прерывание:
```
I1: RS[RSP] ← PC;    RSP += 4
I2: RS[RSP] ← ACC;   RSP += 4
I3: RS[RSP] ← FLAGS; RSP += 4
I4: IE ← 0; IRQ ← 0; PC ← mem[IVEC_IN]; in_trap ← True
```

`RETI` восстанавливает FLAGS, ACC, PC из стека возвратов в обратном порядке и устанавливает `IE ← 1`.

---

## Язык Forth (подмножество)

### Встроенные примитивы

```forth
+ - * / mod  and or xor invert   \ арифметика / логика
dup drop swap                    \ стек: дублировать, удалить, поменять
>r r>                            \ перенос между DS и RS
@ !                              \ чтение / запись памяти по адресу
emit                             \ вывод символа (OUT_PORT)
in@                              \ чтение из IN_PORT
set-isr                          \ установить адрес обработчика прерывания
ei di                            \ разрешить / запретить прерывания
reti                             \ возврат из обработчика
```

### Управляющие конструкции

```forth
if ... then
if ... else ... then
begin ... until           \ выход, когда TOS истинно (-1)
N M do ... loop           \ i = M, M+1, ..., N-1
i                         \ текущий индекс do/loop
```

### Определения

```forth
: имя  тело ;             \ слово (подпрограмма)
variable имя              \ переменная (1 ячейка данных)
N constant имя            \ константа (inline-подстановка)
c" текст"                 \ строка (адрес null-terminated)
." текст"                 \ печать строки
[char] X                  \ символьный литерал (ASCII код)
' слово                   \ execution token (адрес слова)
```

### Стандартная библиотека (prelude.fth)

```forth
over   ( a b -- a b a )
rot    ( a b c -- b c a )
negate ( n -- -n )
=      ( a b -- flag )
<      ( a b -- flag )
>      ( a b -- flag )
cell+  ( addr -- addr+4 )
cr     ( -- )                   \ вывод '\n'
type   ( addr -- )              \ вывод C-строки
strlen ( addr -- n )            \ длина C-строки
key    ( -- c )                 \ чтение символа (interrupt-driven)
.      ( n -- )                 \ вывод десятичного числа
.digits                         \ вспомогательное слово для '.'
```

---

## Транслятор

**Файл:** `forth_acc/translator.py`  
**Точка входа:** `translate(src, prelude="") → BinaryImage`

### Двухпроходная трансляция

**Pass 1 — токенизация + раскладка + кодогенерация:**
- Лексер разбирает исходник в список токенов (`NUM`, `WORD`, `STR`, `PRINT`, `CHAR`, `TICK`).
- Определения слов (`:` ... `;`) компилируются в подпрограммы.
- `variable` / `constant` регистрируются в словаре.
- Управляющие конструкции разворачиваются в переходы с символьными метками.
- Строки укладываются в секцию данных.
- Вершина каждого Forth-слова выбранного языка компилируется как `PUSH` + `LD #val` (TOS-in-ACC соглашение).

**Pass 2 — разрешение символов + кодирование:**
- Все символьные операнды заменяются на конкретные адреса.
- Генерируется `BinaryImage` (reset-вектор + код + данные).

### TOS-in-ACC соглашение

Верхушка стека всегда находится в регистре ACC.  
Остальные элементы — в памяти по адресам `[SP−4]`, `[SP−8]`, ...  
Это позволяет выполнять большинство операций над ACC без обращения к памяти.

---

## Симулятор

**Файл:** `forth_acc/machine.py`  
**Точка входа:** `simulate(image, input_schedule=None, max_ticks=10_000_000) → (output_bytes, trace_lines, state)`

Симулятор точно воспроизводит такты согласно спецификации:

```python
machine = Machine(image, input_schedule=[(tick, byte), ...])
machine.run(trace=True)   # возвращает лог тактов
```

Трассировочная строка:
```
tick=    17 T0 pc=0000001C acc=           65 sp=00002000 rsp=00003000 Z=0 N=0 C=0 IE=0 irq=0 trap=0
```

---

## Запуск

### Установка

```bash
pip install -e .
```

### Трансляция

```bash
translator <source.fth> <out.bin>
# → out.bin (бинарный образ)
# → out.lst (листинг с мнемониками)
```

### Симуляция

```bash
machine <image.bin>          # stdin → порт ввода, stdout ← порт вывода
echo "hello" | machine <image.bin>
```

---

## Примеры

### Hello, World!

```forth
c" Hello, World!" type cr
```

**Трансляция:** 165 слов кода, 16 слов данных.  
**Выполнение:** 854 такта.

```
$ translator hello.fth hello.bin
wrote hello.bin (165 code words, 16 data words)
wrote hello.lst

$ machine hello.bin
Hello, World!
```

Фрагмент листинга (`hello.lst`):
```
00000000 - 11000288 - JMP 0x000288
00000008 - 18000000 - TOR           ; over: >r dup r> swap
0000000C - 05000000 - PUSH
...
00001008 - 00000048 - <data 'H'>
0000100C - 00000065 - <data 'e'>
00001010 - 0000006C - <data 'l'>
```

### Ввод через прерывание (echo)

```forth
: on-input  in@ ch !  -1 ch-ready !  reti ;
' on-input set-isr
ei
key emit
```

Обработчик `on-input` срабатывает при поступлении байта через планировщик ввода:
1. `in@` — читает байт из IN_PORT в ACC.
2. Сохраняет в переменную `ch`, устанавливает флаг `ch-ready`.
3. `reti` — восстанавливает контекст и возвращает IE=1.

Основная программа вызывает `key` (спин-ожидание на `ch-ready`), затем выводит символ.

### Задача Euler #1

Сумма всех кратных 3 или 5 меньше 1000:

```forth
variable total

: mult3or5  ( n -- flag )
  dup 3 mod 0=
  swap 5 mod 0=
  or ;

: solve
  0 total !
  1000 1 do
    i mult3or5 if
      total @ i + total !
    then
  loop
  total @ . ;

solve
```

**Результат:** `233168`  
**Выполнение:** ≈ 113 000 тактов.

---

## Тестирование

```
tests/
├── test_translator.py   — токенизатор, encode/decode, ошибки трансляции
├── test_machine.py      — ALU, ветвления, CALL/RET, прерывания, счёт тактов
└── test_golden.py       — end-to-end: вывод, стек, арифметика, CF, Euler #1
```

```bash
pytest tests/ -v
# 120 passed in 0.34s
```

---

## Структура проекта

```
forth_acc/
├── isa.py          — ISA: опкоды, кодирование, BinaryImage, MachineState  (634 строк)
├── translator.py   — двухпроходный транслятор Forth → binary              (596 строк)
├── machine.py      — tick-accurate симулятор                               (408 строк)
└── cli.py          — консольные точки входа translator / machine           (77 строк)
prelude.fth         — стандартная библиотека Forth                          (85 строк)
tests/
├── test_translator.py
├── test_machine.py
└── test_golden.py
pyproject.toml
.github/workflows/ci.yml
```

**Итого исходного кода:** ~1800 строк Python + 85 строк Forth.

---

## CI

GitHub Actions (`.github/workflows/ci.yml`): при каждом push запускаются ruff, mypy, pytest.

```
ruff check forth_acc/   → All checks passed
mypy forth_acc/         → Success: no issues found in 5 source files
pytest tests/           → 120 passed in 0.34s
```
