import LorentzianClassification

/-!
# Executable property tests

Theorem-named property tests per the team's lean-spec-porting policy: every
theorem in `LorentzianClassification/Properties/` has a matching deterministic
check here (1:1 name mapping), and the deferred Float-level statements in
`Properties/Deferred.lean` are exercised empirically. Stateful logic (the ANN
ratchet/FIFO, running normalization, the full pipeline) is driven across many
iterations, not single calls.

Run with `lake test`.
-/
set_option autoImplicit false

open LorentzianClassification

/-- Deterministic 64-bit LCG (Knuth MMIX constants). -/
structure Rng where
  state : UInt64

namespace Rng

def next (r : Rng) : UInt64 × Rng :=
  let s := r.state * 6364136223846793005 + 1442695040888963407
  (s, { state := s })

/-- Uniform float in `[0, 1)`. -/
def nextFloat (r : Rng) : Float × Rng :=
  let (s, r) := r.next
  ((s >>> 11).toNat.toFloat / 9007199254740992.0, r)

/-- Uniform float in `[lo, hi)`. -/
def nextRange (r : Rng) (lo hi : Float) : Float × Rng :=
  let (x, r) := r.nextFloat
  (lo + x * (hi - lo), r)

/-- Uniform label in `{-1, 0, 1}`. -/
def nextLabel (r : Rng) : Int × Rng :=
  let (s, r) := r.next
  ((Int.ofNat (s % 3).toNat) - 1, r)

end Rng

/-- Mutable test harness state. -/
structure TestState where
  failures : Array String := #[]
  checks : Nat := 0

def TestState.check (st : TestState) (name : String) (ok : Bool) : TestState :=
  if ok then
    { st with checks := st.checks + 1 }
  else
    { failures := st.failures.push name, checks := st.checks + 1 }

/-- Synthetic OHLC bars from a random walk. -/
def syntheticBars (n : Nat) (seed : UInt64) : Array Bar := Id.run do
  let mut rng : Rng := { state := seed }
  let mut close := 100.0
  let mut bars : Array Bar := #[]
  for i in [0:n] do
    let (d, r1) := rng.nextRange (-1.0) 1.0
    let (spreadHi, r2) := r1.nextRange 0.0 0.5
    let (spreadLo, r3) := r2.nextRange 0.0 0.5
    rng := r3
    let open_ := close
    close := max 1.0 (close + d)
    let high := max open_ close + spreadHi
    let low := min open_ close - spreadLo
    bars := bars.push
      { open_ := open_, high := high, low := low, close := close
      , hlc3 := (high + low + close) / 3.0
      , ohlc4 := (open_ + high + low + close) / 4.0
      , barIndex := i }
  return bars

/-- Synthetic feature world for driving the ANN scan directly. -/
structure AnnWorld where
  currentFeatures : Fin 5 → PSFloat
  featureArrays : Fin 5 → Array PSFloat
  yTrain : Array Int

def mkAnnWorld (n : Nat) (seed : UInt64) : AnnWorld := Id.run do
  let mut rng : Rng := { state := seed }
  let mut arrays : Array (Array PSFloat) := #[#[], #[], #[], #[], #[]]
  let mut yTrain : Array Int := #[]
  for _ in [0:n] do
    for k in [0:5] do
      let (v, r) := rng.nextFloat
      rng := r
      arrays := arrays.modify k (·.push (some v))
    let (l, r) := rng.nextLabel
    rng := r
    yTrain := yTrain.push l
  let mut current : Array PSFloat := #[]
  for _ in [0:5] do
    let (v, r) := rng.nextFloat
    rng := r
    current := current.push (some v)
  let frozenArrays := arrays
  let frozenCurrent := current
  return { currentFeatures := fun i => frozenCurrent.getD i.1 none
         , featureArrays := fun i => frozenArrays.getD i.1 #[]
         , yTrain := yTrain }

