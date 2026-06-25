import LorentzianClassification.Pipeline

/-!
# Pipeline invariants (proved)

Replaces the premium spec's vacuous `stepBar_deterministic` (`rfl` — true of
any pure function) with invariants that carry information: output cardinality,
the signal hold rule, and the backtest-stream codomain.

Each theorem name has a matching executable property test in
`ports/lean/Tests.lean`.
-/
set_option autoImplicit false

namespace LorentzianClassification.Properties

open LorentzianClassification

/-- Each processed bar appends exactly one row. -/
theorem runBars_length (settings : Settings) (priceScale : Float)
    (maxBarsBackIndex : Nat) (state : PipelineState) (rows : Array ResultRow)
    (bars : List Bar) :
    (runBars settings priceScale maxBarsBackIndex state rows bars).size =
      rows.size + bars.length := by
  induction bars generalizing state rows with
  | nil => simp [runBars]
  | cons bar rest ih =>
    simp only [runBars, ih, Array.size_push, List.length_cons]
    omega

/-- One result row per input bar (the CSV writer's zip relies on this). -/
theorem processAllBars_length (settings : Settings) (priceScale : Float)
    (bars : Array Bar) :
    (processAllBars settings priceScale bars).size = bars.size := by
  unfold processAllBars
  simp [runBars_length]

/-- The signal holds its previous value on a zero prediction. -/
theorem nextSignal_holds_on_zero_prediction (filterAll : Bool) (prev : Direction) :
    nextSignal 0 filterAll prev = prev := by
  simp [nextSignal]

/-- The signal holds its previous value when any filter fails. -/
theorem nextSignal_holds_when_filtered (prediction : Int) (prev : Direction) :
    nextSignal prediction false prev = prev := by
  simp [nextSignal]

/-- For a non-long previous signal, the machine goes long exactly on a
positive, filter-passing prediction. -/
theorem nextSignal_long_iff (prediction : Int) (filterAll : Bool) (prev : Direction)
    (hprev : prev ≠ .long) :
    (nextSignal prediction filterAll prev = .long) ↔
      (prediction > 0 && filterAll) = true := by
  unfold nextSignal
  split
  · next h => simp [h]
  · next h =>
    split
    · next h2 => simp [h]
    · simp [h, hprev]

/-- For a non-short previous signal, the machine goes short exactly on a
negative, filter-passing prediction. -/
theorem nextSignal_short_iff (prediction : Int) (filterAll : Bool) (prev : Direction)
    (hprev : prev ≠ .short) :
    (nextSignal prediction filterAll prev = .short) ↔
      (prediction < 0 && filterAll) = true := by
  unfold nextSignal
  split
  · next h =>
    have hpos : prediction > 0 := of_decide_eq_true ((Bool.and_eq_true _ _).mp h).left
    constructor
    · intro hcontra
      exact absurd hcontra (by decide)
    · intro h2
      have hneg : prediction < 0 := of_decide_eq_true ((Bool.and_eq_true _ _).mp h2).left
      omega
  · next h =>
    split
    · next h2 => simp [h2]
    · next h2 => simp [hprev, h2]

/-- The bars-held counter resets on a signal change and increments otherwise. -/
theorem nextBarsHeld_spec (signalChanged : Bool) (barsHeld : Nat) :
    nextBarsHeld signalChanged barsHeld = if signalChanged then 0 else barsHeld + 1 := rfl

/-- The backtest stream only takes the five Pine switch values. -/
theorem backtestStream_mem (startLong endLong startShort endShort : Bool) :
    backtestStream startLong endLong startShort endShort ∈ ([1, 2, -1, -2, 0] : List Int) := by
  cases startLong <;> cases endLong <;> cases startShort <;> cases endShort <;> decide

/-- The confidence-gradient ladder only emits the ten Pine transparency
levels. -/
theorem gradientTransparency_mem (prediction : Float) :
    Display.gradientTransparency prediction ∈
      ([0, 10, 20, 30, 40, 50, 60, 70, 80, 90] : List Int) := by
  unfold Display.gradientTransparency
  simp only []
  split
  · decide
  · split
    · decide
    · split
      · decide
      · split
        · decide
        · split
          · decide
          · split
            · decide
            · split
              · decide
              · split
                · decide
                · split
                  · decide
                  · decide

end LorentzianClassification.Properties
