import LorentzianClassification.Basic

/-!
# Lorentzian distance

`d(x, y) = Σ_k ln(1 + |x_k − y_k|)` over the first `featureCount` feature
slots. A missing feature (current or historical) rejects the candidate
entirely — equivalent to the `-inf` sentinel in the Python/Rust `distance`.

The sums are LEFT-associated, matching the Python/Rust accumulation order
bit-for-bit (the premium spec right-associated them; the low-bit difference
can flip an ANN ratchet acceptance).
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- Plain-`Float` Lorentzian distance (used by the property layer). -/
def lorentzianDistance (i featureCount : Nat) (currentFeatures : Fin 5 → Float)
    (featureArrays : Fin 5 → Array Float) : Float :=
  let d (k : Fin 5) : Float :=
    let hist := (featureArrays k).getD i 0.0
    Float.log (1.0 + Float.abs (currentFeatures k - hist))
  let d0 := d 0
  let d1 := d 1
  let d2 := d 2
  let d3 := d 3
  let d4 := d 4
  match featureCount with
  | 5 => d0 + d1 + d2 + d3 + d4
  | 4 => d0 + d1 + d2 + d3
  | 3 => d0 + d1 + d2
  | _ => d0 + d1

/-- Lorentzian distance with Pine-style `na` propagation: `none` when any
participating feature is missing (the candidate can never be accepted, exactly
like Python's `-inf >= lastDistance` rejection). -/
def lorentzianDistancePS (i featureCount : Nat) (currentFeatures : Fin 5 → PSFloat)
    (featureArrays : Fin 5 → Array PSFloat) : PSFloat :=
  let d (k : Fin 5) : PSFloat :=
    match currentFeatures k, (featureArrays k).getD i none with
    | some current, some hist =>
      some (Float.log (1.0 + Float.abs (current - hist)))
    | _, _ => none
  let add := PSFloat.lift2 (· + ·)
  let d0 := d 0
  let d1 := d 1
  let d2 := d 2
  let d3 := d 3
  let d4 := d 4
  match featureCount with
  | 5 => add (add (add (add d0 d1) d2) d3) d4
  | 4 => add (add (add d0 d1) d2) d3
  | 3 => add (add d0 d1) d2
  | _ => add d0 d1

end LorentzianClassification
