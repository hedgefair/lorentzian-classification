import LorentzianClassification.Basic
import LorentzianClassification.Numeric
import LorentzianClassification.Normalize
import LorentzianClassification.Indicators.RMA

/-!
# Average Directional Index

Mirror of Python `calc_adx` (`core.py:832-861`): true range and directional
moves computed with price-quantized subtraction (`quantSub`, banker's rounding
at the input data's decimal scale — `priceScale = 0` disables quantization),
Wilder running smoothing (`x' = x - x/length + value`, first value seeds
directly), `di`/`dx` zero-denominator guards, RMA of `dx`, rescale to `[0,1]`
(`na` preserved through the RMA warmup).

The previous high/low/close are `nz`-defaulted to `0.0` on the first bar,
deliberately inflating the first true range exactly as Pine/Python do.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- ADX state: Wilder accumulators for TR and directional movement plus the
final RMA smoothing of `dx`. -/
structure ADXComputeState where
  trSmooth : Float
  smoothDmPlus : Float
  smoothDmMinus : Float
  adxRma : RMAState
  prevClose : PSFloat
  prevHigh : PSFloat
  prevLow : PSFloat
  deriving Repr, Inhabited

namespace ADXComputeState

/-- Initial state for `ADX(length)`. -/
def init (length : Nat) : ADXComputeState :=
  { trSmooth := 0.0
  , smoothDmPlus := 0.0
  , smoothDmMinus := 0.0
  , adxRma := RMAState.init length
  , prevClose := PSFloat.na
  , prevHigh := PSFloat.na
  , prevLow := PSFloat.na }

/-- One ADX step, returning the `[0,1]`-rescaled value (`na` during warmup). -/
def step (st : ADXComputeState) (highSrc lowSrc closeSrc : Float) (length : Nat)
    (priceScale : Float) : ADXComputeState × PSFloat :=
  let prevC := PSFloat.nz st.prevClose
  let prevH := PSFloat.nz st.prevHigh
  let prevL := PSFloat.nz st.prevLow
  let tr :=
    max (max (quantSub highSrc lowSrc priceScale)
             (Float.abs (quantSub highSrc prevC priceScale)))
        (Float.abs (quantSub lowSrc prevC priceScale))
  let upMove := quantSub highSrc prevH priceScale
  let downMove := quantSub prevL lowSrc priceScale
  let dmPlus := if upMove > downMove && upMove > 0.0 then upMove else 0.0
  let dmMinus := if downMove > upMove && downMove > 0.0 then downMove else 0.0
  let lenF := (max length 1).toFloat
  let newTrSmooth := st.trSmooth - st.trSmooth / lenF + tr
  let newSdmPlus := st.smoothDmPlus - st.smoothDmPlus / lenF + dmPlus
  let newSdmMinus := st.smoothDmMinus - st.smoothDmMinus / lenF + dmMinus
  let diPlus := if newTrSmooth != 0.0 then newSdmPlus / newTrSmooth * 100.0 else 0.0
  let diMinus := if newTrSmooth != 0.0 then newSdmMinus / newTrSmooth * 100.0 else 0.0
  let dx :=
    if diPlus + diMinus != 0.0 then
      Float.abs (diPlus - diMinus) / (diPlus + diMinus) * 100.0
    else
      0.0
  let adxRma := st.adxRma.step (some dx)
  let adxResult := rescale adxRma.value 0.0 100.0 0.0 1.0
  ({ trSmooth := newTrSmooth
   , smoothDmPlus := newSdmPlus
   , smoothDmMinus := newSdmMinus
   , adxRma := adxRma
   , prevClose := some closeSrc
   , prevHigh := some highSrc
   , prevLow := some lowSrc }, adxResult)

end ADXComputeState

end LorentzianClassification
