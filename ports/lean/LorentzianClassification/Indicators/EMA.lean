import LorentzianClassification.Basic
import LorentzianClassification.Indicators.Window

/-!
# Exponential moving average

Mirror of Python `ema` (`core.py:687`): `alpha = 2/(period+1)`; an `na` input
breaks the recursion (output `na` at that bar); whenever the previous output is
`na`, the next non-`na` input re-seeds with the SMA of the trailing full window
provided that window is `na`-free.

Deviation from the premium `LorentzianSpec.EMA` (sample-counting warmup that
ignored `na` inputs): behaviorally identical on every series that occurs in
this indicator (contiguous warmup prefixes), but the windowed form also matches
Python on interior-`na` series.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- EMA state: trailing raw-input window (for SMA re-seeding) plus the current
output value. -/
structure EMAState where
  window : Array PSFloat
  value : PSFloat
  length : Nat
  deriving Repr, Inhabited

namespace EMAState

/-- Initial state; `length` is clamped to ≥ 1. -/
def init (length : Nat) : EMAState :=
  { window := #[], value := PSFloat.na, length := max length 1 }

/-- One EMA step (returns the updated state; read the output from `.value`).
A stored IEEE NaN input counts as missing, like Python's `is_missing`. -/
def step (st : EMAState) (src : PSFloat) : EMAState :=
  let window := Window.push st.window st.length src
  match src with
  | none => { st with window := window, value := PSFloat.na }
  | some x =>
    if x.isNaN then
      { st with window := window, value := PSFloat.na }
    else
      match st.value with
      | some prev =>
        let alpha := 2.0 / (st.length.toFloat + 1.0)
        { st with window := window, value := some (alpha * x + (1.0 - alpha) * prev) }
      | none =>
        { st with window := window, value := Window.mean window st.length }

end EMAState

end LorentzianClassification
