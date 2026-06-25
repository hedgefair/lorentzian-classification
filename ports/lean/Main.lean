import LorentzianClassification

/-!
# Lorentzian Classification — Lean CLI

CLI contract mirrors the Rust port (`ports/rust/lorentzian-classification-cli`)
so this binary slots into `tests/parity/cross_port_parity.sh` identically:

- `run <input.csv> <output.csv>` — read a TradingView OHLC export, compute the
  full pipeline, write the 40-column result CSV (byte conventions of the
  Python writer: `.8f` OHLC, `.16f` floats with `""` for NaN, `"1"`/`""`
  booleans, `""` for a zero backtest stream, CRLF line endings).
- `parity <pine_export.csv>` — recompute from the export's OHLC and compare
  against its own feature/kernel/signal columns (tolerance `1e-6` default;
  signals exact from `maxBarsBackIndex`).
- Options: `--include-full-history`, `--max-bars-back <N>`,
  `--tolerance <T>` (parity only), `-h`/`--help`, `-V`/`--version`.
-/
set_option autoImplicit false

open LorentzianClassification

/-- Output schema, identical to Python `RESULT_FIELDNAMES` / Rust
`RESULT_FIELDNAMES` (40 columns). -/
def resultFieldnames : Array String := #[
  "time", "open", "high", "low", "close",
  "F1_RSI", "F2_WT", "F3_CCI", "F4_ADX", "F5_RSI9",
  "Kernel Regression Estimate", "Prediction", "Direction",
  "Buy", "Sell", "StopBuy", "StopSell",
  "Backtest Stream",
  "Open Long Alert", "Close Long Alert", "Open Short Alert", "Close Short Alert",
  "Open Position Alert", "Close Position Alert",
  "Kernel Bullish Alert", "Kernel Bearish Alert",
  "Kernel Plot Color", "Prediction Label", "Prediction Label Y",
  "Prediction Label Color", "Bar Color",
  "Trade Stats Visible", "Trade Stats Header",
  "Total Wins", "Total Losses", "Total Early Signal Flips", "Total Trades",
  "Win Loss Ratio", "Table WL Ratio", "Win Rate"]

/-- A quiet NaN for missing-cell parsing. -/
def nanFloat : Float := 0.0 / 0.0

/-- Python `float()`-style special values (`nan`, `inf`, `infinity`,
case-insensitive, optionally signed), accepted by the Python and Rust
readers. -/
def parseSpecialFloat? (s : String) : Option Float :=
  let t := s.trimAscii.toString.toLower
  let (sign, body) :=
    if t.startsWith "-" then (-1.0, t.drop 1)
    else if t.startsWith "+" then (1.0, t.drop 1)
    else (1.0, t)
  if body == "nan" then some nanFloat
  else if body == "inf" || body == "infinity" then some (sign * (1.0 / 0.0))
  else none

/-- Parse a numeric CSV cell like Python `float()` / Rust `f64::parse`. -/
def parseNumericCell? (s : String) : Option Float :=
  match parseFloat? s with
  | some v => some v
  | none => parseSpecialFloat? s

/-- Neutralize spreadsheet formula injection in the only passthrough cell
(CWE-1236), exactly like the Python/Rust writers. -/
def sanitizeText (value : String) : String :=
  match value.toList with
  | c :: _ =>
    if c == '=' || c == '+' || c == '-' || c == '@' || c == '\t' || c == '\r' || c == '\n' then
      "'" ++ value
    else
      value
  | [] => value

/-- Boolean cell: `"1"` / `""`. -/
def flagCell (b : Bool) : String := if b then "1" else ""

/-- Optional-float cell: `""` for `na`/NaN, else `%.16f`. -/
def psCell (x : PSFloat) : String :=
  match x with
  | none => ""
  | some v => formatFloatCell v

/-- Backtest-stream cell: `""` for zero. -/
def streamCell (v : Int) : String := if v == 0 then "" else toString v

