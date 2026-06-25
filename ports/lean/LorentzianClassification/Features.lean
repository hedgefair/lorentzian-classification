import LorentzianClassification.Basic
import LorentzianClassification.TimeSeries
import LorentzianClassification.Indicators.RSI
import LorentzianClassification.Indicators.CCI
import LorentzianClassification.Indicators.ADX
import LorentzianClassification.Indicators.WaveTrend

/-!
# Feature engineering

The five normalized feature slots of the classifier. Mirrors Python
`calc_feature` (`core.py:869-886`): RSI/CCI read the close, WT reads hlc3, ADX
reads high/low/close with the price-scale quantization (its `paramB` is
ignored, as in Pine `series_from`).
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- Supported feature indicator kinds. -/
inductive FeatureType where
  | RSI
  | WT
  | CCI
  | ADX
  deriving Repr, BEq, DecidableEq, Inhabited

/-- One feature slot specification: kind plus the two Pine smoothing params. -/
structure FeatureConfig where
  featureType : FeatureType
  paramA : Nat
  paramB : Nat
  deriving Repr, Inhabited

/-- State wrapper for each supported feature indicator. -/
inductive FeatureIndicatorState where
  | rsi (state : RSIState)
  | wt (state : WaveTrendState)
  | cci (state : CCIState)
  | adx (state : ADXComputeState)
  deriving Repr, Inhabited

namespace FeatureIndicatorState

/-- Initial state for a feature slot. -/
def init (config : FeatureConfig) : FeatureIndicatorState :=
  match config.featureType with
  | .RSI => .rsi (RSIState.init config.paramA config.paramB)
  | .WT => .wt (WaveTrendState.init config.paramA config.paramB)
  | .CCI => .cci (CCIState.init config.paramA config.paramB)
  | .ADX => .adx (ADXComputeState.init config.paramA)

/-- One step of a feature slot on the current bar. -/
def step (cfg : FeatureConfig) (state : FeatureIndicatorState) (bar : Bar)
    (priceScale : Float) : FeatureIndicatorState × PSFloat :=
  match state with
  | .rsi st =>
    let (newState, value) := st.step (some bar.close)
    (.rsi newState, value)
  | .wt st =>
    let (newState, value) := st.step (some bar.hlc3)
    (.wt newState, value)
  | .cci st =>
    let (newState, value) := st.step (some bar.close)
    (.cci newState, value)
  | .adx st =>
    let (newState, value) := st.step bar.high bar.low bar.close cfg.paramA priceScale
    (.adx newState, value)

end FeatureIndicatorState

/-- Default Pine feature slots: RSI(14,1), WT(10,11), CCI(20,1), ADX(20,2),
RSI(9,1). -/
def defaultFeatureConfig : Nat → FeatureConfig
  | 0 => { featureType := .RSI, paramA := 14, paramB := 1 }
  | 1 => { featureType := .WT, paramA := 10, paramB := 11 }
  | 2 => { featureType := .CCI, paramA := 20, paramB := 1 }
  | 3 => { featureType := .ADX, paramA := 20, paramB := 2 }
  | _ => { featureType := .RSI, paramA := 9, paramB := 1 }

/-- Full feature engine state: five indicator states plus the historical
per-slot value arrays consumed by the ANN distance scan. -/
structure FeatureEngineState where
  f1State : FeatureIndicatorState
  f2State : FeatureIndicatorState
  f3State : FeatureIndicatorState
  f4State : FeatureIndicatorState
  f5State : FeatureIndicatorState
  f1Array : Array PSFloat
  f2Array : Array PSFloat
  f3Array : Array PSFloat
  f4Array : Array PSFloat
  f5Array : Array PSFloat
  deriving Repr, Inhabited

namespace FeatureEngineState

/-- Initial engine state from the five slot configs (defaults fill gaps). -/
def init (features : Array FeatureConfig) : FeatureEngineState :=
  { f1State := FeatureIndicatorState.init (features.getD 0 (defaultFeatureConfig 0))
  , f2State := FeatureIndicatorState.init (features.getD 1 (defaultFeatureConfig 1))
  , f3State := FeatureIndicatorState.init (features.getD 2 (defaultFeatureConfig 2))
  , f4State := FeatureIndicatorState.init (features.getD 3 (defaultFeatureConfig 3))
  , f5State := FeatureIndicatorState.init (features.getD 4 (defaultFeatureConfig 4))
  , f1Array := #[]
  , f2Array := #[]
  , f3Array := #[]
  , f4Array := #[]
  , f5Array := #[] }

/-- Step all five slots on the current bar; pushes each value onto its
historical array and returns the five current values in slot order. -/
def step (features : Array FeatureConfig) (engine : FeatureEngineState) (bar : Bar)
    (priceScale : Float) : FeatureEngineState × Array PSFloat :=
  let cfg (i : Nat) := features.getD i (defaultFeatureConfig i)
  let (f1State, f1Value) := FeatureIndicatorState.step (cfg 0) engine.f1State bar priceScale
  let (f2State, f2Value) := FeatureIndicatorState.step (cfg 1) engine.f2State bar priceScale
  let (f3State, f3Value) := FeatureIndicatorState.step (cfg 2) engine.f3State bar priceScale
  let (f4State, f4Value) := FeatureIndicatorState.step (cfg 3) engine.f4State bar priceScale
  let (f5State, f5Value) := FeatureIndicatorState.step (cfg 4) engine.f5State bar priceScale
  ({ f1State := f1State
   , f2State := f2State
   , f3State := f3State
   , f4State := f4State
   , f5State := f5State
   , f1Array := engine.f1Array.push f1Value
   , f2Array := engine.f2Array.push f2Value
   , f3Array := engine.f3Array.push f3Value
   , f4Array := engine.f4Array.push f4Value
   , f5Array := engine.f5Array.push f5Value },
   #[f1Value, f2Value, f3Value, f4Value, f5Value])

/-- The five historical arrays as a `Fin 5`-indexed function. -/
def arrays (engine : FeatureEngineState) : Fin 5 → Array PSFloat
  | ⟨0, _⟩ => engine.f1Array
  | ⟨1, _⟩ => engine.f2Array
  | ⟨2, _⟩ => engine.f3Array
  | ⟨3, _⟩ => engine.f4Array
  | ⟨4, _⟩ => engine.f5Array

end FeatureEngineState

end LorentzianClassification
