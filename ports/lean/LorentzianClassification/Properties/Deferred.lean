import LorentzianClassification.Distance
import LorentzianClassification.Normalize
import LorentzianClassification.Kernels

/-!
# Deferred and model-level invariants

The normalization and kernel bounds are stated and PROVED over `Rat` (where
they are true). Adversarial review produced concrete IEEE counterexamples
where the exact Float bounds are violated by one ulp of final-operation
rounding, so the Float-exact forms are FALSE and are deliberately not stated;
the executable tests in `ports/lean/Tests.lean` check the Float behavior with
an explicit `1e-12`/`1e-9` rounding slack.

`lorentzianDistance_nonneg` remains the one `sorry`-deferred statement: it is
about opaque IEEE operations (`Float.log` monotonicity) that core Lean cannot
reason about, and is exercised executably instead.

Core Lean ships only a thin `Rat` lemma library, so the ordered-field facts
needed here (min/max bounds, division bounds, sum monotonicity) are derived
from first principles in the `RatLemmas` section below.
-/
set_option autoImplicit false

namespace LorentzianClassification.Properties

open LorentzianClassification

/-- The Lorentzian distance is non-negative for finite feature values
(`log(1 + |x|) ≥ 0`; `1 + |x| ≥ 1` holds exactly under IEEE rounding, and
`Float.log` is monotone with `log 1 = 0` for any faithful libm). NaN inputs
are excluded by hypothesis — over raw IEEE floats the unguarded statement is
false. Deferred: core Lean exposes `Float.log` as an opaque operation. -/
theorem lorentzianDistance_nonneg (i featureCount : Nat)
    (currentFeatures : Fin 5 → Float) (featureArrays : Fin 5 → Array Float)
    (hcur : ∀ k, (currentFeatures k).isFinite)
    (hhist : ∀ k, ∀ v ∈ featureArrays k, v.isFinite) :
    0.0 ≤ lorentzianDistance i featureCount currentFeatures featureArrays := by
  sorry

/-! ## Rat scaffolding (core Lean has no ordered-field lemma library) -/

section RatLemmas

private theorem rat_min_le_left (a b : Rat) : min a b ≤ a := by
  show (if a ≤ b then a else b) ≤ a
  split
  · exact Rat.le_refl
  · next h => exact Rat.le_of_lt (Rat.not_le.mp h)

private theorem rat_le_max_left (a b : Rat) : a ≤ max a b := by
  show a ≤ if a ≤ b then b else a
  split
  · next h => exact h
  · exact Rat.le_refl

private theorem rat_le_max_right (a b : Rat) : b ≤ max a b := by
  show b ≤ if a ≤ b then b else a
  split
  · exact Rat.le_refl
  · next h => exact Rat.le_of_lt (Rat.not_le.mp h)

private theorem rat_sub_le_sub_right {a b : Rat} (h : a ≤ b) (c : Rat) :
    a - c ≤ b - c := by
  rw [← Rat.add_le_add_right (c := c), Rat.sub_add_cancel, Rat.sub_add_cancel]
  exact h

private theorem rat_ne_of_pos {a : Rat} (h : 0 < a) : a ≠ 0 := by
  intro h0
  rw [h0] at h
  exact (Lean.Grind.Preorder.lt_irrefl (a := (0 : Rat))) h

private theorem rat_lt_of_lt_of_le {a b c : Rat} (h1 : a < b) (h2 : b ≤ c) : a < c :=
  Rat.not_le.mp (fun hca => (Rat.not_le.mpr h1) (Rat.le_trans h2 hca))

private theorem rat_div_nonneg {a b : Rat} (ha : 0 ≤ a) (hb : 0 < b) :
    0 ≤ a / b := by
  rw [Rat.div_def]
  exact Rat.mul_nonneg ha (Rat.le_of_lt (Rat.inv_pos.mpr hb))

/-- `a ≤ c·b` with `b > 0` gives `a/b ≤ c`. -/
private theorem rat_div_le_of_le_mul {a b c : Rat} (h : a ≤ c * b) (hb : 0 < b) :
    a / b ≤ c := by
  rw [Rat.div_def]
  have h1 : a * b⁻¹ ≤ c * b * b⁻¹ :=
    Rat.mul_le_mul_of_nonneg_right h (Rat.le_of_lt (Rat.inv_pos.mpr hb))
  rwa [Rat.mul_assoc, Rat.mul_inv_cancel b (rat_ne_of_pos hb), Rat.mul_one] at h1

/-- `c·b ≤ a` with `b > 0` gives `c ≤ a/b`. -/
private theorem rat_le_div_of_mul_le {a b c : Rat} (h : c * b ≤ a) (hb : 0 < b) :
    c ≤ a / b := by
  rw [Rat.div_def]
  have h1 : c * b * b⁻¹ ≤ a * b⁻¹ :=
    Rat.mul_le_mul_of_nonneg_right h (Rat.le_of_lt (Rat.inv_pos.mpr hb))
  rwa [Rat.mul_assoc, Rat.mul_inv_cancel b (rat_ne_of_pos hb), Rat.mul_one] at h1

