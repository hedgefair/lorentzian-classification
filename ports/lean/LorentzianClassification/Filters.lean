import LorentzianClassification.Basic

/-!
# Volatility, regime, and ADX filters

Mirrors Python `core.py` (`calc_regime_filter` at 889-913 and the per-bar
filter gates at 1056-1064).

Documented Python-vs-Pine divergences preserved here because Python is the
parity authority: the volatility filter is permissive (`true`) while the ATRs
are still warming up (Pine's `na >` would be false), and the regime filter's
seeds (`value2[0] = high−low`, `klmf[0] = ohlc4`) plus its `i < 200`
pass-through EMA warmup differ from a literal Pine reading.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- Volatility filter: `ATR(1) > ATR(10)` when enabled and both are defined;
permissive while either ATR is warming up. -/
def volatilityFilter (recentAtr historicalAtr : PSFloat) (useFilter : Bool) : Bool :=
  if !useFilter then
    true
  else
    match recentAtr, historicalAtr with
    | some recent, some historical => recent > historical
    | _, _ => true

/-- ADX filter: raw (0..100) ADX strictly above the threshold when enabled. -/
def adxFilter (rawAdx : Float) (threshold : Nat) (useFilter : Bool) : Bool :=
  if !useFilter then true else rawAdx > threshold.toFloat

/-- Regime filter state: the KLMF recursion plus the smoothed absolute slope. -/
structure RegimeFilterState where
  value1 : Float
  value2 : Float
  klmf : Float
  emaAbs : Float
  deriving Repr, Inhabited

namespace RegimeFilterState

/-- Pre-first-bar state (the first step overwrites it with the seeds). -/
def init : RegimeFilterState :=
  { value1 := 0.0, value2 := 0.0, klmf := 0.0, emaAbs := 0.0 }

/-- One regime step, returning `(state, absSlope, emaAbsSlope)`.

Bar 0 seeds: `value1 = 0`, `value2 = high − low`, `klmf = ohlc4`,
`absSlope = emaAbs = 0`. For `i ≥ 1` the KLMF recursion runs with
`alpha = (−ω² + √(ω⁴ + 16ω²)) / 8` (powers via `Float.pow`, mirroring
Python `**`/Rust `powf` bit-for-bit), and the slope EMA (`alpha = 2/201`)
passes `absSlope` through while its previous value is `0` and `i < 200`. -/
def step (st : RegimeFilterState) (ohlc4 prevOhlc4 high low : Float) (barIndex : Nat) :
    RegimeFilterState × Float × Float :=
  if barIndex == 0 then
    ({ value1 := 0.0, value2 := high - low, klmf := ohlc4, emaAbs := 0.0 }, 0.0, 0.0)
  else
    let value1 := 0.2 * (ohlc4 - prevOhlc4) + 0.8 * st.value1
    let value2 := 0.1 * (high - low) + 0.8 * st.value2
    let omega := if value2 != 0.0 then Float.abs (value1 / value2) else 0.0
    let alpha :=
      (-(omega ^ (2.0 : Float)) +
        Float.sqrt (omega ^ (4.0 : Float) + 16.0 * omega ^ (2.0 : Float))) / 8.0
    let klmf := alpha * ohlc4 + (1.0 - alpha) * st.klmf
    let absSlope := Float.abs (klmf - st.klmf)
    let alphaEma := 2.0 / 201.0
    let emaAbs :=
      if st.emaAbs == 0.0 && barIndex < 200 then
        absSlope
      else
        alphaEma * absSlope + (1.0 - alphaEma) * st.emaAbs
    ({ value1 := value1, value2 := value2, klmf := klmf, emaAbs := emaAbs },
     absSlope, emaAbs)

end RegimeFilterState

/-- Regime filter gate: normalized slope decline above the threshold when
enabled and the slope EMA is nonzero; permissive otherwise. -/
def regimeFilter (absSlope emaAbs threshold : Float) (useFilter : Bool) : Bool :=
  if useFilter && emaAbs != 0.0 then
    (absSlope - emaAbs) / emaAbs >= threshold
  else
    true

end LorentzianClassification
