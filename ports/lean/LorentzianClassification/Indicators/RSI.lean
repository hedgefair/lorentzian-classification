import LorentzianClassification.Basic
import LorentzianClassification.Normalize
import LorentzianClassification.Indicators.RMA
import LorentzianClassification.Indicators.EMA

/-!
# Wilder's RSI (normalized feature form)

Mirror of Python `calc_rsi`/`calc_normalized_rsi` (`core.py:754-777`):
gain/loss are `na` on the first bar, RMA-smoothed (window-seeded), the RSI is
`100 - 100/(1+rs)` with `avgLoss == 0 → 100`, then EMA-post-smoothed and
rescaled from `[0,100]` to `[0,1]` (`na` preserved).
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- RSI state: dual RMA smoothing plus EMA post-smoothing. -/
structure RSIState where
  rmaUp : RMAState
  rmaDown : RMAState
  ema : EMAState
  prevSrc : PSFloat
  deriving Repr, Inhabited

namespace RSIState

/-- Initial state for `RSI(rsiLength)` smoothed by `EMA(emaLength)`. -/
def init (rsiLength emaLength : Nat) : RSIState :=
  { rmaUp := RMAState.init rsiLength
  , rmaDown := RMAState.init rsiLength
  , ema := EMAState.init emaLength
  , prevSrc := PSFloat.na }

/-- One RSI step. All inner states are stepped every bar (with `na` inputs
during warmup) so the streaming series equals the batch vectors bit-for-bit. -/
def step (st : RSIState) (src : PSFloat) : RSIState × PSFloat :=
  let (gain, loss) : PSFloat × PSFloat :=
    match src, st.prevSrc with
    | some x, some prev =>
      let change := x - prev
      (some (if change > 0.0 then change else 0.0),
       some (if change < 0.0 then -change else 0.0))
    | _, _ => (PSFloat.na, PSFloat.na)
  let rmaUp := st.rmaUp.step gain
  let rmaDown := st.rmaDown.step loss
  let rsiVal :=
    match rmaUp.value, rmaDown.value with
    | some avgGain, some avgLoss =>
      if avgLoss == 0.0 then
        some 100.0
      else
        let rs := avgGain / avgLoss
        some (100.0 - 100.0 / (1.0 + rs))
    | _, _ => PSFloat.na
  let ema := st.ema.step rsiVal
  let result := rescale ema.value 0.0 100.0 0.0 1.0
  ({ rmaUp := rmaUp, rmaDown := rmaDown, ema := ema, prevSrc := src }, result)

end RSIState

end LorentzianClassification
