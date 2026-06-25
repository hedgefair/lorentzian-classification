import LorentzianClassification.Basic

/-!
# Trade statistics

Mirror of the per-bar trade accounting in Python `core.py:1161-1190`
(originally `MLExtensions.analyzePerformance`). Quirks preserved verbatim:
- stats accumulate only when `barIndex > maxBarsBackIndex` (STRICT, vs the
  `≥` prediction gate);
- `marketPrice = (high + low + open + open) / 4` — open deliberately counted
  twice — unless `useWorstCase` selects the source value;
- early-signal flips are counted on `startLong` and `endShort` events only;
- a same-bar `endShort` after `endLong` upgrades (never clears) the per-bar
  win/loss flags (`if delta > 0 → wins := 1`, no else branch);
- `winLossRatio` and `winRate` are intentionally the same formula
  (`wins/(wins+losses)`), kept as two output columns; `tableWLRatio` is
  `wins/losses`; all three are `na` on zero denominators.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- Rolling backtest accumulators. -/
structure BacktestState where
  startLongPrice : Float
  startShortPrice : Float
  totalWins : Int
  totalLosses : Int
  totalEarlySignalFlips : Int
  deriving Repr, Inhabited

/-- Per-bar trade statistics in output form. -/
structure TradeStats where
  totalWins : Int
  totalLosses : Int
  totalEarlySignalFlips : Int
  totalTrades : Int
  winLossRatio : PSFloat
  tableWLRatio : PSFloat
  winRate : PSFloat
  deriving Repr, Inhabited

namespace BacktestState

/-- Initial accumulators. -/
def init : BacktestState :=
  { startLongPrice := 0.0
  , startShortPrice := 0.0
  , totalWins := 0
  , totalLosses := 0
  , totalEarlySignalFlips := 0 }

/-- One bar of trade accounting. -/
def step (st : BacktestState) (marketPrice : Float)
    (startLong endLong startShort endShort isEarlySignalFlip : Bool)
    (barIndex maxBarsBackIndex : Nat) : BacktestState × TradeStats := Id.run do
  let mut startLongPrice := st.startLongPrice
  let mut startShortPrice := st.startShortPrice
  let mut wins : Int := 0
  let mut losses : Int := 0
  let mut earlyFlips : Int := 0
  if barIndex > maxBarsBackIndex then
    if startLong then
      startShortPrice := 0.0
      earlyFlips := if isEarlySignalFlip then 1 else 0
      startLongPrice := marketPrice
    if endLong then
      let delta := marketPrice - startLongPrice
      wins := if delta > 0.0 then 1 else 0
      losses := if delta < 0.0 then 1 else 0
    if startShort then
      startLongPrice := 0.0
      startShortPrice := marketPrice
    if endShort then
      if isEarlySignalFlip then
        earlyFlips := 1
      let delta := startShortPrice - marketPrice
      if delta > 0.0 then
        wins := 1
      if delta < 0.0 then
        losses := 1
  let totalWins := st.totalWins + wins
  let totalLosses := st.totalLosses + losses
  let totalEarlySignalFlips := st.totalEarlySignalFlips + earlyFlips
  let totalTrades := totalWins + totalLosses
  let ratio (num den : Int) : PSFloat :=
    if den != 0 then some (Float.ofInt num / Float.ofInt den) else PSFloat.na
  pure
    ({ startLongPrice := startLongPrice
     , startShortPrice := startShortPrice
     , totalWins := totalWins
     , totalLosses := totalLosses
     , totalEarlySignalFlips := totalEarlySignalFlips },
     { totalWins := totalWins
     , totalLosses := totalLosses
     , totalEarlySignalFlips := totalEarlySignalFlips
     , totalTrades := totalTrades
     , winLossRatio := ratio totalWins totalTrades
     , tableWLRatio := ratio totalWins totalLosses
     , winRate := ratio totalWins (totalWins + totalLosses) })

end BacktestState

end LorentzianClassification
