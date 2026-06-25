# Validation

Every port documents how it was checked against the reference behavior before
release. This page records the validation policy, the current per-port results,
and the commands to reproduce them.

## What Every Validated Port Records

- Reference implementation and revision.
- Port implementation and revision.
- Symbols, timeframes, and date ranges used.
- Input settings used for the run.
- Output fields compared.
- Known platform differences.
- Reviewer and review date.

## What "Parity" Means

Parity does not always mean byte-for-byte identical output. Platforms differ in
data feeds, numerical precision, bar construction, and execution timing. When
exact parity is not possible, the difference is documented and explained, not
papered over.

The shared per-column contract used by all ports against the TradingView gold
exports in `tests/parity/baselines/`:

| Columns | Tolerance |
| --- | --- |
| Features (`F1_*` … `F5_*`), kernel estimate | `1e-6` |
| Prediction, direction | exact (integer) |
| Buy, sell, stop-buy, stop-sell | exact (boolean) |

Useful validation artifacts: exported signal series, the settings files used
for the comparison, and summary tables of mismatches. Screenshots only when
they clarify behavior that the raw data cannot.

## Python Port

Python parity artifacts have been added under `tests/parity/`. Current
validation covers:

- Manifest-pinned PineScript reference files (SHA-256 locked).
- Tracked TradingView/Pine export CSVs under `tests/parity/baselines/`.
- Zero-mismatch comparisons for feature/kernel values, predictions, direction,
  buy/sell signals, stop signals, and backtest stream when those fields are
  exported.
- Optional alert, display, and trade-stat output comparisons when instrumented
  columns are present.
- Smoke execution of every required non-default Python settings combination
  planned for future Pine exports.
- Strict required-export admission checks via a manifest-specific
  `Settings Fingerprint` column, so planned CSVs cannot pass with mismatched
  TradingView inputs.

The Python port is still **not release-ready**: strict coverage requires the
remaining non-default TradingView/Pine export CSVs listed in
[`tests/parity/python_port_coverage.md`](../tests/parity/python_port_coverage.md).

## Rust Port

The Rust port (`ports/rust/`) is validated against the same gold baselines
under the same per-column contract. Its parity integration test
(`lorentzian-classification-core/tests/parity.rs`) recomputes each baseline
from OHLC and compares against the export's feature/kernel/prediction/signal
columns. Because the Python port is already proven equal to those Pine exports,
passing here establishes **Rust == Pine == Python** transitively.

```bash
cd ports/rust
cargo test --release --workspace                   # parity + unit tests
cargo test --release --test parity -- --ignored    # 47k-bar BINANCE H1 baseline
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all --check
```

Verified results (tolerance `1e-6`): OANDA daily (6216 bars), TASTYFX daily
(7988 bars), and COINBASE daily (4155 bars) reproduce exactly; the 47,246-bar
BINANCE H1 intraday baseline reproduces exactly at `include_full_history=false`.

## Lean Port

The Lean port (`ports/lean/`) is the executable formal specification. It is
validated three ways against the same gold baselines:

1. **Self-contained parity** (`lorentzian-classification parity <baseline>`):
   recomputes from the export's OHLC and compares the export's own
   feature/kernel columns within `1e-6` and prediction/direction/buy/sell/
   stop columns exactly from `max_bars_back_index`; the same contract as the
   Rust parity test. All four baselines PASS.
2. **Full-schema cross-port** (`tests/parity/cross_port_parity.sh`): the Lean
   output is **byte-identical to the Rust port** on all four baselines and
   matches the Python port within `4.8e-14` (tolerance `1e-9`) across all 40
   columns.
3. **Theorem-named property tests** (`lake test`, 46 checks): every proved
   invariant in `LorentzianClassification/Properties/` (including the
   ℚ-model normalization/kernel bounds) and the one deferred Float-level
   statement are exercised executably, including the ANN ratchet/FIFO across
   hundreds of iterations.

Reference: the PineScript source in `ports/pinescript/` (algorithmic ground
truth) and the Python port (parity authority for the output schema), at the
revision of this commit. Settings: library defaults (the CLI exposes
`--include-full-history` and `--max-bars-back`, mirroring the Rust CLI).
Known platform differences and deliberate Python-over-Pine semantics are
documented in [`ports/lean/README.md`](../ports/lean/README.md)
("Documented deviations"); proof debt is tracked there as a
stated/proven/extracted-to-tests table.

```bash
cd ports/lean
lake build && lake test
.lake/build/bin/lorentzian-classification parity \
  ../../tests/parity/baselines/pine_coinbase_btcusd_1d_limited_history.csv
```

## Cross-Port Parity

`tests/parity/cross_port_parity.sh` runs the Rust, Python, and Lean CLIs on each
baseline and diffs their outputs pairwise with `tests/parity/compare_csv.py`, an
implementation-independent comparator. This proves the three ports agree
across the full 40-column result schema, including backtest stream, alerts,
colors, and trade-stat columns that the Pine exports do not contain.

Latest run: all four baselines match on all three pairs; Rust↔Python and
Lean↔Python differ by at most `~5e-14` (floating-point round-off; the parity
contract allows `1e-6`). Lean↔Rust byte-identity was verified separately with
`cmp` on the baseline outputs (the harness itself compares numerically at
tolerance, not byte-for-byte).

## Related Documentation

- Per-port usage and design notes live next to each port:
  [`ports/python/README.md`](../ports/python/README.md),
  [`ports/rust/README.md`](../ports/rust/README.md),
  [`ports/lean/README.md`](../ports/lean/README.md),
  [`ports/pinescript/README.md`](../ports/pinescript/README.md).
- The parity suite, cross-port harness, gold baselines, and coverage matrix are
  documented under [`tests/parity/`](../tests/parity/README.md).
- Copy-pasteable, cross-port usage workflows live in
  [`examples.md`](examples.md).
- The complete settings reference is hosted at
  [ai-edge.io/docs](https://ai-edge.io/docs/indicators/lorentzian-classification/general-settings).
