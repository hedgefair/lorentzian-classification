# Python Port: Maintainer and Release Workflows

This document covers the release-readiness and Pine-export machinery for the
Lorentzian Classification Python port. It was split out of the port's
[README.md](README.md) so that the README can stay focused on running the
library and CLI. Everything here is for maintainers preparing a release,
regenerating parity fixtures, or refreshing cross-checks against the external
reference runtime.

> **A note on the "external" runtime.** Several commands and packs below talk to
> an *external reference runtime* used only for cross-checks. The identifiers
> the Python code actually reads or emits are real and must not be renamed:
> the `LORENTZIAN_external_ROOT` environment variable, the `.set` preset files,
> the `[StartUp]` ini section, and the `external_runtime.exe` terminal command
> referenced by a generated runner pack.

## Maintainer command reference

These commands sit alongside the user-facing `run`, `parity`, and
`validate-fixtures` documented in the [README](README.md#command-reference).

| Command | Purpose |
| --- | --- |
| `audit-fixtures` | Inventory local fixture/source evidence without running parity. |
| `export-checklist` | Print the required Pine exports still needed for full coverage. |
| `pine-export-helper` | Generate the Pine plot snippet that exposes the parity columns in TradingView's CSV export. |
| `export-pack` / `verify-export-pack` | Write (and verify) a deterministic handoff pack for all missing required exports. |
| `import-pine-exports` | Validate and copy staged TradingView CSV downloads into the fixture set. |
| `readiness` / `readiness-blockers` | Report release readiness; `readiness-blockers --json` returns just the blocker classes and next actions. |
| `pine-input-contract` / `pine-output-contract` | Source-level checks that Pine inputs/outputs still map to the Python `Settings` and CLI output surface. |
| `external-report-checklist` | Report-refresh checklist for the external side of the parity gate. |
| `external-runner-pack` / `verify-external-runner-pack` | Write (and verify) external runner presets/configs to rerun stale or failing report cases. |
| `prepare-readiness-artifacts` / `verify-readiness-artifacts` | Generate (and revalidate) both handoff artifact packs in one step. |

Most commands accept `--json` for machine-readable output.

## Fixture Lookup and the External Workspace

The default parity source is the set of TradingView/PineScript exports
committed under `tests/parity/baselines/`. To validate against more Pine
exports, point the CLI at another directory. `--fixture-dir` always takes
precedence and must resolve to a directory, not a file. When it is omitted,
CLI commands use `LORENTZIAN_PARITY_FIXTURE_DIR` when set, then
`LORENTZIAN_external_ROOT` (a workspace root whose `Files/` directory is
searched first and the root second), and finally the repo-local baselines.

You can also pass the workspace root explicitly. The CLI searches its `Files/`
directory first and then the root itself, so root-level Pine exports can be
covered by adding them to the manifest:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification validate-fixtures \
  --fixture-dir "/path/to/parity-workspace"
```

## Source locks

When a manifest is present, validation also checks the manifest-pinned Pine and
external sources:

- Required source files must be present, match their recorded SHA-256 hashes,
  and satisfy the configured debug-marker policy. The debug-marker policy
  rejects debug naming plus common logging/display calls such as `Print`,
  `PrintFormat`, `Comment`, `Alert`, and `console.log`.
- For Pine comment or blank-line drift, a source can additionally pin
  `code_sha256`; exact hash drift remains visible in JSON/audit output, but
  the business-logic source lock can still pass when the non-comment code hash
  matches.
- Manifest-pinned sources count as present only when the path resolves to a
  file; same-named directories are treated as missing source evidence.
- Readiness also runs external source sanity checks for known parity traps,
  including incremental ANN replay through `prev_calculated - 1`.
- For the pinned external indicator, readiness checks that the exposed `input`
  defaults match the Python `Settings` defaults and the Pine input contract,
  so a changed external default cannot silently drift away from the
  documented CLI behavior.
- The manifest must be unambiguous: tracked fixtures, required export cases,
  Pine sources, and external sources cannot reuse names, filenames, paths, or
  selector values in ways that could make coverage or `--manifest-case`
  resolution unclear.

## Input schema enforcement

`parity` and `validate-fixtures` reject incomplete Pine export schemas before
comparison:

- A minimum parity CSV must include chart `time/open/high/low/close`, all five
  feature slot columns (`F1_*` through `F5_*`), kernel estimate, prediction,
  direction, buy/sell, and stop-buy/stop-sell columns.
- Header-only exports are rejected because zero compared bars cannot prove
  parity.
- Duplicate CSV headers are rejected so TradingView export columns cannot be
  silently overwritten during parsing.
- Malformed data rows are reported as validation errors instead of uncaught
  parser tracebacks.
- `run` and `parity` output paths must point to files inside existing
  directories; `validate-fixtures --output-mismatches` must point to a
  directory path.
- When the fixture or CLI settings are known, validation requires the exact
  settings-derived feature column names, such as `F1_CCI` for a custom first
  feature, and the reader uses those columns even if stale default feature
  columns are also present.

## Auditing Local Evidence

Inventory the local fixture/source evidence without running parity:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification audit-fixtures
```

The audit reports manifest-pinned Pine and external sources with SHA-256
hashes and debug-marker status, tracked fixture presence, required uncovered
fixture presence, explicitly ignored CSV candidates with their reason,
external source sanity results, compiled `.compiled` freshness for the pinned
external indicator, manifest-listed external parity comparison reports with
mismatch and freshness status, untracked CSV candidates with evidence
classification, and all discovered local `.pine` files. Non-file paths with
`.csv` suffixes are reported as `not_a_file` candidates rather than parsed as
exports. Add `--json` to emit the same inventory as machine-readable JSON for
CI or release checks.

## Manifest and Strict Coverage

The default fixture list is manifest-driven from
`tests/parity/fixtures_manifest.json` when running from this repository. Use
`--manifest path/to/fixtures_manifest.json` to validate another Pine export
set. The manifest path must resolve to a JSON file. Each fixture can carry the
exact Pine settings used for that export.

- Manifest fixture filenames, required-case smoke fixture references, and
  source paths must be relative in-workspace paths; POSIX/Windows absolute
  paths and `..` traversal are rejected. Fixture filenames and source paths
  also cannot differ only by case.
- Manifest fixture paths count as present only when they resolve to files;
  same-named directories are treated as missing export evidence.
- Required uncovered cases must be concrete manifest objects with a target
  filename, proof intent, settings, and `python_smoke_fixture`; label-only
  planned cases are rejected.

Use `--require-full-coverage` when you want the command to fail until the
manifest's required non-default Pine export cases are present and pass parity.
When strict coverage is incomplete, the text and JSON output include
actionable missing-export details from the manifest: target filename, behavior
proved, TradingView settings, equivalent Python CLI flags, and export column
schema. Present required exports must include the full helper schema plus the
manifest-specific `Settings Fingerprint` column so validation can prove the
CSV matches the declared input settings.

Strict coverage also exercises each required non-default Python settings path
against a representative existing Pine fixture named by
`python_smoke_fixture` in the manifest. That smoke check proves the Python
implementation can execute the planned setting combination and produce
internally valid rows: aligned bar times, bounded predictions, valid
direction/backtest stream enum values, finite kernel output, and finite active
feature output. It is not a substitute for the missing Pine export parity
file.

## Generating Missing Pine Exports

### Export checklist

Print the required Pine exports that still need to be generated for full
coverage:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification export-checklist
```

The checklist is manifest-driven and includes the target filenames, the Pine
settings each export must use, the matching TradingView input labels, the
behavior each export proves, equivalent CLI flags, and the minimum/full
instrumentation CSV columns needed for parity. It also identifies the Pine
series expression for each export column, including numeric helper-code
expressions for Pine string/color display fields. Feature export column names
are settings-aware for planned custom-feature cases while still satisfying the
slot-based schema accepted by the parser (`F1_*` through `F5_*`). The
checklist also prints the exact `pine-export-helper` command for each planned
export, plus a `--full` variant for optional alert/display/trade-stat columns.
Use `--case <name-or-filename>` to focus the checklist on one planned export.
Pass `--json` for structured output.

Strict readiness requires required export CSVs to include every numeric
data-window column from the full helper output. String/color display fields
are exported as deterministic numeric codes and decoded back to their Pine
display values by the Python reader, so the full helper output has no
helper-only columns.

### Pine export helper

Generate the Pine plot snippet needed to expose the minimum parity columns in
TradingView's CSV export:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification pine-export-helper
```

Add `--full` to include optional alert, display, and trade-stat fields. The
helper emits numeric codes for Pine string/color fields so TradingView CSV
exports can still validate those display surfaces. Full manifest-case helpers
also emit a constant `Settings Fingerprint` plot, which strict validation uses
to reject CSVs exported with the right filename but wrong TradingView inputs.
Use `--manifest --manifest-case <name-or-filename>` to emit settings-aware
helper lines for one planned missing export, including custom feature column
labels:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification pine-export-helper \
  --manifest tests/parity/fixtures_manifest.json \
  --manifest-case coinbase_daily_custom_features_count3
```

### Export pack

Write a deterministic export pack for all currently missing required exports:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification export-pack \
  --output /tmp/lorentzian-export-pack
```

The pack contains one full-instrumentation Pine helper snippet per missing
manifest case, plus `export_pack.json` and a README index with target
filenames and settings, required full-column counts, settings fingerprints,
and an `acceptance_manifest.csv` spreadsheet-style checklist for the CSV
generation handoff. The JSON index and CSV checklist include each helper
snippet SHA-256 so the generated CSVs can be traced back to the exact helper
text. The pack also records a Pine source lock fingerprint derived from the
manifest-pinned Pine source SHA-256 values, and `export-pack` refuses to run
when those local Pine source locks are missing or mismatched.

The output path must be a directory or a new directory path whose parents can
be created. Add `--include-present` to regenerate snippets for required cases
that already have CSVs, or `--case <name-or-filename>` to write only selected
cases. Export-pack rejects manifest case selections whose sanitized helper
snippet filenames would collide, including case-only collisions on macOS-style
filesystems. If the output directory already contains `.pine` files not
produced by the current selection, the command fails by default; use
`--clean-stale` to remove stale files from earlier `export-pack` runs, or
`--allow-stale` when the directory intentionally contains unrelated Pine
files.

Verify an export pack before using its helpers:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification verify-export-pack \
  /tmp/lorentzian-export-pack
```

The verifier checks `export_pack.json`, `acceptance_manifest.csv`, helper
snippet SHA-256 values, safe helper snippet filenames, settings fingerprints,
Pine source lock fingerprints, safe Pine source lock paths, required
full-column counts, safe target CSV names, and that every referenced pack
artifact is a file inside the export-pack directory. Artifact references may
be absolute POSIX paths inside the pack or relative paths; Windows drive/UNC
paths are rejected so Wine-style handoff metadata cannot be misread as
relative filenames on macOS.

## Release Readiness

Report release readiness across source locks, fixture parity, strict required
exports, required-settings smoke execution, the pinned `lcv6.pine` input and
output-surface contracts, external compiled artifact freshness, external
parity report freshness/mismatch checks, unexpected Pine export candidates,
and missing-export actions:

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification readiness
```

Add `--json` for a machine-readable readiness report.

- Use `readiness-blockers --json` when you only need the concise list of
  blocker classes and the exact Pine export / external report actions still
  required for release readiness, including the matching
  `external-runner-pack --only-failing` command for stale or failing external
  reports. Workflow command arrays also include shell-quoted
  `*_command_string` values for paths that contain spaces.
- When exports are missing, the readiness and strict validation output include
  the exact `pine-export-helper` commands to generate the minimum or full
  helper snippet for each missing manifest case, plus the one-shot
  `export-pack` and `verify-export-pack` commands for the full missing-export
  artifact set.
- Pass `--pine-export-source-dir <dir>` to `validate-fixtures --json`,
  `readiness --json`, or `readiness-blockers --json` when you already know
  where the TradingView CSV downloads are staged; the generated
  `import-pine-exports` workflow command will use that directory instead of
  the placeholder path. `import-pine-exports` also accepts multiple source
  directories and uses the first matching filename, which is useful when
  exports may be split across Downloads, Desktop, and a staging
  folder.
- Use `pine-input-contract --fixture-dir <external-root-or-Files> --json` to
  run only the static `lcv6.pine` input/default check that proves each Pine
  input still has a matching Python `Settings`/CLI surface.
- Use `pine-output-contract --fixture-dir <external-root-or-Files> --json` to
  run only the source-level check that Pine plots, signal shapes, alerts,
  labels, colors, backtest stream, and trade-stat table fields still map to
  CLI output columns. The same readiness path source-checks the pinned
  external indicator inputs against the Python/Pine default settings contract.

### External report gates

- When manifest-pinned external source still contains known non-parity ANN
  replay patterns, readiness remains false even if the SHA-256 pin matches.
- Readiness remains false when a required external parity comparison report is
  missing, stale relative to the compiled indicator artifact, or contains
  feature, kernel, prediction, direction, buy, or sell mismatches. The
  readiness JSON includes `external_parity_report_actions` with the parity
  script path and the exact `InpInputFile`, `InpOutputFile`, and
  `InpIncludeFullHist` values to use when regenerating stale reports in
  the external runtime.
- The external report gate also checks that the configured parity-check script
  has a fresh compiled `.compiled`, because those comparison reports are
  generated by the script rather than by the Python port. It also
  source-checks that script's regeneration contract: `InpInputFile`,
  `InpOutputFile`, and `InpIncludeFullHist` must exist, be used, and produce
  the expected comparison CSV header.
- Use `external-report-checklist --fixture-dir <external-root-or-Files> --json`
  for the same report-refresh checklist without running the full Pine fixture
  gate.
- Use `external-runner-pack --fixture-dir <external-root-or-Files>
  --only-failing --output <dir>` to write the external runner `.set` presets
  and `[StartUp]` `.ini` files needed to rerun the stale or failing report
  cases from the same manifest evidence. Run
  `verify-external-runner-pack <dir>` before using that pack; it checks the
  runner index plus each generated preset and startup config against the
  recorded manifest inputs.
- Use `prepare-readiness-artifacts --output <dir>` to generate and verify both
  handoff artifact sets at once: a Pine export pack and an external runner
  pack. The output directory includes a top-level `readiness_artifacts.json`
  and `README.md`. Add `--pine-export-source-dir <dir>` when creating that
  artifact set if the README should point at a real staged TradingView
  download directory. Run `verify-readiness-artifacts <dir>` to validate that
  top-level artifact set and both nested packs after moving or editing the
  artifact directory. `readiness-blockers --json` includes the same prepare
  and verify commands under `readiness_artifacts_workflow`, with shell-quoted
  command strings for the active fixture directory and manifest.
- Readiness also remains false if a TradingView/Pine-looking CSV exists in the
  fixture search path but is not tracked, required, or explicitly listed under
  the manifest's `ignored_csv_candidates` with a reason.
