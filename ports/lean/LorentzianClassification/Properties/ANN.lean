import LorentzianClassification.ANN

/-!
# ANN invariants (proved)

Structural invariants of the neighbor scan. `annLoopStep_accept_updates_ratchet`
and `annLoopStep_ratchet_monotone_in_fill_phase` are the formalization of the
historic "missing ratchet up" bug: the premium spec's fill phase (which left
`lastDistance` unchanged on acceptance) cannot satisfy them, while the
Pine/Python/Rust semantics do.

Each theorem name has a matching executable property test in
`ports/lean/Tests.lean` (skill rule: 1:1 theorem-to-test mapping).
-/
set_option autoImplicit false

namespace LorentzianClassification.Properties

open LorentzianClassification

/-- The neighbor buffer never exceeds `neighborsCount`: one scan step preserves
the size bound (FIFO eviction fires exactly on overflow). -/
theorem annLoopStep_preserves_size_bound
    (st : ANNLoopState) (i neighborsCount featureCount : Nat)
    (currentFeatures : Fin 5 → PSFloat) (featureArrays : Fin 5 → Array PSFloat)
    (yTrainArray : Array Int)
    (h : st.predictions.size ≤ neighborsCount) :
    (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
      yTrainArray).predictions.size ≤ neighborsCount := by
  unfold annLoopStep
  cases lorentzianDistancePS i featureCount currentFeatures featureArrays with
  | none => exact h
  | some d =>
    simp only []
    split
    · split
      · simp only [Array.size_extract, Array.size_push]
        omega
      · next hover =>
        simp only [Array.size_push] at hover ⊢
        omega
    · exact h

/-- The distance and prediction buffers stay in lockstep. -/
theorem annLoopStep_sizes_eq
    (st : ANNLoopState) (i neighborsCount featureCount : Nat)
    (currentFeatures : Fin 5 → PSFloat) (featureArrays : Fin 5 → Array PSFloat)
    (yTrainArray : Array Int)
    (h : st.distances.size = st.predictions.size) :
    (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
      yTrainArray).distances.size =
    (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
      yTrainArray).predictions.size := by
  unfold annLoopStep
  cases lorentzianDistancePS i featureCount currentFeatures featureArrays with
  | none => exact h
  | some d =>
    simp only []
    split
    · split
      · simp only [Array.size_extract, Array.size_push]
        omega
      · simp only [Array.size_push]
        omega
    · exact h

/-- An accepting, non-overflowing step sets the ratchet to EXACTLY the accepted
distance, which dominates the previous ratchet. This is the invariant whose
absence was the historic "missing ratchet up" bug: a fill phase that leaves
`lastDistance` unchanged on acceptance cannot satisfy the equality. -/
theorem annLoopStep_accept_updates_ratchet
    (st : ANNLoopState) (i neighborsCount featureCount : Nat)
    (currentFeatures : Fin 5 → PSFloat) (featureArrays : Fin 5 → Array PSFloat)
    (yTrainArray : Array Int)
    (hgrow : (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
      yTrainArray).predictions.size = st.predictions.size + 1) :
    ∃ d, lorentzianDistancePS i featureCount currentFeatures featureArrays = some d ∧
      (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
        yTrainArray).lastDistance = d ∧
      st.lastDistance ≤ d := by
  unfold annLoopStep at hgrow ⊢
  cases hd : lorentzianDistancePS i featureCount currentFeatures featureArrays with
  | none =>
    rw [hd] at hgrow
    simp at hgrow
  | some d =>
    rw [hd] at hgrow
    simp only [] at hgrow ⊢
    split at hgrow
    · next hacc =>
      split at hgrow
      · next hover =>
        exfalso
        simp only [Array.size_extract, Array.size_push] at hgrow
        omega
      · next hover =>
        refine ⟨d, rfl, ?_, ?_⟩
        · simp only [hacc, if_pos]
          split
          · next hover2 => exact absurd hover2 hover
          · rfl
        · have hand := (Bool.and_eq_true _ _).mp hacc
          exact of_decide_eq_true hand.left
    · next hacc =>
      exfalso
      omega

/-- During the fill phase (no overflow), the ratchet is monotone
non-decreasing: an accepted candidate's distance dominates the previous
ratchet by the acceptance guard itself. -/
theorem annLoopStep_ratchet_monotone_in_fill_phase
    (st : ANNLoopState) (i neighborsCount featureCount : Nat)
    (currentFeatures : Fin 5 → PSFloat) (featureArrays : Fin 5 → Array PSFloat)
    (yTrainArray : Array Int)
    (hgrow : (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
      yTrainArray).predictions.size = st.predictions.size + 1) :
    st.lastDistance ≤
      (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
        yTrainArray).lastDistance := by
  obtain ⟨d, _, heq, hle⟩ :=
    annLoopStep_accept_updates_ratchet st i neighborsCount featureCount
      currentFeatures featureArrays yTrainArray hgrow
  rw [heq]
  exact hle

