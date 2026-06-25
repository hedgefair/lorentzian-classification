# Python Port Parity Coverage

The authoritative parity-coverage record for the Python CLI port. Fixtures are
read from the repo-local baselines under `tests/parity/baselines/` by default;
point the CLI at another directory with `--fixture-dir`, or at a local
external workspace with `LORENTZIAN_external_ROOT` (its
`Files/` directory is searched first and the root second, so root-level Pine
exports can be added to the manifest without being silently ignored).

The CLI commands referenced below (`validate-fixtures`, `audit-fixtures`,
`export-checklist`, `pine-export-helper`, `export-pack`, `readiness`, and
friends) are documented in full in
[`ports/python/README.md`](../../ports/python/README.md); the manifest and
source-lock rules are documented in [`README.md`](README.md).

## Covered By Pine Exports

The unittest suite validates these PineScript/TradingView export files:

| Fixture | Include full history | Feature values | Kernel | Prediction | Direction | Buy/Sell | Stop plots | Backtest stream |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `pine_oanda_eurusd_1d_full_history.csv` | yes | yes | yes | yes | yes | yes | yes | when exported |
| `pine_tastyfx_eurusd_1d_full_history.csv` | yes | yes | yes | yes | yes | yes | yes | when exported |
| `pine_coinbase_btcusd_1d_limited_history.csv` | no | yes | yes | yes | yes | yes | yes | when exported |
| `pine_btcusd_h1_trimmed_limited_history.csv` | no | no | yes | yes | yes | yes | yes | when exported |

For the available exports, the Python parity command requires zero mismatches
for prediction, direction, buy, sell, stop-buy, stop-sell, and backtest stream
columns. Feature and kernel numeric columns must stay within the configured
tolerance.

When a Pine export includes instrumented CLI-output columns, the parity
command also compares individual long/short open/close alerts, combined
open/close position alerts, kernel bullish/bearish alerts, kernel plot color,
prediction label text/Y/color, bar color, trade-stat visibility/header,
cumulative wins/losses/early flips/trades, exported W/L ratio, table-rendered
W/L ratio, and win rate. Those optional columns are ignored when absent so
current TradingView exports remain usable.

Run the full current fixture gate with:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification validate-fixtures
```

Add `--json` for per-fixture parity summaries and the aggregate pass/fail
state as structured data. The command intentionally uses only the known
PineScript/TradingView exports listed above; external-generated comparison
CSVs in the same workspace are ignored unless explicitly listed in the
manifest, so the validation source remains Pine exports rather than derivative
artifacts.

Fixture metadata is manifest-driven from `fixtures_manifest.json`. Each entry
can define the exact Pine settings used to produce the export (feature
definitions, source, filters, kernel parameters, exit mode, and trade-stat
mode), so non-default Pine exports are addable without changing Python code.

## The Strict Coverage Gate

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification validate-fixtures --require-full-coverage
```

The strict gate validates required non-default cases from
`fixtures_manifest.json` when their CSV files exist, and fails while any
required Pine export is absent or mismatching. For each uncovered case it
emits structured action items: the Pine export filename to create, the exact
indicator settings, the manifest proof intent, TradingView input labels,
equivalent Python CLI flags, and the expected CSV columns (including the
strict `Settings Fingerprint` column that rejects CSVs exported with
mismatched TradingView inputs). Pine string/color display fields are exported
as deterministic numeric helper codes, decoded back to canonical display
values by the Python reader, and counted as required parity columns.

The strict gate also runs each required non-default Python settings
combination against the manifest's representative `python_smoke_fixture`.
This confirms the Python implementation path executes and produces internally
valid rows (aligned bar times, bounded predictions, valid direction/backtest
stream enum values, finite kernel output, and finite active feature output),
but does **not** count as Pine parity for the missing export.

Generating the missing exports is a tooled workflow: `export-checklist` lists
what to create, `pine-export-helper` emits the exact Pine plot lines,
`export-pack` writes the full SHA-256-traced handoff pack (with
`acceptance_manifest.csv` for spreadsheet-style tracking), and
`verify-export-pack` validates a pack before use. `readiness` /
`readiness-blockers --json` roll everything into a single release-gate
report with the exact next-action commands, and
`external-runner-pack` / `prepare-readiness-artifacts` produce the
external-side refresh artifacts. See
[`ports/python/README.md`](../../ports/python/README.md) for each command.

## Covered By CLI Tests

