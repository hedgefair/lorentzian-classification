# Rust Port

A bit-faithful Rust port of the Lorentzian Classification indicator, structured
as a Cargo workspace:

| Crate | Purpose |
| --- | --- |
| `lorentzian-classification-core` | The algorithm as a dependency-free library. |
| `lorentzian-classification-cli` | A thin command-line front end (`run`, `parity`). |

The port is a statement-for-statement translation of the PineScript v6 reference
and the parity-tested Python port (`ports/python`). Series are forward-indexed
(index `0` = oldest bar) and Pine `na` is represented as `f64::NAN`.

## Status

**Implemented and parity-verified.** The port reproduces the TradingView/Pine
gold exports in `tests/parity/baselines/` exactly, under the same contract used
for the Python port (`1e-6` for features/kernel, exact for
prediction/direction/buy/sell/stops). See [Validation](#validation).

## Design

- **Small dependency surface.** The numeric core is dependency-light, and CSV
  parsing uses the established `csv` crate so quoted fields behave like
  Python's standard `csv` parser instead of relying on ad hoc string splitting.
- **`#![forbid(unsafe_code)]`** and `#![warn(clippy::pedantic)]` at the crate
  root; the workspace passes `clippy -D warnings` and `rustfmt --check`.
- **Bit-exact floating point.** Banker's rounding (`f64::round_ties_even`) is
  used for the ADX price-scale quantization to match Python's `round`, and
  `powf`/`exp` match libm, so the two ports agree to the last bit.
- **Typed configuration.** [`Settings`], [`Source`], and [`FeatureSpec`] encode
  the Pine inputs; `Settings::default()` equals the Pine defaults.

Modules: `types`, `indicators` (RSI/WT/CCI/ADX, SMA/EMA/RMA), `kernels`
(Rational Quadratic, Gaussian), `filters` (regime/KLMF), `ann` (the Lorentzian
approximate-nearest-neighbor scan), `engine` (`calculate`), `display` (Pine
colors), `csv_io`, and `parity`.

## Library usage

```rust
use lorentzian_classification_core::{calculate, read_tradingview_csv, Settings};

let (bars, price_scale) = read_tradingview_csv("data.csv".as_ref())?;
let rows = calculate(&bars, &Settings::default(), price_scale);
println!("last prediction: {}", rows.last().unwrap().prediction);
# Ok::<(), lorentzian_classification_core::CsvError>(())
```

## CLI usage

```bash
# Compute the full 40-column result series.
cargo run --release -p lorentzian-classification-cli -- \
  run "input.csv" "output.csv" --include-full-history

# Recompute from a Pine export and compare against its own columns.
cargo run --release -p lorentzian-classification-cli -- \
  parity "tests/parity/baselines/pine_oanda_eurusd_1d_full_history.csv" \
  --include-full-history --tolerance 1e-6
```

## Validation

```bash
cd ports/rust

cargo test --release --workspace          # parity + unit tests
cargo test --release --test parity -- --ignored   # the 47k-bar BINANCE H1 baseline
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all --check
cargo bench                               # criterion benchmarks for `calculate`
```

The parity integration test (`lorentzian-classification-core/tests/parity.rs`)
runs the Rust port against the committed gold baselines. Because the Python port
is already proven equal to those same Pine exports, passing here establishes
**Rust == Pine == Python** transitively.

For an end-to-end cross-port check across the full output schema (including
columns the Pine exports omit: backtest stream, alerts, colors, trade stats),
run the repository harness:

```bash
tests/parity/cross_port_parity.sh
```

It runs both the Rust and Python CLIs on each baseline and diffs the outputs with
an implementation-independent comparator.