/-- The scan index sequence covers exactly the closed interval between
`startIndex` and `sizeLoop` (ascending or descending). -/
theorem mem_annScanIndices {startIndex sizeLoop j : Nat} :
    j ∈ annScanIndices startIndex sizeLoop ↔
      min startIndex sizeLoop ≤ j ∧ j ≤ max startIndex sizeLoop := by
  unfold annScanIndices
  split
  · next hle =>
    simp only [List.mem_map, List.mem_range]
    constructor
    · rintro ⟨n, hn, rfl⟩
      omega
    · intro ⟨h1, h2⟩
      exact ⟨j - startIndex, by omega, by omega⟩
  · next hgt =>
    simp only [List.mem_map, List.mem_range]
    constructor
    · rintro ⟨n, hn, rfl⟩
      omega
    · intro ⟨h1, h2⟩
      exact ⟨startIndex - j, by omega, by omega⟩

/-- The scan visits the correct number of candidates. -/
theorem annScanIndices_length (startIndex sizeLoop : Nat) :
    (annScanIndices startIndex sizeLoop).length =
      (if startIndex ≤ sizeLoop then sizeLoop - startIndex else startIndex - sizeLoop) + 1 := by
  unfold annScanIndices
  split <;> simp

/-- Helper: a left fold of integer addition over labels of magnitude ≤ 1 grows
by at most one per element. -/
theorem foldl_add_natAbs_le (l : List Int) (acc : Int)
    (h : ∀ p ∈ l, p.natAbs ≤ 1) :
    (l.foldl (· + ·) acc).natAbs ≤ acc.natAbs + l.length := by
  induction l generalizing acc with
  | nil => simp
  | cons p t ih =>
    have hp : p.natAbs ≤ 1 := h p (List.mem_cons_self ..)
    have ht : ∀ q ∈ t, q.natAbs ≤ 1 := fun q hq => h q (List.mem_cons_of_mem _ hq)
    have := ih (acc + p) ht
    have habs : (acc + p).natAbs ≤ acc.natAbs + p.natAbs := Int.natAbs_add_le acc p
    simp only [List.foldl_cons, List.length_cons]
    omega

/-- The prediction is bounded by the number of surviving neighbors when every
stored label has magnitude ≤ 1 (training labels are `Direction.toInt`).
Together with `annLoopStep_preserves_size_bound` this yields the spec-level
bound `|prediction| ≤ neighborsCount`. -/
theorem computePrediction_natAbs_le (st : ANNLoopState)
    (h : ∀ p ∈ st.predictions, p.natAbs ≤ 1) :
    (computePrediction st).natAbs ≤ st.predictions.size := by
  unfold computePrediction
  have hl : ∀ p ∈ st.predictions.toList, p.natAbs ≤ 1 := by
    intro p hp
    exact h p (by simpa using hp)
  calc (st.predictions.foldl (· + ·) 0).natAbs
      = (st.predictions.toList.foldl (· + ·) 0).natAbs := by
        rw [Array.foldl_toList]
    _ ≤ (0 : Int).natAbs + st.predictions.toList.length := foldl_add_natAbs_le _ 0 hl
    _ = st.predictions.size := by simp

/-- Every training label has magnitude ≤ 1. -/
theorem direction_toInt_natAbs_le_one (d : Direction) : d.toInt.natAbs ≤ 1 := by
  cases d <;> decide

/-- `ofInt` is a left inverse of `toInt`. -/
theorem direction_ofInt_toInt (d : Direction) : Direction.ofInt d.toInt = d := by
  cases d <;> decide

/-- On overflow, the ratchet re-anchors to the banker's-rounded three-quarter
index of the post-push distance array, and the OLDEST neighbor is evicted
from both buffers (Pine `array.shift`). Together with
`annLoopStep_accept_updates_ratchet` this pins both halves of the historic
ratchet semantics. -/
theorem annLoopStep_overflow_reanchors_and_evicts
    (st : ANNLoopState) (i neighborsCount featureCount : Nat)
    (currentFeatures : Fin 5 → PSFloat) (featureArrays : Fin 5 → Array PSFloat)
    (yTrainArray : Array Int) (d : Float)
    (hd : lorentzianDistancePS i featureCount currentFeatures featureArrays = some d)
    (hacc : (d ≥ st.lastDistance && (i % 4 != 0)) = true)
    (hover : st.predictions.size + 1 > neighborsCount) :
    (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
      yTrainArray).lastDistance =
      (st.distances.push d).getD
        ((roundTiesEven (neighborsCount.toFloat * 3.0 / 4.0)).toUInt64.toNat) d ∧
    (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
      yTrainArray).predictions =
      (st.predictions.push (yTrainArray.getD i 0)).extract 1 (st.predictions.size + 1) ∧
    (annLoopStep st i neighborsCount featureCount currentFeatures featureArrays
      yTrainArray).distances =
      (st.distances.push d).extract 1 (st.distances.size + 1) := by
  unfold annLoopStep
  rw [hd]
  simp only [hacc, if_pos]
  split
  · next hover2 =>
    refine ⟨rfl, ?_, ?_⟩ <;> simp [Array.size_push]
  · next hover2 =>
    exfalso
    simp only [Array.size_push] at hover2
    omega

end LorentzianClassification.Properties
