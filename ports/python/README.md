# Python Port

Python implementation of the original Lorentzian Classification indicator,
packaged as both a library and a CLI. It mirrors the PineScript reference's
business logic:

- Feature engineering: RSI, WaveTrend, CCI, ADX.
- Greedy Lorentzian approximate-nearest-neighbor (ANN) classifier.
- Volatility, regime, ADX, EMA, SMA, and kernel filters.
- Prediction, direction, buy, sell, and exit signal generation.
- Pine-compatible individual/combined alert booleans, hidden backtest stream,
  and cumulative trade stats.
- Pine display-derived label, bar color, kernel color, and trade-stat table
  fields as deterministic CLI data.
- Parity comparison against TradingView/PineScript export CSVs.

## Acknowledgement

The community [`advanced-ta`](https://pypi.org/project/advanced-ta/) Python
package by Loki Arya helped demonstrate early demand for a Python interface to
Lorentzian Classification and inspired the library-facing ergonomics in this
port. This implementation keeps its own parity-tested calculation path and
validates behavior against the fixtures in this repository.

## Quick Start

Run the package from this folder, or set `PYTHONPATH=ports/python` from the
repository root:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification --version
```

### Library API

```python
from lorentzian_classification import LorentzianClassification

model = LorentzianClassification("path/to/tradingview_export.csv")
latest = model.latest
print(latest.prediction, latest.direction, latest.buy, latest.sell)
model.to_csv("/tmp/lorentzian_python_output.csv")
```

The wrapper accepts a TradingView CSV path, `Bar`/`TvRow` iterables, mapping
records, or a DataFrame-like object with `to_dict("records")`. DataFrame
support is intentionally dependency-light: install the optional
`lorentzian-classification[dataframe]` extra only if you need
`model.to_dataframe()`.

### CLI

```bash
# Compute the full result series from an OHLC CSV.
PYTHONPATH=ports/python python3 -m lorentzian_classification run \
  path/to/tradingview_export.csv \
  --output /tmp/lorentzian_python_output.csv

# Compare Python output against a TradingView/Pine export's own columns.
PYTHONPATH=ports/python python3 -m lorentzian_classification parity \
  path/to/tradingview_export.csv
```

`run` requires a non-empty OHLC CSV with unique `time/open/high/low/close`
headers before it writes an output artifact. Missing input files and directory
paths are reported as CLI validation errors rather than Python tracebacks.
Output paths must point to files inside existing directories.

## Command Reference

The commands most users need:

| Command | Purpose |
| --- | --- |
| `run` | Compute the full result series from an OHLC CSV. |
| `parity` | Recompute from a Pine export and compare against its own columns. |
| `validate-fixtures` | Run the registered Pine export fixture suite (add `--require-full-coverage` for the strict gate). |

Most commands accept `--json` for machine-readable output.

The port also ships maintainer-only commands for release readiness, Pine-export
generation, and the external reference workspace: `audit-fixtures`,
`export-checklist`, `pine-export-helper`, `export-pack` / `verify-export-pack`,
`import-pine-exports`, `readiness` / `readiness-blockers`,
`pine-input-contract` / `pine-output-contract`, `external-report-checklist`,
`external-runner-pack` / `verify-external-runner-pack`, and
`prepare-readiness-artifacts` / `verify-readiness-artifacts`.

For release/maintenance workflows, see [MAINTAINERS.md](MAINTAINERS.md).

## Validating Fixtures

Run the full known Pine export fixture suite. The fixture directory is resolved
in this order: explicit `--fixture-dir`, then `LORENTZIAN_PARITY_FIXTURE_DIR`,
then the repo-local baselines under `tests/parity/baselines/`. (An external
reference workspace can also be wired in through `LORENTZIAN_external_ROOT`; see
[MAINTAINERS.md](MAINTAINERS.md) for that path and the rest of the release-time
fixture machinery.)

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification validate-fixtures
```

Add `--json` to emit the validation results, per-fixture parity summaries, and
strict-coverage status as machine-readable JSON.

Run the portable repo-local standard baseline suite with:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification validate-fixtures \
  --fixture-dir tests/parity/baselines \
  --manifest tests/parity/baselines/baselines_manifest.json
```

Those baselines are TradingView/Pine exports committed under
`tests/parity/baselines/`. They exclude TDANN/downsampling exports because the
standard Python port mirrors the original Pine neighbor cadence.

Use `--include-full-history` for exports generated with PineScript's
`Include Full History` setting enabled.

## Settings Flags

The CLI exposes the indicator settings used by the business logic: neighbors,
max bars back, feature count, filter toggles, kernel parameters, and feature
definitions such as `--f1 RSI:14:1` or `--f2 WT:10:11`.

## Validation

From the repository root:

```bash
PYTHONPATH=ports/python python3 -m unittest tests.parity.test_python_port
```

The current validation set checks:

- `pine_oanda_eurusd_1d_full_history.csv` with full history enabled
- `pine_tastyfx_eurusd_1d_full_history.csv` with full history enabled
- `pine_coinbase_btcusd_1d_limited_history.csv` with full history disabled
- `pine_btcusd_h1_trimmed_limited_history.csv` with full history disabled

The first three exports validate feature values, kernel values, predictions,
directions, buy signals, sell signals, and default-hidden stop signal plots.
The BTC H1 trimmed-window export validates kernel values and downstream signal
parity for the warmup-sensitive case.

The tests also exercise the CLI output artifact and confirm it includes the
Pine alert, backtest stream, and trade-stat columns, including source-backed
coverage for every `lcv6.pine` `alertcondition`, plot/shape, display label,
bar color, backtest stream, and trade-stat table visibility/header/ratio
field. They compare the CLI defaults against the pinned `lcv6.pine` reference
source and assert that every Pine `input.*` declaration maps to a Python
`Settings` field. They further assert that the Python core still covers the
canonical Pine business-logic surfaces from `lcv6.pine`, `MLExtensions.pine`,
and `KernelFunctions.pine`.

The suite also executes every required non-default manifest setting against
its representative `python_smoke_fixture`, so planned settings such as dynamic
exits, filters, alternate source, custom features, alternate kernel
parameters, and worst-case mode are known to run end-to-end through the Python
calculation path before their Pine export fixtures exist.

If an instrumented Pine export includes alert, display-derived, or trade-stat
columns, the parity command compares those fields directly. Current default
exports do not include all of those optional columns.

See [`tests/parity/python_port_coverage.md`](../../tests/parity/python_port_coverage.md)
for the exact coverage matrix and the remaining Pine export fixtures needed to
prove every non-default setting.
