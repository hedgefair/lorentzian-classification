import LorentzianClassification.Basic

/-!
# Nadaraya-Watson kernel regression (Rational Quadratic, Gaussian)

Mirror of Python `kernel_rational_quadratic`/`kernel_gaussian`
(`core.py:916-937`): the window is `i = 0 .. min(1 + startAtBar, barIndex)`
inclusive — clipped at the available history, so the estimate is defined from
bar 0 (falling back to the current source value if the cumulative weight is not
positive).

Deviation from both the premium `LorentzianSpec.Kernels` and PineScript (which
yield `na` until `startAtBar + 2` bars exist): the Python/Rust ports clip the
window instead, and they are the parity authority for the 40-column output
schema. Documented in `ports/lean/README.md`.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- Rational Quadratic kernel estimate at the latest bar of `src`
(weights `(1 + i²/(2·r·h²))^(−r)`). -/
def rationalQuadratic (src : Array Float) (lookback : Nat) (relativeWeight : Float)
    (startAtBar : Nat) : Float := Id.run do
  if src.isEmpty then
    return 0.0
  let barIndex := src.size - 1
  let denom := max ((lookback * lookback).toFloat * 2.0 * relativeWeight) 0.0000000001
  let count := min (1 + startAtBar) barIndex + 1
  let mut currentWeight := 0.0
  let mut cumulativeWeight := 0.0
  for i in [0:count] do
    let weight := (1.0 + (i * i).toFloat / denom) ^ (-relativeWeight)
    currentWeight := currentWeight + src.getD (barIndex - i) 0.0 * weight
    cumulativeWeight := cumulativeWeight + weight
  if cumulativeWeight > 0.0 then
    return currentWeight / cumulativeWeight
  return src.getD barIndex 0.0

/-- Gaussian kernel estimate at the latest bar of `src`
(weights `exp(−i²/(2·h²))`). -/
def gaussian (src : Array Float) (lookback : Nat) (startAtBar : Nat) : Float := Id.run do
  if src.isEmpty then
    return 0.0
  let barIndex := src.size - 1
  let denom := max (2.0 * (lookback * lookback).toFloat) 0.0000000001
  let count := min (1 + startAtBar) barIndex + 1
  let mut currentWeight := 0.0
  let mut cumulativeWeight := 0.0
  for i in [0:count] do
    let weight := Float.exp (-(i * i).toFloat / denom)
    currentWeight := currentWeight + src.getD (barIndex - i) 0.0 * weight
    cumulativeWeight := cumulativeWeight + weight
  if cumulativeWeight > 0.0 then
    return currentWeight / cumulativeWeight
  return src.getD barIndex 0.0

end LorentzianClassification
