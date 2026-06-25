import LorentzianClassification.Basic
import LorentzianClassification.TimeSeries
import LorentzianClassification.Features
import LorentzianClassification.Distance
import LorentzianClassification.ANN
import LorentzianClassification.Labels
import LorentzianClassification.Kernels
import LorentzianClassification.KernelFilters
import LorentzianClassification.Filters
import LorentzianClassification.Signals
import LorentzianClassification.Backtest
import LorentzianClassification.Display
import LorentzianClassification.Indicators.ATR
import LorentzianClassification.Indicators.EMA
import LorentzianClassification.Indicators.SMA

/-!
# The per-bar classification pipeline

Streaming formulation of Python `calculate` (`core.py:992-1262`): one explicit
state record threaded bar-to-bar, one `stepBar` producing the full 40-column
result surface. The streaming indicator states are bit-equivalent to the
Python batch vectors (see the per-module docstrings for the equivalence
arguments and the documented deviations).

Batch knowledge: `maxBarsBackIndex` and the ANN's `startIndex` derive from the
FINAL dataset length (`lastBarIndex = bars.size - 1`), exactly like the Python
reference — appending bars and re-running changes earlier outputs by design.
-/
set_option autoImplicit false

namespace LorentzianClassification

/-- Indicator settings, mirroring the Pine `input.*` declarations and the
Python `Settings` dataclass defaults exactly. `colorCompression` is validated
but unused by the math, as in the reference. -/
structure Settings where
  source : Source := .close
  neighborsCount : NeighborsCount := ⟨8, by decide⟩
  maxBarsBack : Nat := 2000
  featureCount : FeatureCount := ⟨5, by decide⟩
  colorCompression : Nat := 1
  includeFullHistory : Bool := false
  useVolatilityFilter : Bool := true
  useRegimeFilter : Bool := true
  useAdxFilter : Bool := false
  regimeThreshold : Float := -0.1
  adxThreshold : Nat := 20
  useEmaFilter : Bool := false
  emaPeriod : Nat := 200
  useSmaFilter : Bool := false
  smaPeriod : Nat := 200
  useKernelFilter : Bool := true
  useKernelSmoothing : Bool := false
  useDynamicExits : Bool := false
  showExits : Bool := false
  useWorstCase : Bool := false
  kernelH : Nat := 8
  kernelR : Float := 8.0
  kernelX : Nat := 25
  kernelLag : Nat := 2
  showKernelEstimate : Bool := true
  showBarColors : Bool := true
  showBarPredictions : Bool := true
  useAtrOffset : Bool := true
  barPredictionsOffset : Float := 0.0
  useConfidenceGradient : Bool := true
  showTradeStats : Bool := true
  features : Array FeatureConfig := #[
    defaultFeatureConfig 0,
    defaultFeatureConfig 1,
    defaultFeatureConfig 2,
    defaultFeatureConfig 3,
    defaultFeatureConfig 4]
  deriving Repr, Inhabited

/-- A fully computed per-bar result, mirroring the Python `ResultRow` minus the
input bar (the CSV runner zips bars back in). -/
structure ResultRow where
  f1 : PSFloat
  f2 : PSFloat
  f3 : PSFloat
  f4 : PSFloat
  f5 : PSFloat
  kernel : Float
  prediction : Int
  direction : Int
  buy : Bool
  sell : Bool
  stopBuy : Bool
  stopSell : Bool
  backtestStream : Int
  openLongAlert : Bool
  closeLongAlert : Bool
  openShortAlert : Bool
  closeShortAlert : Bool
  openPositionAlert : Bool
  closePositionAlert : Bool
  kernelBullishAlert : Bool
  kernelBearishAlert : Bool
  kernelPlotColor : String
  predictionLabel : String
  predictionLabelY : PSFloat
  predictionLabelColor : String
  barColor : String
  tradeStatsVisible : Bool
  tradeStatsHeader : String
  totalWins : Int
  totalLosses : Int
  totalEarlySignalFlips : Int
  totalTrades : Int
  winLossRatio : PSFloat
  tableWLRatio : PSFloat
  winRate : PSFloat
  deriving Repr, Inhabited