/-- Render one result row to its 40 CSV cells. -/
def resultToCells (time : String) (bar : Bar) (row : ResultRow) : Array String := #[
  sanitizeText time,
  formatFixed bar.open_ 8, formatFixed bar.high 8, formatFixed bar.low 8,
  formatFixed bar.close 8,
  psCell row.f1, psCell row.f2, psCell row.f3, psCell row.f4, psCell row.f5,
  formatFloatCell row.kernel,
  toString row.prediction, toString row.direction,
  flagCell row.buy, flagCell row.sell, flagCell row.stopBuy, flagCell row.stopSell,
  streamCell row.backtestStream,
  flagCell row.openLongAlert, flagCell row.closeLongAlert,
  flagCell row.openShortAlert, flagCell row.closeShortAlert,
  flagCell row.openPositionAlert, flagCell row.closePositionAlert,
  flagCell row.kernelBullishAlert, flagCell row.kernelBearishAlert,
  row.kernelPlotColor, row.predictionLabel, psCell row.predictionLabelY,
  row.predictionLabelColor, row.barColor,
  flagCell row.tradeStatsVisible, row.tradeStatsHeader,
  toString row.totalWins, toString row.totalLosses,
  toString row.totalEarlySignalFlips, toString row.totalTrades,
  psCell row.winLossRatio, psCell row.tableWLRatio, psCell row.winRate]

/-- Number of significant fractional digits in a decimal string
(`"0.91825"` → 5, `"300"` → 0, `"1.250"` → 2). -/
def decimalCount (value : String) : Nat :=
  match value.splitOn "." with
  | [_, frac] => (frac.dropEndWhile (· == '0')).toString.length
  | _ => 0

/-- ADX price scale from the raw OHLC cell strings: `10^min(maxDecimals, 18)`,
or `0.0` for all-integer data. -/
def detectPriceScale (ohlcCells : Array (Array String)) : Float := Id.run do
  let mut maxDecimals := 0
  for row in ohlcCells do
    for cell in row do
      maxDecimals := max maxDecimals (decimalCount cell)
  if maxDecimals == 0 then
    return 0.0
  let mut scale := 1.0
  for _ in [0:min maxDecimals 18] do
    scale := scale * 10.0
  return scale

/-- Find a header column case-insensitively (cells trimmed). -/
def columnIndex? (header : Array String) (name : String) : Option Nat :=
  header.findIdx? (fun c => c.trimAscii.toString.toLower == name.toLower)

/-- Find the first column whose trimmed uppercase name starts with `prefix`
(the Python/Rust feature-slot lookup). -/
def columnPrefixIndex? (header : Array String) (pre : String) : Option Nat :=
  header.findIdx? (fun c => c.trimAscii.toString.toUpper.startsWith pre.toUpper)

/-- A parsed input row: passthrough time, bar, and the raw cells. -/
structure InputData where
  times : Array String
  bars : Array Bar
  priceScale : Float
  header : Array String
  records : Array (Array String)

/-- Read a TradingView CSV: required `time/open/high/low/close` columns
(case-insensitive, any order), all-empty rows skipped, price scale detected
from the raw OHLC strings before parsing. -/
def readTradingViewCsv (path : System.FilePath) : IO InputData := do
  let content ← IO.FS.readFile path
  let rows := parseCsv content
  if rows.isEmpty then
    throw (IO.userError "csv is empty (no header row)")
  let header := rows[0]!
  let need (name : String) : IO Nat :=
    match columnIndex? header name with
    | some i => pure i
    | none => throw (IO.userError s!"missing required column: {name}")
  let timeI ← need "time"
  let openI ← need "open"
  let highI ← need "high"
  let lowI ← need "low"
  let closeI ← need "close"
  let mut records : Array (Array String) := #[]
  for row in rows[1:] do
    if row.all (fun c => c.trimAscii.toString.isEmpty) then
      continue
    records := records.push row
  let cell (row : Array String) (i : Nat) : String := (row.getD i "").trimAscii.toString
  let ohlcStrings := records.map fun row =>
    #[cell row openI, cell row highI, cell row lowI, cell row closeI]
  let priceScale := detectPriceScale ohlcStrings
  let mut times : Array String := #[]
  let mut bars : Array Bar := #[]
  for h : idx in [0:records.size] do
    let row := records[idx]
    let parse (i : Nat) (name : String) : IO Float := do
      let v := cell row i
      match parseNumericCell? v with
      | some f => pure f
      | none => throw (IO.userError s!"row {idx + 1}: cannot parse {name} value {repr v} as a number")
    let o ← parse openI "open"
    let h ← parse highI "high"
    let l ← parse lowI "low"
    let c ← parse closeI "close"
    times := times.push (cell row timeI)
    bars := bars.push
      { open_ := o, high := h, low := l, close := c
      , hlc3 := (h + l + c) / 3.0
      , ohlc4 := (o + h + l + c) / 4.0
      , barIndex := idx }
  pure { times := times, bars := bars, priceScale := priceScale
       , header := header, records := records }

