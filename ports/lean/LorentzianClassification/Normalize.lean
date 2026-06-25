import LorentzianClassification.Basic

/-!
# Rescaling and running normalization

Mirrors `rescale` / `normalize_running` in the Python reference
(`core.py:735-752`), which themselves mirror `MLExtensions.rescale` /
`MLExtensions.normalize` (where Pine's `10e-10` literal is `1e-9`).

Deviation from the premium `LorentzianSpec.Normalize`: the denominator floor
there was `1e-10`; the Python/Rust/Pine value is `1e-9` and is used here.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- State for Pine's stateful `normalize`: all-time historic min/max,
seeded with the `±1e11` sentinels from `MLExtensions.normalize`. -/
structure NormalizeState where
  historicMin : Float
  historicMax : Float
  deriving Repr, Inhabited

namespace NormalizeState

/-- Sentinel-seeded initial state (`var _historicMin = 10e10` = `1e11`). -/
def init : NormalizeState :=
  { historicMin := 100000000000.0, historicMax := -100000000000.0 }

/-- One step of the running normalization. `na` inputs pass through without
updating the extrema (Python `normalize_running` skips NaN inputs). -/
def step (st : NormalizeState) (src : PSFloat) (lower upper : Float) :
    NormalizeState × PSFloat :=
  match src with
  | none => (st, PSFloat.na)
  | some x =>
    let newMin := min x st.historicMin
    let newMax := max x st.historicMax
    let result := lower + (upper - lower) * (x - newMin) / max (newMax - newMin) 0.000000001
    ({ historicMin := newMin, historicMax := newMax }, some result)

end NormalizeState

/-- Stateless linear rescale from `[oldMin, oldMax]` to `[newMin, newMax]`
(Python `rescale`, denominator floored at `1e-9`). -/
def rescale (src : PSFloat) (oldMin oldMax newMin newMax : Float) : PSFloat :=
  src.map fun x =>
    newMin + (newMax - newMin) * (x - oldMin) / max (oldMax - oldMin) 0.000000001

end LorentzianClassification