/-- Complete explicit pipeline state. -/
structure PipelineState where
  featureEngine : FeatureEngineState
  yTrainArray : Array Int
  annDistances : Array Float
  annPredictions : Array Int
  signalState : SignalState
  regimeState : RegimeFilterState
  emaFilterState : EMAState
  smaFilterState : SMAState
  kernelState : KernelFilterState
  backtestState : BacktestState
  volatilityAtr1 : ATRState
  volatilityAtr10 : ATRState
  adxFilterState : ADXComputeState
  srcHistory : Array Float
  prevOhlc4 : Float
  deriving Repr, Inhabited

namespace PipelineState

/-- Initial pipeline state for the given settings. -/
def init (settings : Settings) : PipelineState :=
  { featureEngine := FeatureEngineState.init settings.features
  , yTrainArray := #[]
  , annDistances := #[]
  , annPredictions := #[]
  , signalState := SignalState.init
  , regimeState := RegimeFilterState.init
  , emaFilterState := EMAState.init settings.emaPeriod
  , smaFilterState := SMAState.init settings.smaPeriod
  , kernelState := { yhat1History := #[], yhat2History := #[] }
  , backtestState := BacktestState.init
  , volatilityAtr1 := ATRState.init 1
  , volatilityAtr10 := ATRState.init 10
  , adxFilterState := ADXComputeState.init 14
  , srcHistory := #[]
  , prevOhlc4 := 0.0 }

end PipelineState

/-- Process one bar: advance every indicator/filter state, run the ANN scan
when eligible, drive the signal machine and trade accounting, and emit the
full result row. Mirrors the loop body of Python `calculate` statement for
statement. -/
def stepBar (settings : Settings) (priceScale : Float) (maxBarsBackIndex : Nat)
    (state : PipelineState) (bar : Bar) : PipelineState × ResultRow :=
  -- 1. Source projection, training label, features (current bar joins the
  --    training set BEFORE the scan, exactly like `ann.push` then `ann.run`).
  let src := bar.source settings.source
  let srcHistory := state.srcHistory.push src
  let trainLabel : Int :=
    if srcHistory.size ≥ 5 then
      let srcCurrent := srcHistory.getD (srcHistory.size - 1) 0.0
      let srcFourBarsAgo := srcHistory.getD (srcHistory.size - 5) 0.0
      (computeTrainingLabel srcCurrent srcFourBarsAgo).toInt
    else
      0
  let yTrainArray := state.yTrainArray.push trainLabel
  let (featureEngine, featureValues) :=
    FeatureEngineState.step settings.features state.featureEngine bar priceScale

  -- 2. Kernel regression estimates for this bar.
  let yhat1 := rationalQuadratic srcHistory settings.kernelH settings.kernelR settings.kernelX
  let lagH := max (settings.kernelH - settings.kernelLag) 1
  let yhat2 := gaussian srcHistory lagH settings.kernelX
  let kernelState : KernelFilterState :=
    { yhat1History := state.kernelState.yhat1History.push yhat1
    , yhat2History := state.kernelState.yhat2History.push yhat2 }

  -- 3. ANN prediction (eligible once barIndex reaches maxBarsBackIndex).
  let eligible := bar.barIndex ≥ maxBarsBackIndex && settings.maxBarsBack != 0
  let annResult :=
    if eligible then
      let sizeLoop := min (settings.maxBarsBack - 1) (yTrainArray.size - 1)
      let startIndex := if settings.includeFullHistory then 0 else maxBarsBackIndex
      annSearch startIndex sizeLoop settings.neighborsCount.val settings.featureCount.val
        (fun i => featureValues.getD i.1 none) featureEngine.arrays yTrainArray
        state.annDistances state.annPredictions
    else
      { lastDistance := -1.0
      , distances := state.annDistances
      , predictions := state.annPredictions }
  let prediction : Int := if eligible then computePrediction annResult else 0

  -- 4. Volatility / regime / ADX filters (states advance every bar).
  let (volatilityAtr1, recentAtr) := state.volatilityAtr1.step bar.high bar.low bar.close
  let (volatilityAtr10, historicalAtr) := state.volatilityAtr10.step bar.high bar.low bar.close
  let (regimeState, absSlope, emaAbsSlope) :=
    state.regimeState.step bar.ohlc4 state.prevOhlc4 bar.high bar.low bar.barIndex
  let (adxFilterState, adxRescaled) :=
    state.adxFilterState.step bar.high bar.low bar.close 14 0.0
  let rawAdx := match adxRescaled with
    | none => 0.0
    | some v => v * 100.0
  let filtVolatility := volatilityFilter recentAtr historicalAtr settings.useVolatilityFilter
  let filtRegime := regimeFilter absSlope emaAbsSlope settings.regimeThreshold settings.useRegimeFilter
  let filtAdx := adxFilter rawAdx settings.adxThreshold settings.useAdxFilter
  let filterAll := filtVolatility && filtRegime && filtAdx

  -- 5. EMA/SMA trend filters (enabled-but-warming-up blocks both directions).
  let emaFilterState := state.emaFilterState.step (some bar.close)
  let isEmaUptrend :=
    match settings.useEmaFilter, emaFilterState.value with
    | true, some v => bar.close > v
    | true, none => false
    | false, _ => true
  let isEmaDowntrend :=
    match settings.useEmaFilter, emaFilterState.value with
    | true, some v => bar.close < v
    | true, none => false
    | false, _ => true
  let (smaFilterState, smaValue) := state.smaFilterState.step (some bar.close)
  let isSmaUptrend :=
    match settings.useSmaFilter, smaValue with
    | true, some v => bar.close > v
    | true, none => false
    | false, _ => true
  let isSmaDowntrend :=
    match settings.useSmaFilter, smaValue with
    | true, some v => bar.close < v
    | true, none => false
    | false, _ => true

  -- 6. Kernel signals.
  let ks := computeKernelSignals kernelState.yhat1History kernelState.yhat2History
  let alertBullish := if settings.useKernelSmoothing then ks.isBullishCross else ks.isBullishChange
  let alertBearish := if settings.useKernelSmoothing then ks.isBearishCross else ks.isBearishChange
  let isBullish :=
    if settings.useKernelFilter then
      if settings.useKernelSmoothing then ks.isBullishSmooth else ks.isBullishRate
    else
      true
  let isBearish :=
    if settings.useKernelFilter then
      if settings.useKernelSmoothing then ks.isBearishSmooth else ks.isBearishRate
    else
      true

  -- 7. Signal machine, early-flip shift register, bars-held counter.
  let previousSignal := state.signalState.signal
  let newSignal := nextSignal prediction filterAll previousSignal
  let signalHistory := state.signalState.signalHistory.push newSignal.toInt
  let signalChange : Int := newSignal.toInt - previousSignal.toInt
  let isDifferentSignalType := signalChange != 0
  let isEarlySignalFlip := isDifferentSignalType &&
    (state.signalState.prevSignalChange != 0 ||
     state.signalState.prevSignalChange1 != 0 ||
     state.signalState.prevSignalChange2 != 0)
  let barsHeld := nextBarsHeld isDifferentSignalType state.signalState.barsHeld
  let isHeldFourBars := barsHeld == 4
  let isHeldLessThanFourBars := 0 < barsHeld && barsHeld < 4

  -- 8. Entries. (Python checks only `direction_buffer[i-4]` for the last
  --    signal — without Pine's 4-bars-ago trend flags; Python is authority.)
  let isBuySignal := newSignal == .long && isEmaUptrend && isSmaUptrend
  let isSellSignal := newSignal == .short && isEmaDowntrend && isSmaDowntrend
  let startLongTrade := isBuySignal && isDifferentSignalType && isBullish
  let startShortTrade := isSellSignal && isDifferentSignalType && isBearish
  let isLastSignalBuy :=
    signalHistory.size ≥ 5 && signalHistory.getD (signalHistory.size - 5) 0 == 1
  let isLastSignalSell :=
    signalHistory.size ≥ 5 && signalHistory.getD (signalHistory.size - 5) 0 == -1
  let startLongTradeHistory := state.signalState.startLongTradeHistory.push startLongTrade
  let startShortTradeHistory := state.signalState.startShortTradeHistory.push startShortTrade

  -- 9. Exit logic (counters update before the validity comparison; the
  --    dynamic path consumes the PREVIOUS bar's validity).
  let barsSinceStartLong := nextBarsSince startLongTrade state.signalState.barsSinceStartLong
  let barsSinceStartShort := nextBarsSince startShortTrade state.signalState.barsSinceStartShort
  let barsSinceAlertBull := nextBarsSince alertBullish state.signalState.barsSinceAlertBull
  let barsSinceAlertBear := nextBarsSince alertBearish state.signalState.barsSinceAlertBear
  let endLongTradeStrict :=
    ((isHeldFourBars && isLastSignalBuy) ||
      (isHeldLessThanFourBars && startShortTrade && isLastSignalBuy)) &&
      startLongTradeHistory.get? 4
  let endShortTradeStrict :=
    ((isHeldFourBars && isLastSignalSell) ||
      (isHeldLessThanFourBars && startLongTrade && isLastSignalSell)) &&
      startShortTradeHistory.get? 4
  let isValidLongExit := barsSinceAlertBear > barsSinceStartLong
  let isValidShortExit := barsSinceAlertBull > barsSinceStartShort
  let endLongTradeDynamic := ks.isBearishChange && state.signalState.prevValidLongExit
  let endShortTradeDynamic := ks.isBullishChange && state.signalState.prevValidShortExit
  let isDynamicExitValid :=
    !settings.useEmaFilter && !settings.useSmaFilter && !settings.useKernelSmoothing
  let endLongTrade :=
    if settings.useDynamicExits && isDynamicExitValid then endLongTradeDynamic
    else endLongTradeStrict
  let endShortTrade :=
    if settings.useDynamicExits && isDynamicExitValid then endShortTradeDynamic
    else endShortTradeStrict

  -- 10. Trade accounting and stream.
  let marketPrice :=
    if settings.useWorstCase then src
    else (bar.high + bar.low + bar.open_ + bar.open_) / 4.0
  let (backtestState, stats) :=
    state.backtestState.step marketPrice startLongTrade endLongTrade startShortTrade
      endShortTrade isEarlySignalFlip bar.barIndex maxBarsBackIndex
  let stream := backtestStream startLongTrade endLongTrade startShortTrade endShortTrade
  let stopBuy := endLongTrade && settings.showExits
  let stopSell := endShortTrade && settings.showExits

  -- 11. Display surface.
  let kernelBullish :=
    if settings.useKernelSmoothing then ks.isBullishSmooth else ks.isBullishRate
  let kernelPlotColor :=
    if settings.showKernelEstimate then
      if kernelBullish then Display.pineColor Display.green 20
      else Display.pineColor Display.red 20
    else
      Display.transparentColor
  let predictionColor :=
    if prediction > 0 then
      Display.predictionGreen (Float.ofInt prediction) settings.useConfidenceGradient
    else if prediction < 0 then
      Display.predictionRed (Float.ofInt (-prediction)) settings.useConfidenceGradient
    else
      Display.neutralColor
  let predictionLabelColor := if settings.showBarPredictions then predictionColor else ""
  let barColor :=
    if settings.showBarColors then
      Display.colorWithTransparency predictionColor
        (if settings.useConfidenceGradient then 50 else 30)
    else
      ""
  let predictionLabelY : PSFloat :=
    if settings.useAtrOffset then
      recentAtr.map fun atr =>
        if prediction > 0 then bar.high + atr else bar.low - atr
    else
      let hl2 := (bar.high + bar.low) / 2.0
      some (if prediction > 0 then
              bar.high + hl2 * settings.barPredictionsOffset / 20.0
            else
              bar.low - hl2 * settings.barPredictionsOffset / 30.0)

  ({ featureEngine := featureEngine
   , yTrainArray := yTrainArray
   , annDistances := annResult.distances
   , annPredictions := annResult.predictions
   , signalState :=
     { signal := newSignal
     , barsHeld := barsHeld
     , signalHistory := signalHistory
     , startLongTradeHistory := startLongTradeHistory
     , startShortTradeHistory := startShortTradeHistory
     , prevSignalChange := signalChange
     , prevSignalChange1 := state.signalState.prevSignalChange
     , prevSignalChange2 := state.signalState.prevSignalChange1
     , barsSinceStartLong := barsSinceStartLong
     , barsSinceStartShort := barsSinceStartShort
     , barsSinceAlertBull := barsSinceAlertBull
     , barsSinceAlertBear := barsSinceAlertBear
     , prevValidLongExit := isValidLongExit
     , prevValidShortExit := isValidShortExit }
   , regimeState := regimeState
   , emaFilterState := emaFilterState
   , smaFilterState := smaFilterState
   , kernelState := kernelState
   , backtestState := backtestState
   , volatilityAtr1 := volatilityAtr1
   , volatilityAtr10 := volatilityAtr10
   , adxFilterState := adxFilterState
   , srcHistory := srcHistory
   , prevOhlc4 := bar.ohlc4 },
   { f1 := featureValues.getD 0 none
   , f2 := featureValues.getD 1 none
   , f3 := featureValues.getD 2 none
   , f4 := featureValues.getD 3 none
   , f5 := featureValues.getD 4 none
   , kernel := yhat1
   , prediction := prediction
   , direction := newSignal.toInt
   , buy := startLongTrade
   , sell := startShortTrade
   , stopBuy := stopBuy
   , stopSell := stopSell
   , backtestStream := stream
   , openLongAlert := startLongTrade
   , closeLongAlert := endLongTrade
   , openShortAlert := startShortTrade
   , closeShortAlert := endShortTrade
   , openPositionAlert := startLongTrade || startShortTrade
   , closePositionAlert := endLongTrade || endShortTrade
   , kernelBullishAlert := alertBullish
   , kernelBearishAlert := alertBearish
   , kernelPlotColor := kernelPlotColor
   , predictionLabel := toString prediction
   , predictionLabelY := predictionLabelY
   , predictionLabelColor := predictionLabelColor
   , barColor := barColor
   , tradeStatsVisible := settings.showTradeStats
   , tradeStatsHeader := Display.tradeStatsHeader
   , totalWins := stats.totalWins
   , totalLosses := stats.totalLosses
   , totalEarlySignalFlips := stats.totalEarlySignalFlips
   , totalTrades := stats.totalTrades
   , winLossRatio := stats.winLossRatio
   , tableWLRatio := stats.tableWLRatio
   , winRate := stats.winRate })

/-- Tail of the pipeline run: fold `stepBar` over the remaining bars,
accumulating result rows. Structural recursion keeps the cardinality
invariant (`Properties.processAllBars_length`) a one-line induction. -/
def runBars (settings : Settings) (priceScale : Float) (maxBarsBackIndex : Nat)
    (state : PipelineState) (rows : Array ResultRow) : List Bar → Array ResultRow
  | [] => rows
  | bar :: rest =>
    let (newState, row) := stepBar settings priceScale maxBarsBackIndex state bar
    runBars settings priceScale maxBarsBackIndex newState (rows.push row) rest

/-- Run the full pipeline over a chronologically ordered bar array. -/
def processAllBars (settings : Settings) (priceScale : Float) (bars : Array Bar) :
    Array ResultRow :=
  let lastBarIndex := bars.size - 1
  let maxBarsBackIndex :=
    if lastBarIndex ≥ settings.maxBarsBack then lastBarIndex - settings.maxBarsBack else 0
  runBars settings priceScale maxBarsBackIndex (PipelineState.init settings) #[] bars.toList

end LorentzianClassification