private theorem rat_add_le_add {a b c d : Rat} (h1 : a ≤ b) (h2 : c ≤ d) :
    a + c ≤ b + d :=
  Rat.le_trans ((Rat.add_le_add_right (c := c)).mpr h1)
    ((Rat.add_le_add_left (c := b)).mpr h2)

end RatLemmas

/-! ## ℚ-model invariants (proved) -/

/-- ℚ-model of one running-normalization step (`NormalizeState.step` with the
`1e-9` denominator floor). The Float implementation realizes this exactly up
to final-operation rounding. -/
def ratNormalizeStep (historicMin historicMax x lower upper : Rat) : Rat :=
  let newMin := min x historicMin
  let newMax := max x historicMax
  lower + (upper - lower) * (x - newMin) / max (newMax - newMin) (1 / 1000000000)

/-- The normalization output stays inside `[lower, upper]` over ℚ: the input
is always inside the updated historic range, and the denominator dominates
that range. (True over ℚ; the Float-exact form is false by one ulp of final
rounding — tested with slack in `Tests.lean`.) -/
theorem ratNormalizeStep_bounded (historicMin historicMax x lower upper : Rat)
    (hBounds : lower ≤ upper) :
    lower ≤ ratNormalizeStep historicMin historicMax x lower upper ∧
      ratNormalizeStep historicMin historicMax x lower upper ≤ upper := by
  have hEps : (0 : Rat) < 1 / 1000000000 := by native_decide
  have hMinLe : min x historicMin ≤ x := rat_min_le_left ..
  have hLeMax : x ≤ max x historicMax := rat_le_max_left ..
  have hNumNonneg : 0 ≤ x - min x historicMin :=
    (Rat.le_iff_sub_nonneg (min x historicMin) x).mp hMinLe
  have hDenPos : 0 < max (max x historicMax - min x historicMin) (1 / 1000000000) :=
    rat_lt_of_lt_of_le hEps (rat_le_max_right ..)
  have hNumLeDen : x - min x historicMin ≤
      max (max x historicMax - min x historicMin) (1 / 1000000000) :=
    Rat.le_trans (rat_sub_le_sub_right hLeMax (min x historicMin)) (rat_le_max_left ..)
  have hUL : 0 ≤ upper - lower := (Rat.le_iff_sub_nonneg lower upper).mp hBounds
  -- the quotient term t = ((upper − lower)·num)/den satisfies 0 ≤ t ≤ upper − lower
  have hTNonneg : 0 ≤ (upper - lower) * (x - min x historicMin) /
      max (max x historicMax - min x historicMin) (1 / 1000000000) :=
    rat_div_nonneg (Rat.mul_nonneg hUL hNumNonneg) hDenPos
  have hTLe : (upper - lower) * (x - min x historicMin) /
      max (max x historicMax - min x historicMin) (1 / 1000000000) ≤ upper - lower :=
    rat_div_le_of_le_mul (Rat.mul_le_mul_of_nonneg_left hNumLeDen hUL) hDenPos
  constructor
  · show lower ≤ lower + (upper - lower) * (x - min x historicMin) /
      max (max x historicMax - min x historicMin) (1 / 1000000000)
    have h := (Rat.add_le_add_left (c := lower)).mpr hTNonneg
    rwa [Rat.add_zero] at h
  · show lower + (upper - lower) * (x - min x historicMin) /
      max (max x historicMax - min x historicMin) (1 / 1000000000) ≤ upper
    have h := (Rat.add_le_add_left (c := lower)).mpr hTLe
    have hcancel : lower + (upper - lower) = upper := by
      rw [Rat.add_comm, Rat.sub_add_cancel]
    rwa [hcancel] at h

/-- ℚ-model of a normalized weighted mean over (value, weight) pairs — the
shape of both kernel estimators (`rationalQuadratic`, `gaussian`), whose
weights are positive. -/
def ratWeightedMean (pairs : List (Rat × Rat)) : Rat :=
  (pairs.foldl (fun acc p => acc + p.1 * p.2) 0) /
    (pairs.foldl (fun acc p => acc + p.2) 0)

