import LorentzianClassification.Basic
import LorentzianClassification.Normalize
import LorentzianClassification.Indicators.EMA
import LorentzianClassification.Indicators.SMA

/-!
# WaveTrend Classic (normalized feature form)

Mirror of Python `calc_wavetrend` (`core.py:796-815`): `ema1 = EMA(src, n1)`,
`ema2 = EMA(|src - ema1|, n1)`, `ci = (src - ema1)/(0.015·ema2)` with
`ema2 == 0 → 0`, `wt1 = EMA(ci, n2)`, `wt2 = SMA(wt1, 4)`, and the running
normalization of `wt1 - wt2` to `[0,1]`.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- WaveTrend state. -/
structure WaveTrendState where
  ema1 : EMAState
  ema2 : EMAState
  ciEma : EMAState
  sma : SMAState
  normState : NormalizeState
  deriving Repr, Inhabited

namespace WaveTrendState

/-- Initial state for `WT(n1, n2)`. -/
def init (n1 n2 : Nat) : WaveTrendState :=
  { ema1 := EMAState.init n1
  , ema2 := EMAState.init n1
  , ciEma := EMAState.init n2
  , sma := SMAState.init 4
  , normState := NormalizeState.init }

/-- One WaveTrend step. -/
def step (st : WaveTrendState) (src : PSFloat) : WaveTrendState × PSFloat :=
  let ema1 := st.ema1.step src
  let absDeviation :=
    match src, ema1.value with
    | some x, some e => some (Float.abs (x - e))
    | _, _ => PSFloat.na
  let ema2 := st.ema2.step absDeviation
  let ci :=
    match src, ema1.value, ema2.value with
    | some x, some e1, some e2 =>
      if e2 != 0.0 then some ((x - e1) / (0.015 * e2)) else some 0.0
    | _, _, _ => PSFloat.na
  let ciEma := st.ciEma.step ci
  let (sma, wt2) := st.sma.step ciEma.value
  let diff := PSFloat.lift2 (· - ·) ciEma.value wt2
  let (normState, result) := st.normState.step diff 0.0 1.0
  ({ ema1 := ema1
   , ema2 := ema2
   , ciEma := ciEma
   , sma := sma
   , normState := normState }, result)

end WaveTrendState

end LorentzianClassification
