import LorentzianClassification.Basic
import LorentzianClassification.Normalize
import LorentzianClassification.Indicators.Window
import LorentzianClassification.Indicators.EMA

/-!
# Commodity Channel Index (normalized feature form)

Mirror of Python `calc_cci`/`calc_normalized_cci` (`core.py:780-794`): SMA mean
over the trailing window, mean absolute deviation divided by `period`, guard
`meanDev == 0 → 0`, EMA post-smoothing, then stateful running normalization to
`[0,1]`.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- CCI state: trailing source window, EMA smoothing, running normalization. -/
structure CCIState where
  window : Array PSFloat
  length : Nat
  ema : EMAState
  normState : NormalizeState
  deriving Repr, Inhabited

namespace CCIState

/-- Initial state for `CCI(cciLength)` smoothed by `EMA(emaLength)`. -/
def init (cciLength emaLength : Nat) : CCIState :=
  { window := #[]
  , length := max cciLength 1
  , ema := EMAState.init emaLength
  , normState := NormalizeState.init }

/-- One CCI step. The mean deviation sums oldest-first over the same window as
the SMA mean and divides by `length`, exactly as the batch reference. -/
def step (st : CCIState) (src : PSFloat) : CCIState × PSFloat :=
  let window := Window.push st.window st.length src
  let cciVal :=
    match Window.mean window st.length, src with
    | some mean, some x =>
      let meanDev :=
        window.foldl (fun acc v => acc + Float.abs (v.getD 0.0 - mean)) 0.0 /
          st.length.toFloat
      if meanDev != 0.0 then
        some ((x - mean) / (0.015 * meanDev))
      else
        some 0.0
    | _, _ => PSFloat.na
  let ema := st.ema.step cciVal
  let (normState, result) := st.normState.step ema.value 0.0 1.0
  ({ window := window, length := st.length, ema := ema, normState := normState }, result)

end CCIState

end LorentzianClassification
