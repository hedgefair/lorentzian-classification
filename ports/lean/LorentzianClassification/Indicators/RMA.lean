import LorentzianClassification.Basic
import LorentzianClassification.Indicators.Window

/-!
# Wilder's running moving average (RMA / SMMA)

Mirror of Python `rma` (`core.py:705`): identical structure to `ema` with
`alpha = 1/period`, SMA-seeded over the trailing full window, recursion broken
by `na` inputs.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- RMA state: trailing raw-input window plus the current output value. -/
structure RMAState where
  window : Array PSFloat
  value : PSFloat
  length : Nat
  deriving Repr, Inhabited

namespace RMAState

/-- Initial state; `length` is clamped to ≥ 1. -/
def init (length : Nat) : RMAState :=
  { window := #[], value := PSFloat.na, length := max length 1 }

/-- One RMA step (returns the updated state; read the output from `.value`).
A stored IEEE NaN input counts as missing, like Python's `is_missing`. -/
def step (st : RMAState) (src : PSFloat) : RMAState :=
  let window := Window.push st.window st.length src
  match src with
  | none => { st with window := window, value := PSFloat.na }
  | some x =>
    if x.isNaN then
      { st with window := window, value := PSFloat.na }
    else
      match st.value with
      | some prev =>
        let alpha := 1.0 / st.length.toFloat
        { st with window := window, value := some (alpha * x + (1.0 - alpha) * prev) }
      | none =>
        { st with window := window, value := Window.mean window st.length }

end RMAState

end LorentzianClassification
