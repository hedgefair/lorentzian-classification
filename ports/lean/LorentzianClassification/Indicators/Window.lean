import LorentzianClassification.Basic

/-!
# Sliding-window helpers

Shared by the SMA/EMA/RMA/CCI indicator states. The window stores the last
`length` raw inputs *including* `na` values, so that window-based seeding and
NaN-poisoning behave exactly like the batch implementations in the Python
reference (`core.py: sma/ema/rma`), which recompute over the trailing slice of
the raw input series.
-/
set_option autoImplicit false

namespace LorentzianClassification

namespace Window

/-- Append `v`, keeping only the most recent `length` values
(`length` is clamped to ≥ 1 by the indicator constructors). -/
def push (window : Array PSFloat) (length : Nat) (v : PSFloat) : Array PSFloat :=
  if window.size >= length then
    (window.extract 1 window.size).push v
  else
    window.push v

/-- Mean of a full, `na`-free window; `na` otherwise. A stored IEEE NaN counts
as missing (Python's `is_missing` is `isnan`). The sum is accumulated
oldest-first, matching Python's left-to-right slice sum bit-for-bit. -/
def mean (window : Array PSFloat) (length : Nat) : PSFloat :=
  if window.size < length then
    PSFloat.na
  else
    let sum := window.foldl
      (fun acc v =>
        match acc, v with
        | some s, some x => if x.isNaN then none else some (s + x)
        | _, _ => none)
      (some 0.0)
    match sum with
    | some s => some (s / length.toFloat)
    | none => PSFloat.na

end Window

end LorentzianClassification
