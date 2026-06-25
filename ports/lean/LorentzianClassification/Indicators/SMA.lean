import LorentzianClassification.Basic
import LorentzianClassification.Indicators.Window

/-!
# Simple moving average

Mirror of Python `sma` (`core.py:673`): `na` until the window holds `period`
inputs; any `na` inside the window poisons the output; mean recomputed over the
trailing window each bar (left-to-right sum).

Deviation from the premium `LorentzianSpec.SMA` (which silently skipped `na`
inputs): the window here stores raw inputs including `na`, matching Python on
interior-`na` series; identical on the contiguous-prefix series that occur.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- SMA state: the trailing raw-input window. -/
structure SMAState where
  window : Array PSFloat
  length : Nat
  deriving Repr, Inhabited

namespace SMAState

/-- Initial state; `length` is clamped to ≥ 1. -/
def init (length : Nat) : SMAState :=
  { window := #[], length := max length 1 }

/-- One SMA step, returning the updated state and the output value. -/
def step (st : SMAState) (src : PSFloat) : SMAState × PSFloat :=
  let window := Window.push st.window st.length src
  ({ st with window := window }, Window.mean window st.length)

end SMAState

end LorentzianClassification
