import LorentzianClassification.Basic

/-!
# Training labels

Mirror of Pine line 337 / Python `core.py:1044-1046`. The label pushed at bar
`t` describes the 4-bar move ending at `t`, and the long/short mapping is
deliberately inverted relative to intuition (a price RISE labels SHORT): this
is verbatim ground truth from the original indicator, not a bug to fix. `Float`
comparisons make NaN sources fall through to `neutral`, matching Python's
`partial_cmp → 0`.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- Retrospective label over a four-bar horizon. -/
def computeTrainingLabel (srcCurrent srcFourBarsAgo : Float) : Direction :=
  if srcFourBarsAgo < srcCurrent then
    Direction.short
  else if srcFourBarsAgo > srcCurrent then
    Direction.long
  else
    Direction.neutral

end LorentzianClassification
