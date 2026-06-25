# Parity Fixtures

Parity tests for the Python port run against TradingView/PineScript export
CSVs. Two fixture sources are supported:

- **Repo-local baselines** under [`baselines/`](baselines/README.md), the
  portable, committed gold baselines (the TradingView CSV exports we treat as
  ground truth) used by default.
- **A local external runtime workspace** (for example
  `/path/to/external-runtime/external`) when available. The CLI accepts either
  the workspace root or a direct fixture directory; given the workspace root,
  it searches `Files/` first and then the root itself, so root-level Pine
  exports can be added to `fixtures_manifest.json` without moving them.

Lookup precedence: `--fixture-dir` beats everything; otherwise
`LORENTZIAN_PARITY_FIXTURE_DIR` when set; otherwise the local external runtime
workspace pointed to by `LORENTZIAN_external_ROOT`.

## Tracked Exports

- `pine_oanda_eurusd_1d_full_history.csv`
- `pine_tastyfx_eurusd_1d_full_history.csv`
- `pine_coinbase_btcusd_1d_limited_history.csv`
- `pine_btcusd_h1_trimmed_limited_history.csv`

Run the portable repo-local baseline suite with:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification validate-fixtures \
  --fixture-dir tests/parity/baselines \
  --manifest tests/parity/baselines/baselines_manifest.json
```

The committed baselines exclude TDANN/downsampling exports that the standard
Python port does not implement. Use `validate-fixtures --json` when release
tooling needs machine-readable per-fixture parity summaries and
strict-coverage status.

## Cross-Port Parity Harness

Two stdlib-only helpers live alongside the baselines and prove the ports agree
with each other across the full 40-column result schema (backtest stream,
alerts, colors, and trade stats that the Pine exports do not contain):

| Script | Purpose |
| --- | --- |
| `cross_port_parity.sh` | Build the Rust and Lean CLIs, run the Rust, Python, and Lean ports on each gold baseline, and diff their full-schema outputs pairwise (Rust↔Python, Lean↔Python, Lean↔Rust). Exit status is non-zero on any mismatch. |
| `compare_csv.py` | Generic CSV comparator (numeric tolerance + exact cells). Used by `cross_port_parity.sh`; depends on no port, so the comparison is not circular. |

```bash
tests/parity/cross_port_parity.sh     # PYTHON=python3 TOLERANCE=1e-9 by default
```

It runs from the repository root, building the Rust CLI (release) and the Lean
CLI, then for each baseline runs all three ports' `run` command and compares
the resulting CSVs cell-by-cell across the full schema. Diff any two output
CSVs directly with `python3 tests/parity/compare_csv.py a.csv b.csv --tolerance 1e-9`.

## The Manifest Is the Source of Truth

`fixtures_manifest.json` defines the tracked fixture list, the Pine settings
associated with each export, and the required non-default Pine exports that
are not available yet. Add future non-default Pine exports there instead of
hardcoding new cases in the CLI.

Each planned case must be an object with a target CSV filename, the Pine
settings to use, a representative `python_smoke_fixture`, and the behavior it
proves. Strict coverage output includes those manifest details when an export
is missing, along with equivalent Python CLI flags and the expected export
column schema.

The manifest must be unambiguous: duplicate fixture names, filenames, Pine
source names/paths, external source names/paths, case-only filename/path
collisions, tracked-vs-required-or-ignored filename overlap, external parity
report filename overlap, ambiguous required-case name/filename selectors, and
unsafe smoke fixture paths are all rejected.

## Source Locks

`validate-fixtures` enforces the manifest-pinned Pine and external source
locks when a manifest is available:

- A missing source, SHA-256 drift, or disallowed debug marker fails validation
  before the fixture set can be treated as release-ready. Disallowed debug
  markers include debug naming plus common runtime logging or display calls
  such as `Print`, `PrintFormat`, `Comment`, `Alert`, and `console.log`.
- Pine source entries may also pin `code_sha256`, so comment-only or
  blank-line-only drift stays visible but does not fail the business-logic
  parity gate.

`readiness` adds source-level contract gates on top:

- It fails if the pinned `lcv6.pine` input/default contract drifts away from
  the Python `Settings` and CLI surface (`pine-input-contract --json` runs
  just this check).
- It fails if the pinned Pine plots, signal shapes, alerts, labels, colors,
  backtest stream, or trade-stat table fields drift away from the CLI output
  surface (`pine-output-contract --json` runs just this check).
- It source-checks the pinned external indicator `input` defaults against the
  same Python/Pine settings contract, and fails on known non-parity patterns
  such as incremental ANN replay through `prev_calculated - 1`.
- It fails when a required external parity report is missing, stale relative
  to the compiled indicator artifact, or contains feature/kernel/prediction/
  direction/signal mismatches, and verifies that the parity-check script has
  a fresh compiled `.compiled` plus the expected `InpInputFile` /
  `InpOutputFile` / `InpIncludeFullHist` regeneration contract.

## Schema Enforcement

`parity` and `validate-fixtures` enforce the minimum Pine export schema before
running comparisons. Missing OHLC/time, feature slot (`F1_*` through `F5_*`),
kernel, prediction, direction, buy/sell, or stop columns fail validation
instead of silently reducing parity coverage. For manifest-backed or
CLI-configured custom features, the schema requires the expected
settings-derived feature column names, and the reader uses those columns for
comparison.

## Workflow Commands

The CLI commands for inventorying evidence, generating missing exports, and
gating release readiness are documented in detail in
[`ports/python/README.md`](../../ports/python/README.md). In brief:

| Command | Use it to |
| --- | --- |
| `audit-fixtures` | Inventory workspace evidence (source pins, fixtures, reports, untracked CSVs) without running parity. |
| `export-checklist` | Print each required missing CSV: filename, settings, TradingView labels, proof intent, CLI flags, columns, and helper commands. |
| `pine-export-helper` | Print the Pine `plot(..., display=display.data_window)` lines for the expected export columns (`--full` for alert/display/trade-stat fields; `--manifest-case` for a planned export's exact settings-aware helper, including the strict `Settings Fingerprint` plot). |
| `export-pack` / `verify-export-pack` | Write and verify a deterministic, SHA-256-traced handoff pack of helper snippets for every missing required case. |
| `readiness` / `readiness-blockers` | The release gate view / the compact blocker list with exact next actions. |
| `external-report-checklist` | The external parity report-refresh checklist on its own. |
| `external-runner-pack` / `verify-external-runner-pack` | Write and verify the external runtime preset and startup config files for stale or failing report cases. |
| `prepare-readiness-artifacts` / `verify-readiness-artifacts` | Generate and revalidate both handoff packs plus a top-level `readiness_artifacts.json` and `README.md` in one step. |

Example inventory run against an external workspace:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification audit-fixtures \
  --fixture-dir "/path/to/external-runtime/external"
```

## What the Unittest Suite Checks

Beyond fixture parity, the suite reads the canonical local Pine sources and
verifies that the Python core still represents the major business-logic
surfaces: normalized features, volatility/regime/ADX filters, kernels,
Lorentzian distance, ANN prediction, entries, exits, backtest stream, and
trade-stat outputs. The optional full-instrumentation surface also covers the
`showTradeStats` display gate as a numeric `Trade Stats Visible` export
column.

The suite also executes every required non-default settings case against its
representative `python_smoke_fixture` and requires the intended output family
to change versus a baseline run, so planned cases are known to be
behaviorally wired, not label-only coverage, even before their Pine parity
exports exist.