/-- Fold-level bounds: with positive weights and `lo ≤ v ≤ hi` per pair, the
weighted sum is bracketed by `lo·W` and `hi·W` and the weight sum dominates
its accumulator. -/
private theorem ratWeightedSum_bounds (pairs : List (Rat × Rat)) (lo hi : Rat)
    (hw : ∀ p ∈ pairs, 0 < p.2) (hv : ∀ p ∈ pairs, lo ≤ p.1 ∧ p.1 ≤ hi)
    (accS accW : Rat)
    (hlo : lo * accW ≤ accS) (hhi : accS ≤ hi * accW) :
    lo * (pairs.foldl (fun acc p => acc + p.2) accW) ≤
        (pairs.foldl (fun acc p => acc + p.1 * p.2) accS) ∧
      (pairs.foldl (fun acc p => acc + p.1 * p.2) accS) ≤
        hi * (pairs.foldl (fun acc p => acc + p.2) accW) ∧
      accW ≤ pairs.foldl (fun acc p => acc + p.2) accW := by
  induction pairs generalizing accS accW with
  | nil => exact ⟨hlo, hhi, Rat.le_refl⟩
  | cons p rest ih =>
    have hwp : 0 < p.2 := hw p (List.mem_cons_self ..)
    have hvp := hv p (List.mem_cons_self ..)
    have hwRest : ∀ q ∈ rest, 0 < q.2 := fun q hq => hw q (List.mem_cons_of_mem _ hq)
    have hvRest : ∀ q ∈ rest, lo ≤ q.1 ∧ q.1 ≤ hi :=
      fun q hq => hv q (List.mem_cons_of_mem _ hq)
    have hwNonneg : 0 ≤ p.2 := Rat.le_of_lt hwp
    -- new accumulators after consuming p
    have hloStep : lo * (accW + p.2) ≤ accS + p.1 * p.2 := by
      rw [Rat.mul_add]
      exact rat_add_le_add hlo (Rat.mul_le_mul_of_nonneg_right hvp.left hwNonneg)
    have hhiStep : accS + p.1 * p.2 ≤ hi * (accW + p.2) := by
      rw [Rat.mul_add]
      exact rat_add_le_add hhi (Rat.mul_le_mul_of_nonneg_right hvp.right hwNonneg)
    have ⟨h1, h2, h3⟩ := ih hwRest hvRest (accS + p.1 * p.2) (accW + p.2) hloStep hhiStep
    refine ⟨h1, h2, ?_⟩
    calc accW ≤ accW + p.2 := by
          have h := (Rat.add_le_add_left (c := accW)).mpr hwNonneg
          rwa [Rat.add_zero] at h
      _ ≤ rest.foldl (fun acc p => acc + p.2) (accW + p.2) := h3

/-- A positively-weighted normalized mean lies within any bounds enclosing
every value (convex-combination fact, proved over ℚ; the Float-exact form for
the kernel estimators is false by accumulated rounding — tested with slack in
`Tests.lean`). -/
theorem ratWeightedMean_within_bounds (pairs : List (Rat × Rat)) (lo hi : Rat)
    (hpos : ∀ p ∈ pairs, 0 < p.2) (hne : pairs ≠ [])
    (hbound : ∀ p ∈ pairs, lo ≤ p.1 ∧ p.1 ≤ hi) :
    lo ≤ ratWeightedMean pairs ∧ ratWeightedMean pairs ≤ hi := by
  cases pairs with
  | nil => exact absurd rfl hne
  | cons p rest =>
    have hwp : 0 < p.2 := hpos p (List.mem_cons_self ..)
    have hvp := hbound p (List.mem_cons_self ..)
    have hwRest : ∀ q ∈ rest, 0 < q.2 := fun q hq => hpos q (List.mem_cons_of_mem _ hq)
    have hvRest : ∀ q ∈ rest, lo ≤ q.1 ∧ q.1 ≤ hi :=
      fun q hq => hbound q (List.mem_cons_of_mem _ hq)
    have hloHead : lo * (0 + p.2) ≤ 0 + p.1 * p.2 := by
      rw [Rat.zero_add, Rat.zero_add]
      exact Rat.mul_le_mul_of_nonneg_right hvp.left (Rat.le_of_lt hwp)
    have hhiHead : 0 + p.1 * p.2 ≤ hi * (0 + p.2) := by
      rw [Rat.zero_add, Rat.zero_add]
      exact Rat.mul_le_mul_of_nonneg_right hvp.right (Rat.le_of_lt hwp)
    have h := ratWeightedSum_bounds rest lo hi hwRest hvRest
      (0 + p.1 * p.2) (0 + p.2) hloHead hhiHead
    have hWpos : 0 < (p :: rest).foldl (fun acc q => acc + q.2) 0 := by
      show 0 < rest.foldl (fun acc q => acc + q.2) (0 + p.2)
      have hstart : (0 : Rat) < 0 + p.2 := by rwa [Rat.zero_add]
      exact rat_lt_of_lt_of_le hstart h.2.2
    exact ⟨rat_le_div_of_mul_le h.1 hWpos, rat_div_le_of_le_mul h.2.1 hWpos⟩

end LorentzianClassification.Properties
