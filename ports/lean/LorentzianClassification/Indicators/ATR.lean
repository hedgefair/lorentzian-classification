import LorentzianClassification.Basic
import LorentzianClassification.Indicators.RMA

/-!
# Average True Range

Mirror of Python `calc_atr` (`core.py:823-830`): true range with the previous
close defaulted to `0.0` on the first bar, smoothed by `RMA(period)`.

Documented deviation from Pine: the `ta.atr` builtin uses `high - low` when
`close[1]` is `na`, so the bar-0 true range here (≈ `high`) inflates the early
ATR seeds relative to Pine — matching the Python parity authority and the
Rust port (see README "Documented deviations").
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- ATR state: previous close plus RMA smoothing. -/
structure ATRState where
  rma : RMAState
  prevClose : PSFloat
  deriving Repr, Inhabited

namespace ATRState

/-- Initial state for `ATR(length)`. -/
def init (length : Nat) : ATRState :=
  { rma := RMAState.init length, prevClose := PSFloat.na }

/-- One ATR step, returning the smoothed value (`na` during RMA warmup). -/
def step (st : ATRState) (high low close : Float) : ATRState × PSFloat :=
  let prevC := PSFloat.nz st.prevClose
  let tr := max (max (high - low) (Float.abs (high - prevC))) (Float.abs (low - prevC))
  let rma := st.rma.step (some tr)
  ({ rma := rma, prevClose := some close }, rma.value)

end ATRState

end LorentzianClassification