/-- Drive the ANN scan across `bars` simulated bars (the rolling buffers
persist, `lastDistance` resets to `-1` per bar, the query features change per
bar — exactly the pipeline's usage), asserting the per-step invariants. -/
def annInvariantRun (st0 : TestState) (k n bars : Nat) (seed : UInt64) :
    TestState := Id.run do
  let world := mkAnnWorld n seed
  let mut rng : Rng := { state := seed ^^^ 0xA11CE }
  let mut st := st0
  let mut distances : Array Float := #[]
  let mut predictions : Array Int := #[]
  let mut sizeOk := true
  let mut lockstepOk := true
  let mut ratchetEqOk := true
  let mut monotoneOk := true
  let mut overflowOk := true
  let mut overflowSeen := false
  let thresholdIdx := (roundTiesEven (k.toFloat * 3.0 / 4.0)).toUInt64.toNat
  for _ in [0:bars] do
    -- fresh query features per bar
    let mut currentArr : Array PSFloat := #[]
    for _ in [0:5] do
      let (v, r) := rng.nextFloat
      rng := r
      currentArr := currentArr.push (some v)
    let frozen := currentArr
    let current : Fin 5 → PSFloat := fun i => frozen.getD i.1 none
    let mut ann : ANNLoopState :=
      { lastDistance := -1.0, distances := distances, predictions := predictions }
    for i in annScanIndices 0 (n - 1) do
      let before := ann
      ann := annLoopStep before i k 5 current world.featureArrays world.yTrain
      sizeOk := sizeOk && (ann.predictions.size ≤ k || before.predictions.size > k)
      lockstepOk := lockstepOk && (ann.distances.size == ann.predictions.size)
      if ann.predictions.size == before.predictions.size + 1 then
        -- fill-phase acceptance: ratchet must equal the accepted distance
        match lorentzianDistancePS i 5 current world.featureArrays with
        | some d =>
          ratchetEqOk := ratchetEqOk && (ann.lastDistance == d)
          monotoneOk := monotoneOk && (before.lastDistance <= ann.lastDistance)
        | none => ratchetEqOk := false
      else
        -- overflow acceptance: re-anchor + oldest-eviction semantics
        match lorentzianDistancePS i 5 current world.featureArrays with
        | some d =>
          if (d >= before.lastDistance && (i % 4 != 0)) &&
              before.predictions.size + 1 > k then
            overflowSeen := true
            let pushedD := before.distances.push d
            let pushedP := before.predictions.push (world.yTrain.getD i 0)
            overflowOk := overflowOk &&
              (ann.lastDistance == pushedD.getD thresholdIdx d) &&
              (ann.predictions == pushedP.extract 1 pushedP.size) &&
              (ann.distances == pushedD.extract 1 pushedD.size)
        | none => pure ()
    distances := ann.distances
    predictions := ann.predictions
  st := st.check s!"annLoopStep_preserves_size_bound (k={k})" sizeOk
  st := st.check s!"annLoopStep_sizes_eq (k={k})" lockstepOk
  st := st.check s!"annLoopStep_accept_updates_ratchet (k={k})" ratchetEqOk
  st := st.check s!"annLoopStep_ratchet_monotone_in_fill_phase (k={k})" monotoneOk
  st := st.check s!"annLoopStep_overflow_reanchors_and_evicts (k={k})" (overflowOk && overflowSeen)
  return st

def main : IO UInt32 := do
  let mut st : TestState := {}

  -- ANN invariants across many iterations and neighbor counts.
  st := annInvariantRun st 8 400 8 0xC0FFEE
  st := annInvariantRun st 1 200 3 0xBEEF
  st := annInvariantRun st 3 200 4 0xF00D
  st := annInvariantRun st 100 600 60 0xABCDEF

  -- annScanIndices: lengths, endpoints, membership (incl. the descending
  -- branch that the COINBASE/H1 limited-history fixtures exercise).
  let asc := annScanIndices 0 1999
  let desc := annScanIndices 2154 1999
  st := st.check "annScanIndices_length (ascending)" (asc.length == 2000)
  st := st.check "annScanIndices_length (descending)" (desc.length == 156)
  st := st.check "mem_annScanIndices (ascending endpoints)"
    (asc.head? == some 0 && asc.getLast? == some 1999)
  st := st.check "mem_annScanIndices (descending endpoints)"
    (desc.head? == some 2154 && desc.getLast? == some 1999)
  st := st.check "annScanIndices_length (singleton)"
    ((annScanIndices 5 5).length == 1 && (annScanIndices 5 5).head? == some 5)

  -- computePrediction bound on random label buffers.
  let mut rng : Rng := { state := 0x5EED }
  let mut predBoundOk := true
  for _ in [0:200] do
    let (len, r1) := rng.next
    rng := r1
    let n := (len % 32).toNat
    let mut labels : Array Int := #[]
    for _ in [0:n] do
      let (l, r) := rng.nextLabel
      rng := r
      labels := labels.push l
    let ann : ANNLoopState := { lastDistance := 0.0, distances := #[], predictions := labels }
    predBoundOk := predBoundOk && ((computePrediction ann).natAbs ≤ labels.size)
  st := st.check "computePrediction_natAbs_le" predBoundOk

  -- Direction lemmas (exhaustive).
  st := st.check "direction_toInt_natAbs_le_one"
    ([Direction.long, .short, .neutral].all (fun d => d.toInt.natAbs ≤ 1))
  st := st.check "direction_ofInt_toInt"
    ([Direction.long, .short, .neutral].all (fun d => Direction.ofInt d.toInt == d))

  -- nextSignal (exhaustive over a small domain).
  let dirs := [Direction.long, .short, .neutral]
  let mut nsZeroOk := true
  let mut nsFilteredOk := true
  let mut nsLongOk := true
  let mut nsShortOk := true
  for prev in dirs do
    for f in [true, false] do
      nsZeroOk := nsZeroOk && (nextSignal 0 f prev == prev)
    for p in ([-2, -1, 0, 1, 2] : List Int) do
      nsFilteredOk := nsFilteredOk && (nextSignal p false prev == prev)
      if prev != .long then
        nsLongOk := nsLongOk && ((nextSignal p true prev == .long) == (p > 0))
      if prev != .short then
        nsShortOk := nsShortOk && ((nextSignal p true prev == .short) == (p < 0))
  st := st.check "nextSignal_holds_on_zero_prediction" nsZeroOk
  st := st.check "nextSignal_holds_when_filtered" nsFilteredOk
  st := st.check "nextSignal_long_iff" nsLongOk
  st := st.check "nextSignal_short_iff" nsShortOk

  -- nextBarsHeld.
  st := st.check "nextBarsHeld_spec"
    (nextBarsHeld true 7 == 0 && nextBarsHeld false 7 == 8 && nextBarsHeld false 0 == 1)

  -- backtestStream codomain (exhaustive).
  let mut bsOk := true
  for a in [true, false] do
    for b in [true, false] do
      for c in [true, false] do
        for d in [true, false] do
          bsOk := bsOk && ([1, 2, -1, -2, 0].contains (backtestStream a b c d))
  st := st.check "backtestStream_mem" bsOk

  -- gradientTransparency codomain (sweep).
  let mut gtOk := true
  let mut p := -12.0
  while p <= 12.0 do
    gtOk := gtOk &&
      ([0, 10, 20, 30, 40, 50, 60, 70, 80, 90].contains (Display.gradientTransparency p))
    p := p + 0.25
  st := st.check "gradientTransparency_mem" gtOk

  -- processAllBars cardinality across dataset sizes (2300 exercises the
  -- post-maxBarsBack eligible region with the default settings).
  let mut lenOk := true
  for n in [0, 1, 7, 250, 2300] do
    let bars := syntheticBars n 0xDA7A
    let rows := processAllBars {} 100.0 bars
    lenOk := lenOk && (rows.size == bars.size)
  st := st.check "processAllBars_length" lenOk
  st := st.check "runBars_length"
    ((runBars {} 100.0 7 (PipelineState.init {}) #[] (syntheticBars 25 0xCAFE).toList).size == 25)

  -- Pipeline determinism across runs (executable replacement for the premium
  -- spec's vacuous `stepBar_deterministic`).
  let bars := syntheticBars 2300 0xD00D
  let rows1 := processAllBars {} 100.0 bars
  let rows2 := processAllBars {} 100.0 bars
  let mut detOk := rows1.size == rows2.size
  for i in [0:rows1.size] do
    if i % 97 == 0 || i + 1 == rows1.size then
      detOk := detOk && (reprStr (rows1.getD i default) == reprStr (rows2.getD i default))
  st := st.check "processAllBars_deterministic" detOk

  -- Deferred theorem statements, exercised empirically (Float level).
  let mut nonnegOk := true
  rng := { state := 0xD15C }
  for _ in [0:500] do
    let w := mkAnnWorld 16 rng.state
    let (_, r) := rng.next
    rng := r
    for i in [0:16] do
      match lorentzianDistancePS i 5 w.currentFeatures w.featureArrays with
      | some d => nonnegOk := nonnegOk && (d >= 0.0)
      | none => nonnegOk := false
  st := st.check "lorentzianDistance_nonneg" nonnegOk

  let mut normOk := true
  rng := { state := 0x0B0E }
  let mut norm := NormalizeState.init
  let mut seen : Nat := 0
  for _ in [0:5000] do
    let (x, r) := rng.nextRange (-50.0) 50.0
    rng := r
    let (norm', v) := norm.step (some x) 0.0 1.0
    norm := norm'
    seen := seen + 1
    if seen ≥ 2 then
      match v with
      | some out => normOk := normOk && (out >= -0.000000000001 && out <= 1.000000000001)
      | none => normOk := false
  st := st.check "ratNormalizeStep_bounded (Float, 1e-12 slack)" normOk

  let mut kernOk := true
  rng := { state := 0x6E55 }
  for trial in [0:200] do
    let n := 1 + (trial % 40)
    let mut src : Array Float := #[]
    let mut lo := 1000000.0
    let mut hi := -1000000.0
    for _ in [0:n] do
      let (x, r) := rng.nextRange 50.0 150.0
      rng := r
      src := src.push x
      lo := min lo x
      hi := max hi x
    let est := rationalQuadratic src 8 8.0 25
    kernOk := kernOk && (est >= lo - 0.000000001 && est <= hi + 0.000000001)
  st := st.check "ratWeightedMean_within_bounds (Float kernels, 1e-9 slack)" kernOk

  -- Numeric formatting goldens (full differential validation lives in the
  -- module's development harness; these pin the contract).
  st := st.check "formatFixed_goldens"
    (formatFixed 0.1 16 == "0.1000000000000000" &&
     formatFixed 0.30000000000000004 16 == "0.3000000000000000" &&
     formatFixed 1.2345 8 == "1.23450000" &&
     formatFixed (-0.0) 16 == "-0.0000000000000000")
  st := st.check "roundTiesEven_goldens"
    (roundTiesEven 0.5 == 0.0 && roundTiesEven 1.5 == 2.0 && roundTiesEven 2.5 == 2.0 &&
     roundTiesEven (-2.5) == -2.0 && roundTiesEven 0.75 == 1.0)
  st := st.check "quantSub_uses_bankers_rounding"
    (quantSub 0.5 0.0 1.0 == 0.0 && quantSub 1.5 0.0 1.0 == 2.0 &&
     quantSub 2.5 0.0 1.0 == 2.0 && quantSub 3.5 0.0 1.0 == 4.0 &&
     quantSub 1.234565 0.0 100000.0 == 1.23456)
  st := st.check "quantSub_disabled_when_scale_non_positive"
    (quantSub 1.5 0.25 0.0 == 1.25 && quantSub 1.5 0.25 (-1.0) == 1.25)

  -- CSV round-trip smoke (full differential validation in the module).
  let sample : Array (Array String) :=
    #[#["time", "a,b", "c\"d"], #["1", "", "📈 Trade Stats"]]
  st := st.check "csv_roundtrip" (parseCsv (renderCsv sample) == sample)

  IO.println s!"{st.checks} checks, {st.failures.size} failures"
  if st.failures.isEmpty then
    IO.println "ALL PROPERTY TESTS PASSED"
    return 0
  else
    for f in st.failures do
      IO.println s!"FAIL: {f}"
    return 1