The tests verify that the command-line artifact includes:

- `Backtest Stream`
- individual long/short open/close alert booleans
- open/close position alert booleans
- kernel bullish/bearish alert booleans
- cumulative wins, losses, early signal flips, trades, exported/table W/L
  ratios, and win rate
- trade-stat visibility gate and header text
- optional Pine-exported alert and trade-stat columns, parsed and compared
- every `lcv6.pine` `alertcondition` with a corresponding CLI output column
- every `lcv6.pine` plot/shape, display label, bar color, backtest stream, and
  trade-stat table surface with a corresponding CLI output column

The CLI default settings are locked to the PineScript `lcv6.pine` defaults,
including feature definitions, filters, kernel parameters, and the Pine
minimum feature count of 2. The test suite reads the pinned `lcv6.pine`
reference source, compares those Pine defaults against the CLI defaults, and
parses every Pine `input.*` declaration to ensure each one has a corresponding
Python `Settings` field and CLI flag path, so a Pine default drift is caught
directly.

The same source-level contracts are exposed as standalone commands and strict
readiness summary flags:

| Contract | Standalone command | Readiness flag |
| --- | --- | --- |
| Pine inputs/defaults ↔ Python `Settings`/CLI | `pine-input-contract --json` | `pine_input_contract_failed` |
| Pine plots/shapes/alerts/labels/colors/stream/trade-stats ↔ CLI output columns | `pine-output-contract --json` | `pine_output_contract_failed` |
| External indicator `input` defaults ↔ Python/Pine settings contract | (part of `readiness`) | `external_indicator_input_contract_failed` |
| External parity script exposes and uses `InpInputFile`/`InpOutputFile`/`InpIncludeFullHist` and emits the expected comparison CSV header | (part of `readiness`) | `external_parity_script_contract_failed` |

The suite also reads `lcv6.pine`, `MLExtensions.pine`, and
`KernelFunctions.pine` from the same workspace and checks that the Python core
still contains corresponding implementations for the major Pine business-logic
surfaces: normalized feature functions, volatility/regime/ADX filters, kernel
functions, Lorentzian distance, ANN prediction, entries, strict and dynamic
exits, backtest stream, and trade-stat outputs.

It additionally exercises every required non-default settings case against its
representative `python_smoke_fixture`, so planned cases are known to execute
through the Python business logic and satisfy internal output invariants even
before their Pine parity exports exist.

## Not Yet Proven By Pine Exports

The current fixture directory does not contain Pine exports for every
non-default setting combination. Additional TradingView/Pine exports are still
needed to prove full parity for:

| Required export | Settings purpose |
| --- | --- |
| `pine_coinbase_btcusd_1d_show_exits.csv` | `show_exits=true`, visible StopBuy/StopSell plot parity |
| `pine_coinbase_btcusd_1d_dynamic_exits.csv` | `use_dynamic_exits=true`, dynamic exit stream parity |
| `pine_oanda_eurusd_1d_adx_filter.csv` | `include_full_history=true`, `use_adx_filter=true`, ADX threshold parity |
| `pine_oanda_eurusd_1d_ema_filter.csv` | `include_full_history=true`, `use_ema_filter=true`, EMA trend filter parity |
| `pine_oanda_eurusd_1d_sma_filter.csv` | `include_full_history=true`, `use_sma_filter=true`, SMA trend filter parity |
| `pine_oanda_eurusd_1d_kernel_smoothing.csv` | `include_full_history=true`, `use_kernel_smoothing=true`, crossover alert parity |
| `pine_coinbase_btcusd_1d_hlc3_source.csv` | `source=hlc3`, alternate source parity |
| `pine_coinbase_btcusd_1d_custom_features_count3.csv` | `feature_count=3`, custom feature definitions |
| `pine_oanda_eurusd_1d_kernel_params.csv` | alternate `kernel_h`, `kernel_r`, `kernel_x`, and `kernel_lag` |
| `pine_coinbase_btcusd_1d_worst_case.csv` | `use_worst_case=true`, trade-stat mode parity |
| `pine_coinbase_btcusd_1d_hide_trade_stats.csv` | `show_trade_stats=false`, trade-stat table display gate parity |

The Python CLI implements all of these settings, and the test suite confirms
each planned combination executes against a representative existing Pine
fixture. The default/original behavior is covered by the current exports.
Full completion still requires adding Pine exports for the non-default cases
above or another authoritative Pine-generated fixture set.
