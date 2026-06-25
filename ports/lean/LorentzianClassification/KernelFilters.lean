import LorentzianClassification.Basic

/-!
# Kernel crossover / rate-of-change signals

Mirror of the per-bar kernel booleans in Python `core.py:1071-1092`. The rate
and cross flags are gated by bar count (`i ≥ 2`, `i ≥ 3`, `i ≥ 1`) exactly as
the Python/Rust ports gate them — the premium spec's `0.0` out-of-range default
diverged from this at bars 0..2.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- Rolling kernel estimate histories (`yhat1` rational quadratic, `yhat2`
gaussian), oldest first; one entry per processed bar. -/
structure KernelFilterState where
  yhat1History : Array Float
  yhat2History : Array Float
  deriving Repr, Inhabited

/-- Boolean signals derived from the kernel histories. -/
structure KernelSignals where
  isBullishRate : Bool
  isBearishRate : Bool
  isBullishChange : Bool
  isBearishChange : Bool
  isBullishSmooth : Bool
  isBearishSmooth : Bool
  isBullishCross : Bool
  isBearishCross : Bool
  deriving Repr, Inhabited

/-- Compute the kernel signals for the latest bar (`n = history size`,
current bar index `n − 1`). -/
def computeKernelSignals (yhat1Hist yhat2Hist : Array Float) : KernelSignals :=
  let n := yhat1Hist.size
  let get (arr : Array Float) (ago : Nat) : Float :=
    if ago + 1 > arr.size then 0.0 else arr.getD (arr.size - 1 - ago) 0.0
  let y1_0 := get yhat1Hist 0
  let y1_1 := get yhat1Hist 1
  let y1_2 := get yhat1Hist 2
  let y2_0 := get yhat2Hist 0
  let y2_1 := get yhat2Hist 1
  let isBullishRate := n ≥ 3 && y1_1 < y1_0
  let isBearishRate := n ≥ 3 && y1_1 > y1_0
  let wasBullishRate := n ≥ 4 && y1_2 < y1_1
  let wasBearishRate := n ≥ 4 && y1_2 > y1_1
  { isBullishRate := isBullishRate
  , isBearishRate := isBearishRate
  , isBullishChange := isBullishRate && wasBearishRate
  , isBearishChange := isBearishRate && wasBullishRate
  , isBullishSmooth := y2_0 >= y1_0
  , isBearishSmooth := y2_0 <= y1_0
  , isBullishCross := n ≥ 2 && y2_0 >= y1_0 && y2_1 < y1_1
  , isBearishCross := n ≥ 2 && y2_0 <= y1_0 && y2_1 > y1_1 }

end LorentzianClassification
