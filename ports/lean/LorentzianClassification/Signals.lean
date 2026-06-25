import LorentzianClassification.Basic
import LorentzianClassification.TimeSeries

/-!
# Signal state machine

Mirror of the per-bar signal logic in Python `core.py:1094-1140`. The signal
holds its previous value when the prediction is zero or any filter fails; the
early-flip detector keeps a 3-deep shift register of past signal changes; the
`barssince` counters initialize at `999999` and increment every bar.

Documented Python-vs-Pine divergences preserved here (Python is the parity
authority): `isLastSignalBuy` checks only `signal[4]` (Pine also requires the
4-bars-ago EMA/SMA uptrend flags), and the `999999` counters make never-fired
conditions compare as *large* instead of Pine's `na`-false. Both only surface
under non-default filter/exit settings.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- Sentinel for "condition has never fired" (Python `999999`). -/
def barsSinceNever : Nat := 999999

/-- Signal-level rolling state. -/
structure SignalState where
  signal : Direction
  barsHeld : Nat
  signalHistory : Array Int
  startLongTradeHistory : BoolSeries
  startShortTradeHistory : BoolSeries
  prevSignalChange : Int
  prevSignalChange1 : Int
  prevSignalChange2 : Int
  barsSinceStartLong : Nat
  barsSinceStartShort : Nat
  barsSinceAlertBull : Nat
  barsSinceAlertBear : Nat
  prevValidLongExit : Bool
  prevValidShortExit : Bool
  deriving Repr, Inhabited

namespace SignalState

/-- Initial signal state. -/
def init : SignalState :=
  { signal := .neutral
  , barsHeld := 0
  , signalHistory := #[]
  , startLongTradeHistory := { values := #[] }
  , startShortTradeHistory := { values := #[] }
  , prevSignalChange := 0
  , prevSignalChange1 := 0
  , prevSignalChange2 := 0
  , barsSinceStartLong := barsSinceNever
  , barsSinceStartShort := barsSinceNever
  , barsSinceAlertBull := barsSinceNever
  , barsSinceAlertBear := barsSinceNever
  , prevValidLongExit := false
  , prevValidShortExit := false }

end SignalState

/-- Next signal value: flips on a nonzero prediction that passes all filters,
holds otherwise (Pine line 426). -/
def nextSignal (prediction : Int) (filterAll : Bool) (prev : Direction) : Direction :=
  if prediction > 0 && filterAll then
    .long
  else if prediction < 0 && filterAll then
    .short
  else
    prev

/-- Bars-held counter: resets on a signal change, increments otherwise. -/
def nextBarsHeld (signalChanged : Bool) (barsHeld : Nat) : Nat :=
  if signalChanged then 0 else barsHeld + 1

/-- One `ta.barssince`-style counter update. -/
def nextBarsSince (fired : Bool) (counter : Nat) : Nat :=
  if fired then 0 else counter + 1

/-- Backtest stream encoding (Pine lines 616-621): `1` start long, `2` end
long, `-1` start short, `-2` end short, `0` otherwise (the writer renders `0`
as an empty cell). -/
def backtestStream (startLong endLong startShort endShort : Bool) : Int :=
  if startLong then 1
  else if endLong then 2
  else if startShort then -1
  else if endShort then -2
  else 0

end LorentzianClassification
