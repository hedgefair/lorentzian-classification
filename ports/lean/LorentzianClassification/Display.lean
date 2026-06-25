import LorentzianClassification.Basic

/-!
# Display encoding (colors, labels)

Mirror of Python's display helpers (`core.py:623-670`, `pine_color`) and the
Rust `display.rs`. Colors are `"#RRGGBB@<transparency>"` strings so the display
columns compare byte-for-byte across ports.
-/
set_option autoImplicit false

namespace LorentzianClassification

namespace Display

/-- Encode a hex color with a transparency, e.g. `pineColor "#009988" 20`. -/
def pineColor (hexColor : String) (transparency : Int) : String :=
  s!"{hexColor}@{transparency}"

/-- Re-encode an existing `"#RRGGBB@t"` color with a new transparency. -/
def colorWithTransparency (color : String) (transparency : Int) : String :=
  let base := (color.splitOn "@").headD color
  pineColor base transparency

/-- Bullish base color. -/
def green : String := "#009988"

/-- Bearish base color. -/
def red : String := "#CC3311"

/-- Confidence-gradient transparency ladder shared by the green/red helpers. -/
def gradientTransparency (prediction : Float) : Int :=
  let scaled := min (Float.abs prediction) 10.0
  if scaled >= 9.0 then 0
  else if scaled >= 8.0 then 10
  else if scaled >= 7.0 then 20
  else if scaled >= 6.0 then 30
  else if scaled >= 5.0 then 40
  else if scaled >= 4.0 then 50
  else if scaled >= 3.0 then 60
  else if scaled >= 2.0 then 70
  else if scaled >= 1.0 then 80
  else 90

/-- Bullish prediction color, optionally shaded by confidence. -/
def predictionGreen (prediction : Float) (useConfidenceGradient : Bool) : String :=
  if !useConfidenceGradient then
    pineColor green 0
  else
    pineColor green (gradientTransparency prediction)

/-- Bearish prediction color, optionally shaded by confidence. -/
def predictionRed (prediction : Float) (useConfidenceGradient : Bool) : String :=
  if !useConfidenceGradient then
    pineColor red 0
  else
    pineColor red (gradientTransparency prediction)

/-- The neutral prediction color. -/
def neutralColor : String := pineColor "#787b86" 25

/-- The fully transparent color used when the kernel plot is hidden. -/
def transparentColor : String := pineColor "#000000" 100

/-- The trade-stats header cell (`📈 Trade Stats`). -/
def tradeStatsHeader : String := "📈 Trade Stats"

end Display

end LorentzianClassification
