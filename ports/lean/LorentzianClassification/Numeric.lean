/-!
# `LorentzianClassification.Numeric`

Bit-exact numeric kernel for the Lorentzian Classification ports: round-half-to-even,
quantised subtraction, fixed-point decimal formatting, and decimal parsing.

## Parity contract

This module is the Lean reference specification for the numeric primitives shared by
the Python port (`core.py`) and the Rust port (`indicators.rs`). The contract, which is
validated by differential testing against CPython:

* `roundTiesEven x` agrees with Python's `round(x)` (and Rust's
  `f64::round_ties_even`) for every finite `Float`. The sign of a zero result is
  unspecified (`-0.0` may come back as `-0.0` or `0.0`); only the numeric value is part
  of the contract.
* `quantSub a b scale` mirrors Python `quant_sub` / Rust `indicators::quant_sub`
  bit for bit.
* `formatFixed x d` is byte-identical to CPython `f"{x:.<d>f}"` for all finite doubles
  and any digit count `d`: correct rounding of the exact binary value to `d` decimal
  places with ties to even, and a negative sign for every negative input including
  `-0.0`. Non-finite inputs map to `"nan"`, `"inf"`, `"-inf"` like Python `format`.
* `formatFloatCell x` is the CSV writer's `format_result_float`: `""` for NaN,
  otherwise `formatFixed x 16`.
* `parseFloat?` parses the decimal cells found in TradingView CSV exports (and every
  string `formatFixed` emits) to the correctly rounded nearest `Float`, ties to even —
  the same value CPython's `float(s)` produces on the accepted grammar. Anything
  outside the grammar parses to `none`.

All decimal/binary conversions are performed exactly with arbitrary-precision `Int`
arithmetic; no code path in this file performs more than one floating-point rounding,
so results are correctly rounded rather than merely close.

## Theorem debt

Per the team's Lean-spec porting policy, modules should carry theorems for their
invariants. `Float` is opaque in core Lean (no axioms describe IEEE-754 runtime
behaviour), so the invariants above are stated informally here and discharged by the
differential test harness against CPython instead of by in-file proofs. The `Int`-level
helpers are total and deterministic by construction.
-/

set_option autoImplicit false

namespace LorentzianClassification

/-- `2^52` as a `Float`: the smallest magnitude at which every double is already an
integer, used as the shortcut threshold in `roundTiesEven`. -/
private def twoPow52 : Float := 4503599627370496.0

/-- Round `x` to the nearest integer-valued `Float` with ties going to the even
neighbour — the behaviour of Python's `round(x)` and Rust's `f64::round_ties_even`
for all finite doubles.

Finite inputs with `|x| ≥ 2^52` are returned unchanged (such doubles are already
integers). NaN and infinities are returned unchanged. The sign of a zero result is
not specified by the parity contract (e.g. `roundTiesEven (-0.3)` is `0.0`); only
the numeric value matters downstream.

Below `2^52` the fractional part `x - x.floor` is computed exactly (by Sterbenz's
lemma, except for `x ∈ (-1, 0)` where any rounding cannot change the comparison
outcome), so the half-way test and the evenness test on the floor are exact. -/
def roundTiesEven (x : Float) : Float :=
  if x.isNaN then x
  else if twoPow52 ≤ x.abs then x
  else
    let f := x.floor
    let frac := x - f
    if frac < 0.5 then f
    else if 0.5 < frac then f + 1.0
    else if (f / 2.0).floor == f / 2.0 then f  -- tie: floor is even, keep it
    else f + 1.0

/-- Quantised subtraction, the mirror of Python `core.quant_sub` / Rust
`indicators::quant_sub`: if `scale ≤ 0` the plain difference `a - b`, otherwise both
operands are snapped to the grid `1/scale` with `roundTiesEven` before subtracting,
i.e. `(roundTiesEven (a*scale) - roundTiesEven (b*scale)) / scale`. -/
def quantSub (a b scale : Float) : Float :=
  if scale ≤ 0.0 then a - b
  else (roundTiesEven (a * scale) - roundTiesEven (b * scale)) / scale

/-- Exact integer rounding of the rational `num / den` to the nearest integer with
ties to even. Precondition (callers guarantee it): `num ≥ 0` and `den > 0`. -/
private def divRoundTiesEven (num den : Int) : Int :=
  let q := num / den
  let r := num % den
  if den < 2 * r then q + 1
  else if 2 * r < den then q
  else q + q % 2  -- exact tie: round to even