/-- `run` subcommand: compute and write the 40-column result CSV. -/
def cmdRun (input output : System.FilePath) (settings : Settings) : IO UInt32 := do
  let data ← readTradingViewCsv input
  let results := processAllBars settings data.priceScale data.bars
  let mut rows : Array (Array String) := #[resultFieldnames]
  for h : i in [0:results.size] do
    rows := rows.push (resultToCells (data.times.getD i "") (data.bars.getD i default) results[i])
  IO.FS.writeFile output (renderCsv rows)
  IO.println s!"wrote {results.size} rows to {output}"
  pure 0

/-- Parse a CSV cell as a float (`NaN` for empty/unparseable, mirroring the
Rust parity reader). -/
def parseFloatCell (s : String) : Float :=
  let t := s.trimAscii.toString
  if t.isEmpty then nanFloat
  else match parseNumericCell? t with
    | some v => v
    | none => nanFloat

/-- Parse a CSV cell as Python `int(float(x or 0))`. -/
def parseIntCell (s : String) : Int :=
  let t := s.trimAscii.toString
  if t.isEmpty then 0
  else match parseFloat? t with
    | some v =>
      let tr := if v >= 0.0 then v.floor else v.ceil
      if tr >= 0.0 then Int.ofNat tr.toUInt64.toNat
      else -Int.ofNat (Float.toUInt64 (-tr)).toNat
    | none => 0

/-- Pine-export boolean cell (`""/"0"/"false"/"na"/"nan"` falsey). -/
def parseBoolCell (s : String) : Bool :=
  let t := s.trimAscii.toString.toLower
  !(t == "" || t == "0" || t == "false" || t == "na" || t == "nan")

/-- `parity` subcommand: recompute from the export's OHLC and compare against
its own columns under the documented contract (features/kernel within
tolerance wherever the export value is present; prediction/direction/
buy/sell/stops exact from `maxBarsBackIndex`). -/
def cmdParity (input : System.FilePath) (settings : Settings) (tolerance : Float) :
    IO UInt32 := do
  let data ← readTradingViewCsv input
  let results := processAllBars settings data.priceScale data.bars
  let header := data.header
  let featureIdx : Array (Option Nat) :=
    #["F1", "F2", "F3", "F4", "F5"].map (columnPrefixIndex? header)
  let kernelIdx := columnIndex? header "Kernel Regression Estimate"
  let predictionIdx := columnIndex? header "Prediction"
  let directionIdx := columnIndex? header "Direction"
  let buyIdx := columnIndex? header "Buy"
  let sellIdx := columnIndex? header "Sell"
  let stopBuyIdx := columnIndex? header "StopBuy"
  let stopSellIdx := columnIndex? header "StopSell"
  let lastBarIndex := data.records.size - 1
  let maxBarsBackIndex :=
    if lastBarIndex ≥ settings.maxBarsBack then lastBarIndex - settings.maxBarsBack else 0
  let mut maxFeatureDiff := 0.0
  let mut maxKernelDiff := 0.0
  let mut compared := 0
  let mut mismatches : Array String := #[]
  for h : i in [0:results.size] do
    let row := results[i]
    let record := data.records.getD i #[]
    let cellAt (idx : Option Nat) : String :=
      match idx with
      | some j => (record.getD j "")
      | none => ""
    -- Features and kernel: every row where the export value is present.
    let psVals : Array PSFloat := #[row.f1, row.f2, row.f3, row.f4, row.f5]
    for k in [0:5] do
      let expected := parseFloatCell (cellAt (featureIdx.getD k none))
      if !expected.isNaN then
        match psVals.getD k none with
        | none =>
          mismatches := mismatches.push s!"[{i}] f{k + 1}: expected {formatFloatCell expected} got na"
        | some actual =>
          let diff := Float.abs (expected - actual)
          maxFeatureDiff := max maxFeatureDiff diff
          if diff > tolerance then
            mismatches := mismatches.push
              s!"[{i}] f{k + 1}: expected {formatFloatCell expected} got {formatFloatCell actual}"
    let expectedKernel := parseFloatCell (cellAt kernelIdx)
    if !expectedKernel.isNaN then
      let diff := Float.abs (expectedKernel - row.kernel)
      maxKernelDiff := max maxKernelDiff diff
      if diff > tolerance then
        mismatches := mismatches.push
          s!"[{i}] kernel: expected {formatFloatCell expectedKernel} got {formatFloatCell row.kernel}"
    -- Signals: exact from maxBarsBackIndex onward.
    if i ≥ maxBarsBackIndex then
      compared := compared + 1
      -- Missing columns parse to 0/false and are still compared, exactly like
      -- the Rust `read_pine_export` defaults.
      let checkInt (name : String) (idx : Option Nat) (actual : Int) : Array String :=
        if parseIntCell (cellAt idx) != actual then
          #[s!"[{i}] {name}: expected {parseIntCell (cellAt idx)} got {actual}"]
        else #[]
      let checkBool (name : String) (idx : Option Nat) (actual : Bool) : Array String :=
        if parseBoolCell (cellAt idx) != actual then
          #[s!"[{i}] {name}: expected {parseBoolCell (cellAt idx)} got {actual}"]
        else #[]
      mismatches := mismatches
        ++ checkInt "prediction" predictionIdx row.prediction
        ++ checkInt "direction" directionIdx row.direction
        ++ checkBool "buy" buyIdx row.buy
        ++ checkBool "sell" sellIdx row.sell
        ++ checkBool "stop_buy" stopBuyIdx row.stopBuy
        ++ checkBool "stop_sell" stopSellIdx row.stopSell
  IO.println s!"rows: {results.size}"
  IO.println s!"compared from index: {maxBarsBackIndex}"
  IO.println s!"compared rows: {compared}"
  IO.println s!"max feature diff: {formatFloatCell maxFeatureDiff}"
  IO.println s!"max kernel diff:  {formatFloatCell maxKernelDiff}"
  if mismatches.isEmpty then
    IO.println s!"PARITY: PASS (tolerance {tolerance})"
    pure 0
  else
    IO.println s!"PARITY: FAIL ({mismatches.size} mismatches)"
    for m in mismatches.toSubarray 0 (min mismatches.size 10) do
      IO.println s!"  {m}"
    pure 1

