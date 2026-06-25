import LorentzianClassification.Basic
import LorentzianClassification.Numeric
import LorentzianClassification.Distance

/-!
# Approximate nearest neighbors over the Lorentzian distance

Mirror of Python `AnnState.run` (`core.py:962-989`) / Rust `ann.rs`. The
neighbor `distances`/`predictions` buffers PERSIST ACROSS BARS (Pine `var`
arrays) and FIFO-evict from the front; `lastDistance` resets to `-1.0` at the
start of each bar's scan.

Loop semantics (all parity-critical):
- a candidate is accepted iff `d >= lastDistance && i % 4 != 0` (chronological
  spacing skip of every 4th index);
- EVERY acceptance ratchets `lastDistance := d` (Pine line 405 — this update
  was missing from the premium spec's fill phase, the descendant of the
  historic "ratchet up" bug);
- on overflow (`size > neighborsCount`) `lastDistance` re-anchors DOWN to
  `distances[roundTiesEven(neighborsCount·3/4)]` (banker's rounding, matching
  Python `round`; Pine truncates instead — divergence documented for
  `neighborsCount ∉ {k : 3k mod 4 < 2}`, irrelevant at the default 8), then
  both buffers shift;
- iteration is ASCENDING over `[startIndex, sizeLoop]`, or DESCENDING from
  `startIndex` down to `sizeLoop` when `startIndex > sizeLoop` (the
  `includeFullHistory = false` regime on datasets longer than
  `2 × maxBarsBack`, mirroring Python's `range(start, stop-1, -1)`).
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- State threaded through the ANN candidate scan. `predictions` stores the
integer training labels of the accepted neighbors. -/
structure ANNLoopState where
  lastDistance : Float
  distances : Array Float
  predictions : Array Int
  deriving Repr, Inhabited

/-- The candidate index sequence of one bar's scan: ascending
`[startIndex, sizeLoop]`, or descending `[startIndex, …, sizeLoop]` when
`startIndex > sizeLoop`. -/
def annScanIndices (startIndex sizeLoop : Nat) : List Nat :=
  if startIndex ≤ sizeLoop then
    (List.range (sizeLoop - startIndex + 1)).map (· + startIndex)
  else
    (List.range (startIndex - sizeLoop + 1)).map (startIndex - ·)

/-- One candidate evaluation of the ANN scan. -/
def annLoopStep (st : ANNLoopState) (i neighborsCount featureCount : Nat)
    (currentFeatures : Fin 5 → PSFloat) (featureArrays : Fin 5 → Array PSFloat)
    (yTrainArray : Array Int) : ANNLoopState :=
  match lorentzianDistancePS i featureCount currentFeatures featureArrays with
  | some d =>
    if d >= st.lastDistance && (i % 4 != 0) then
      let newDistances := st.distances.push d
      let newPredictions := st.predictions.push (yTrainArray.getD i 0)
      if newPredictions.size > neighborsCount then
        let thresholdIdx :=
          (roundTiesEven (neighborsCount.toFloat * 3.0 / 4.0)).toUInt64.toNat
        { lastDistance := newDistances.getD thresholdIdx d
        , distances := newDistances.extract 1 newDistances.size
        , predictions := newPredictions.extract 1 newPredictions.size }
      else
        { lastDistance := d
        , distances := newDistances
        , predictions := newPredictions }
    else
      st
  | none => st

/-- One bar's full ANN scan, seeded with the rolling buffers carried over from
the previous bar. `lastDistance` starts at `-1.0`. -/
def annSearch (startIndex sizeLoop neighborsCount featureCount : Nat)
    (currentFeatures : Fin 5 → PSFloat) (featureArrays : Fin 5 → Array PSFloat)
    (yTrainArray : Array Int) (seedDistances : Array Float)
    (seedPredictions : Array Int) : ANNLoopState :=
  let initState : ANNLoopState :=
    { lastDistance := -1.0, distances := seedDistances, predictions := seedPredictions }
  (annScanIndices startIndex sizeLoop).foldl
    (fun st i =>
      annLoopStep st i neighborsCount featureCount currentFeatures featureArrays yTrainArray)
    initState

/-- The bar's prediction: the sum of the surviving neighbor labels
(`array.sum(predictions)`), bounded by `±neighborsCount`. -/
def computePrediction (loopResult : ANNLoopState) : Int :=
  loopResult.predictions.foldl (· + ·) 0

end LorentzianClassification