/-- Left-pad `s` with `'0'` to at least `width` characters. -/
private def padLeftZeros (s : String) (width : Nat) : String :=
  String.ofList (List.replicate (width - s.length) '0') ++ s

/-- Exact equivalent of CPython `f"{x:.<digits>f}"` for all finite doubles and any
`digits` (the ports use 8 and 16).

* The double is decomposed exactly into sign and magnitude `M * 2^E` from its bits,
  and `round(|x| * 10^digits)` is computed with exact `Int` arithmetic, ties to even —
  the same correct rounding CPython/glibc perform.
* A negative sign is emitted for every negative input, including `-0.0` and negative
  values that round to zero: `formatFixed (-0.0) 16 = "-0.0000000000000000"`.
* Non-finite inputs follow Python `format`: `"nan"`, `"inf"`, `"-inf"`. -/
def formatFixed (x : Float) (digits : Nat) : String :=
  let bits := x.toBits
  let sgn := bits >>> 63
  let expBits := (bits >>> 52) &&& 0x7FF
  let fracBits := bits &&& 0xFFFFFFFFFFFFF
  if expBits == 0x7FF then
    if fracBits != 0 then "nan"
    else if sgn == 1 then "-inf"
    else "inf"
  else
    -- |x| = M * 2^E exactly (subnormals/zero have an implicit exponent of 1).
    let M : Int := (fracBits.toNat : Int) + (if expBits == 0 then 0 else (2 : Int) ^ (52 : Nat))
    let E : Int := (if expBits == 0 then 1 else (expBits.toNat : Int)) - 1075
    let p : Int := (10 : Int) ^ digits
    -- A = round(|x| * 10^digits), ties to even, computed exactly.
    let A : Int :=
      if 0 ≤ E then M * p * (2 : Int) ^ E.toNat
      else divRoundTiesEven (M * p) ((2 : Int) ^ (-E).toNat)
    let head := (if sgn == 1 then "-" else "") ++ toString (A / p)
    if digits == 0 then head
    else head ++ "." ++ padLeftZeros (toString (A % p)) digits

/-- The CSV writer's `format_result_float`: the empty string for NaN, otherwise the
16-fractional-digit fixed-point rendering `formatFixed x 16`. -/
def formatFloatCell (x : Float) : String :=
  if x.isNaN then "" else formatFixed x 16

/-- Fold a list of ASCII digits (most significant first) into a nonnegative `Int`. -/
private def digitsToInt (ds : List Char) : Int :=
  ds.foldl (fun acc c => acc * 10 + Int.ofNat (c.toNat - '0'.toNat)) 0

/-- Convert the exact decimal value `(-1)^neg * mAbs * 10^k` (with `mAbs ≥ 0`) to the
nearest `Float`, ties to even — the value CPython's `float()` returns for the same
decimal.

* `mAbs = 0` yields a signed zero.
* `|value| ≥ 10^309` overflows to a signed infinity (as CPython string parsing does);
  `|value| < 10^-324` (below half the smallest subnormal) underflows to a signed zero.
  These guards also bound the size of the exact-arithmetic intermediates.
* When `mAbs ≤ 2^53` and `|k| ≤ 15`, both `mAbs` and `10^|k|` are exactly representable
  doubles, so a single multiply or divide performs the one correct rounding.
* Otherwise the correctly rounded double is computed exactly: with `value = num/den`,
  the binary exponent `e` (`2^e ≤ num/den < 2^(e+1)`) is located via `Nat.log2` plus
  one comparison, the 53-bit significand is obtained by `divRoundTiesEven` at scale
  `2^(e-52)` (clamped to `2^-1074` in the subnormal range), and the bits are assembled
  directly. This is never off by an ulp. -/
