/-!
# Basic value types

Pine-style optional floats (`na` semantics), trade directions, and the
constrained setting types shared across the specification.

Curated from the team-reviewed `LorentzianSpec.Basic` (premium repo); the JSON
derives were dropped so the library depends only on core Lean.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- PineScript values can be `na` (not available) or a concrete `Float`. -/
abbrev PSFloat := Option Float

namespace PSFloat

/-- The Pine `na` value. -/
def na : PSFloat := none

/-- Wrap a concrete value. -/
def val (x : Float) : PSFloat := some x

/-- `nz(x)` returns `x` if available, `fallback` otherwise. -/
def nz (x : PSFloat) (fallback : Float := 0.0) : Float :=
  x.getD fallback

/-- Lift a binary `Float` operation while propagating `na`. -/
def lift2 (f : Float → Float → Float) (a b : PSFloat) : PSFloat :=
  match a, b with
  | some x, some y => some (f x y)
  | _, _ => none

/-- Lift a comparison, returning `false` when either operand is `na`. -/
def liftCmp (f : Float → Float → Bool) (a b : PSFloat) : Bool :=
  match a, b with
  | some x, some y => f x y
  | _, _ => false

/-- Lift a unary operation while propagating `na`. -/
def map (f : Float → Float) (a : PSFloat) : PSFloat :=
  Option.map f a

/-- `true` when the value is `na` or a stored IEEE NaN (Python's `is_missing`). -/
def isMissing (x : PSFloat) : Bool :=
  match x with
  | none => true
  | some v => v.isNaN

end PSFloat

/-- Trade direction enumeration. -/
inductive Direction where
  | long
  | short
  | neutral
  deriving Repr, BEq, DecidableEq, Inhabited

namespace Direction

/-- Pine `direction` integers: `long = 1`, `short = -1`, `neutral = 0`. -/
def toInt : Direction → Int
  | .long => 1
  | .short => -1
  | .neutral => 0

/-- Inverse of `toInt` (any unmapped integer is `neutral`). -/
def ofInt : Int → Direction
  | 1 => .long
  | -1 => .short
  | _ => .neutral

/-- `toInt` as a `Float`. -/
def toFloat : Direction → Float
  | .long => 1.0
  | .short => -1.0
  | .neutral => 0.0

end Direction

/-- Neighbor count constrained to the Pine input range (`minval=1, maxval=100`).
The bound makes the `|prediction| ≤ neighborsCount ≤ 100` invariant a type-level
fact rather than tribal knowledge. -/
abbrev NeighborsCount := { n : Nat // 1 ≤ n ∧ n ≤ 100 }

instance : Inhabited NeighborsCount := ⟨⟨8, by decide⟩⟩

/-- Feature count constrained to the Pine input range (`minval=2, maxval=5`). -/
abbrev FeatureCount := { n : Nat // 2 ≤ n ∧ n ≤ 5 }

instance : Inhabited FeatureCount := ⟨⟨5, by decide⟩⟩

/-- The price series a feature or kernel reads from (Pine `input.source`). -/
inductive Source where
  | open_
  | high
  | low
  | close
  | hl2
  | hlc3
  | ohlc4
  deriving Repr, BEq, DecidableEq, Inhabited

end LorentzianClassification
