import LorentzianClassification.Basic
import LorentzianClassification.Numeric
import LorentzianClassification.TimeSeries
import LorentzianClassification.Normalize
import LorentzianClassification.Indicators.Window
import LorentzianClassification.Indicators.EMA
import LorentzianClassification.Indicators.SMA
import LorentzianClassification.Indicators.RMA
import LorentzianClassification.Indicators.RSI
import LorentzianClassification.Indicators.CCI
import LorentzianClassification.Indicators.ADX
import LorentzianClassification.Indicators.WaveTrend
import LorentzianClassification.Indicators.ATR
import LorentzianClassification.Kernels
import LorentzianClassification.Features
import LorentzianClassification.Distance
import LorentzianClassification.ANN
import LorentzianClassification.Labels
import LorentzianClassification.Filters
import LorentzianClassification.KernelFilters
import LorentzianClassification.Signals
import LorentzianClassification.Backtest
import LorentzianClassification.Display
import LorentzianClassification.Pipeline
import LorentzianClassification.Csv
import LorentzianClassification.Properties.ANN
import LorentzianClassification.Properties.Pipeline
import LorentzianClassification.Properties.Deferred

/-!
# Lorentzian Classification — Lean 4 reference specification

Root import aggregator for the `LorentzianClassification` library: an
executable Lean 4 specification of the "Machine Learning: Lorentzian
Classification" indicator, validated for parity against the Python and Rust
ports and the TradingView/Pine gold baselines in `tests/parity/baselines/`.

The Python port (`ports/python/`) is the parity authority for the full
40-column output schema; deliberate divergences between this spec, the
PineScript original, and the Python reference are documented per module and
summarized in `ports/lean/README.md`.
-/