private def decimalToFloat (neg : Bool) (mAbs : Int) (k : Int) : Float :=
  if mAbs == 0 then (if neg then -0.0 else 0.0)
  else
    let signBit : UInt64 := if neg then (1 : UInt64) <<< 63 else 0
    let nd : Int := ((toString mAbs).length : Int)  -- decimal digit count of mAbs
    -- 10^(k+nd-1) ≤ |value| < 10^(k+nd)
    if 310 ≤ k + nd then Float.ofBits (signBit ||| 0x7FF0000000000000)
    else if k + nd ≤ -324 then (if neg then -0.0 else 0.0)
    else if mAbs ≤ 9007199254740992 ∧ -15 ≤ k ∧ k ≤ 15 then
      let mf := Float.ofInt mAbs
      let res :=
        if 0 ≤ k then mf * Float.ofNat ((10 : Nat) ^ k.toNat)
        else mf / Float.ofNat ((10 : Nat) ^ (-k).toNat)
      if neg then -res else res
    else
      -- Exact path: |value| = num / den with num, den > 0.
      let num : Int := if 0 ≤ k then mAbs * (10 : Int) ^ k.toNat else mAbs
      let den : Int := if 0 ≤ k then 1 else (10 : Int) ^ (-k).toNat
      -- Binary exponent e with 2^e ≤ num/den < 2^(e+1); Nat.log2 brackets it to
      -- {e0 - 1, e0}, one exact comparison resolves which.
      let e0 : Int := (num.toNat.log2 : Int) - (den.toNat.log2 : Int)
      let rGePow2 : Int → Bool := fun e =>
        if 0 ≤ e then den * (2 : Int) ^ e.toNat ≤ num
        else den ≤ num * (2 : Int) ^ (-e).toNat
      let e : Int := if rGePow2 e0 then e0 else e0 - 1
      -- Below the normal range the rounding quantum is pinned at 2^-1074.
      let eEff : Int := if e < -1022 then -1022 else e
      let shift : Int := eEff - 52
      let msig : Int :=
        if 0 ≤ shift then divRoundTiesEven num (den * (2 : Int) ^ shift.toNat)
        else divRoundTiesEven (num * (2 : Int) ^ (-shift).toNat) den
      let c52 : Int := (2 : Int) ^ (52 : Nat)
      -- Rounding may carry into the next binade (msig = 2^53): renormalise.
      let (msig, eEff) := if msig == 2 * c52 then (c52, eEff + 1) else (msig, eEff)
      if 1023 < eEff then Float.ofBits (signBit ||| 0x7FF0000000000000)
      else if msig == 0 then (if neg then -0.0 else 0.0)
      else if msig < c52 then
        Float.ofBits (signBit ||| UInt64.ofNat msig.toNat)  -- subnormal
      else
        Float.ofBits (signBit
          ||| (UInt64.ofNat (eEff + 1023).toNat <<< 52)
          ||| UInt64.ofNat (msig - c52).toNat)

/-- Parse a decimal float from a TradingView CSV cell.

Accepted grammar (whitespace must be pre-trimmed by the caller):
`['+' | '-'] digits* ['.' digits*] [('e' | 'E') ['+' | '-'] digits+]`, with at least
one digit in the mantissa — so `"300"`, `"1.250"`, `".5"`, `"5."` and `"-1.5e-3"` are
all accepted. Everything else (empty string, lone `"."`, `"nan"`, `"inf"`, trailing
garbage, …) returns `none`.

The result is the correctly rounded nearest `Float` (ties to even) of the exact
decimal value — bit-identical to CPython `float(s)` — computed via `decimalToFloat`
from the integer mantissa and decimal exponent. In particular it round-trips every
string `formatFixed` emits and every OHLC cell in real TradingView exports. -/
def parseFloat? (s : String) : Option Float :=
  let (neg, cs) :=
    match s.toList with
    | '-' :: rest => (true, rest)
    | '+' :: rest => (false, rest)
    | cs => (false, cs)
  let (intDs, afterInt) := cs.span Char.isDigit
  let (fracDs, afterFrac) :=
    match afterInt with
    | '.' :: rest => rest.span Char.isDigit
    | _ => ([], afterInt)
  if intDs.isEmpty && fracDs.isEmpty then none
  else
    let mAbs := digitsToInt (intDs ++ fracDs)
    let kFrac : Int := -(fracDs.length : Int)
    match afterFrac with
    | [] => some (decimalToFloat neg mAbs kFrac)
    | c :: rest =>
      if c == 'e' || c == 'E' then
        let (eNeg, rest) :=
          match rest with
          | '-' :: r => (true, r)
          | '+' :: r => (false, r)
          | _ => (false, rest)
        let (expDs, trailing) := rest.span Char.isDigit
        if expDs.isEmpty || !trailing.isEmpty then none
        else some (decimalToFloat neg mAbs
          (kFrac + if eNeg then -(digitsToInt expDs) else digitsToInt expDs))
      else none

end LorentzianClassification