/-- CLI usage text. -/
def usage : String :=
  "Usage: lorentzian-classification <command> [options]\n\n" ++
  "Commands:\n" ++
  "  run <input.csv> <output.csv>   compute and write the 40-column result CSV\n" ++
  "  parity <pine_export.csv>       compare recomputed results to the export's columns\n\n" ++
  "Options:\n" ++
  "  --include-full-history         scan the ANN from training index 0\n" ++
  "  --max-bars-back <N>            training window (default 2000)\n" ++
  "  --tolerance <T>                parity tolerance (default 1e-6; parity only)\n" ++
  "  -h, --help                     print this help\n" ++
  "  -V, --version                  print the version"

def version : String := "lorentzian-classification-lean 0.1.0"

/-- Entry point. -/
def main (args : List String) : IO UInt32 := do
  let mut positionals : List String := []
  let mut includeFullHistory := false
  let mut maxBarsBack : Nat := 2000
  let mut tolerance : Float := 0.000001
  let mut rest := args
  while !rest.isEmpty do
    match rest with
    | [] => pure ()
    | arg :: tail =>
      rest := tail
      match arg with
      | "-h" | "--help" | "help" =>
        IO.println usage
        return 0
      | "-V" | "--version" =>
        IO.println version
        return 0
      | "--include-full-history" =>
        includeFullHistory := true
      | "--max-bars-back" =>
        match rest with
        | v :: tail2 =>
          match v.trimAscii.toString.toNat? with
          | some n => maxBarsBack := n; rest := tail2
          | none =>
            IO.eprintln s!"error: invalid --max-bars-back value: {v}"
            return 1
        | [] =>
          IO.eprintln "error: --max-bars-back requires a value"
          return 1
      | "--tolerance" =>
        match rest with
        | v :: tail2 =>
          match parseFloat? v.trimAscii.toString with
          | some t => tolerance := t; rest := tail2
          | none =>
            IO.eprintln s!"error: invalid --tolerance value: {v}"
            return 1
        | [] =>
          IO.eprintln "error: --tolerance requires a value"
          return 1
      | _ =>
        if arg.startsWith "--" then
          IO.eprintln s!"error: unknown option: {arg}"
          return 1
        positionals := positionals ++ [arg]
  let settings : Settings :=
    { includeFullHistory := includeFullHistory, maxBarsBack := maxBarsBack }
  match positionals with
  | ["run", input, output] => cmdRun input output settings
  | ["parity", input] => cmdParity input settings tolerance
  | [] =>
    IO.eprintln usage
    pure 1
  | cmd :: _ =>
    IO.eprintln s!"error: unknown or malformed command: {cmd}"
    IO.eprintln usage
    pure 1
