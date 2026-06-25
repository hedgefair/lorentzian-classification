import LorentzianClassification.Basic

/-!
# Time-series containers

Bars and Pine-style lookback access (`x[n]` where `0` is the current bar).
Curated from `LorentzianSpec.TimeSeries`; the unused `FloatSeries.windowSum`
and `FloatSeries.change` helpers and the JSON derives were dropped.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- A single OHLC bar of market data. `hlc3`/`ohlc4` are precomputed by the
loader exactly as the Python reference does (`(h+l+c)/3`, `(o+h+l+c)/4`) so all
ports share one rounding. `barIndex` is the 0-based chronological index. -/
structure Bar where
  open_ : Float
  high : Float
  low : Float
  close : Float
  hlc3 : Float
  ohlc4 : Float
  barIndex : Nat
  deriving Repr, Inhabited

namespace Bar

/-- Project the configured source series from a bar
(mirror of Python `select_source`). -/
def source (b : Bar) : Source → Float
  | .open_ => b.open_
  | .high => b.high
  | .low => b.low
  | .close => b.close
  | .hl2 => (b.high + b.low) / 2.0
  | .hlc3 => b.hlc3
  | .ohlc4 => b.ohlc4

end Bar

/-- A time series of PineScript floats, oldest first. -/
structure FloatSeries where
  values : Array PSFloat
  deriving Repr, Inhabited

namespace FloatSeries

/-- Number of stored values. -/
def size (s : FloatSeries) : Nat :=
  s.values.size

/-- Append the current bar's value. -/
def push (s : FloatSeries) (v : PSFloat) : FloatSeries :=
  { values := s.values.push v }

/-- Pine lookback access: `get? 0` is the current bar, `na` out of range. -/
def get? (s : FloatSeries) (barsAgo : Nat) : PSFloat :=
  if barsAgo + 1 > s.values.size then
    PSFloat.na
  else
    s.values.getD (s.values.size - 1 - barsAgo) PSFloat.na

end FloatSeries

/-- Rolling boolean series for Pine lookbacks such as `startLongTrade[4]`. -/
structure BoolSeries where
  values : Array Bool
  deriving Repr, Inhabited

namespace BoolSeries

/-- Append the current bar's value. -/
def push (s : BoolSeries) (v : Bool) : BoolSeries :=
  { values := s.values.push v }

/-- Pine lookback access: `false` out of range (Pine `na` coerced to false). -/
def get? (s : BoolSeries) (barsAgo : Nat) : Bool :=
  if barsAgo + 1 > s.values.size then
    false
  else
    s.values.getD (s.values.size - 1 - barsAgo) false

end BoolSeries

end LorentzianClassification
