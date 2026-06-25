# Lean Port

An executable **Lean 4 formal specification** of Lorentzian Classification.
This is the reference-spec port: beyond reproducing the algorithm, it states
and (where core Lean allows) proves the invariants that define correctness,
following the team's Lean-spec-porting policy (theorems are the spec; a Lean
file that would convey the same information in Python is not a spec yet).

## Status

Implemented and parity-verified. The Python port is the parity authority for
the 40-column output schema; the PineScript source is the algorithmic ground
truth.

| Check | Result |
| --- | --- |
| `lake build` on the pinned toolchain (`lean-toolchain`: v4.30.0) | clean (1 deliberate `sorry` warning in `Properties/Deferred.lean`; see debt table) |
| `lake test` (theorem-named property tests) | 46 checks, 0 failures |
| `parity` subcommand vs all 4 gold baselines (`tests/parity/baselines/`) | PASS, max feature/kernel diff ≪ 1e-6; signals exact |
| Full 40-column output vs Python (`tests/parity/compare_csv.py`, tol 1e-9) | MATCH on all 4 baselines, max diff ≤ 4.8e-14 |
| Full 40-column output vs Rust | **byte-identical** on all 4 baselines |
| Non-default settings DRT vs Python (16 variants × 600 synthetic bars, incl. dynamic exits, EMA/SMA/ADX filters, kernel smoothing, custom features, `neighborsCount=5`, worst-case, alternate kernel params, display toggles; mirrors the manifest's planned non-default export cases) | MATCH on all 16, tol 1e-9 |

Validation details and per-fixture results: `docs/validation.md`.

## Layout

```
lakefile.lean / lean-toolchain      build config (lib + CLI exe + test driver)
LorentzianClassification.lean       root import aggregator
LorentzianClassification/
  Basic.lean                        PSFloat (na semantics), Direction, bounded setting types
  Numeric.lean                      exact %.Nf formatter, parser, banker's rounding, quantSub
  Csv.lean                          RFC-4180-style reader/writer (CRLF, QUOTE_MINIMAL)
  TimeSeries.lean                   Bar, Pine-style lookback series
  Normalize.lean                    rescale / running min-max normalization
  Indicators/                       EMA, SMA, RMA, RSI, CCI, ADX, WaveTrend, ATR, window helpers
  Kernels.lean                      Nadaraya-Watson rational-quadratic + gaussian
  Features.lean                     the five normalized feature slots
  Distance.lean                     Lorentzian distance (left-associated sums)
  ANN.lean                          the approximate-nearest-neighbor scan (ratchet, FIFO, descending branch)
  Labels.lean                       4-bar training labels (inverted mapping is ground truth)
  Filters.lean                      volatility / regime (KLMF) / ADX filters
  KernelFilters.lean                kernel rate/cross/smooth booleans
  Signals.lean                      signal state machine, barssince counters, backtest stream
  Backtest.lean                     trade accounting
  Display.lean                      Pine color/label encoding
  Pipeline.lean                     Settings, ResultRow, stepBar, processAllBars
  Properties/                       proved invariants + deferred (sorry) statements
Main.lean                           CLI (run / parity)
Tests.lean                          theorem-named executable property tests
```

## Build, test, run

```bash
cd ports/lean
lake build          # library + CLI
lake test           # property tests (theorem-named)

# Compute the full 40-column result CSV (CLI contract mirrors the Rust port):
.lake/build/bin/lorentzian-classification run input.csv output.csv [--include-full-history] [--max-bars-back N]

# Self-contained parity check against a TradingView/Pine export's own columns:
.lake/build/bin/lorentzian-classification parity export.csv [--tolerance 1e-6]
```

The three-way harness `tests/parity/cross_port_parity.sh` builds and diffs
Rust == Python == Lean across the full schema on every gold baseline.

## Theorems and proof status

Proved in core Lean (no external dependencies), in
`LorentzianClassification/Properties/`:

| Theorem | Meaning |
| --- | --- |
| `annLoopStep_preserves_size_bound` | the neighbor buffer never exceeds `neighborsCount` |
| `annLoopStep_sizes_eq` | distance/prediction buffers stay in lockstep |
| `annLoopStep_accept_updates_ratchet` | **the historic-bug invariant**: an accepting, non-overflow step sets `lastDistance` to exactly the accepted distance |
| `annLoopStep_ratchet_monotone_in_fill_phase` | the ratchet is non-decreasing during the fill phase |
| `annLoopStep_overflow_reanchors_and_evicts` | on overflow the ratchet re-anchors to the banker's-rounded ¾-index and the OLDEST neighbor is evicted |
| `mem_annScanIndices` / `annScanIndices_length` | the scan covers exactly the closed interval `[startIndex, sizeLoop]`, ascending or descending |
| `computePrediction_natAbs_le` | `\|prediction\| ≤` neighbor count (labels are ±1/0) |
| `direction_toInt_natAbs_le_one`, `direction_ofInt_toInt` | label encoding sanity |
| `processAllBars_length`, `runBars_length` | exactly one output row per input bar |
| `nextSignal_holds_on_zero_prediction`, `nextSignal_holds_when_filtered`, `nextSignal_long_iff`, `nextSignal_short_iff` | the signal hold/flip rule |
| `nextBarsHeld_spec` | bars-held counter semantics |
| `backtestStream_mem` | stream values ∈ {1, 2, −1, −2, 0} |
| `gradientTransparency_mem` | the confidence ladder emits only the ten Pine levels |

Every theorem above has a same-named executable property test in `Tests.lean`
(the helper lemma `foldl_add_natAbs_le` is exercised through
`computePrediction_natAbs_le`).

### Formalization debt

Tracked in `Properties/Deferred.lean`; each invariant is also extracted to an
executable test:

| Invariant | Stated | Proven | Extracted to test |
| --- | --- | --- | --- |
| `lorentzianDistance_nonneg` (Float) | ✅ | ❌ `sorry` (needs `log(1+x) ≥ 0` + libm monotonicity; opaque in core Lean) | ✅ |
| `ratNormalizeStep_bounded` (ℚ model) | ✅ | ✅ (proved from first principles, incl. the ℚ scaffolding) | ✅ (Float, 1e-12 slack) |
| `ratWeightedMean_within_bounds` (ℚ model) | ✅ | ✅ (convex-combination bound by fold induction) | ✅ (Float kernels, 1e-9 slack) |

The normalization and kernel bounds are deliberately stated and proved over
ℚ: adversarial review produced IEEE counterexamples showing the Float-exact
bounds can be exceeded by one ulp of final-operation rounding, so the
Float-exact statements are false and the executable tests carry explicit
rounding slack instead. Core Lean has no ordered-field lemma library, so the
needed min/max/division/sum-monotonicity facts are derived in-file.

## Documented deviations

This port follows the **Python reference** (the parity authority) wherever
Python and a literal Pine reading differ. All are invisible under default
settings on the committed baselines but are recorded so nobody "fixes" them:

- **Kernel warmup**: Pine yields `na` for the first `regressionLevel + 1`
  bars; Python/Rust/Lean clip the window and emit estimates from bar 0.
- **`barssince` sentinels**: never-fired conditions count from 999999 instead
  of Pine's `na`-false comparisons (affects only dynamic exits).
- **`isLastSignalBuy/Sell`**: checks only `signal[4]` (Pine also requires the
  4-bars-ago EMA/SMA trend flags; affects only non-default trend filters).
- **Strict exits**: gate on `startShortTrade`/`startLongTrade` where Pine uses
  `isNewSellSignal`/`isNewBuySignal` (differ only under the kernel filter).
- **Volatility filter warmup**: permissive (`true`) while the ATRs warm up
  (Pine's `na >` comparison is false).
- **Regime filter seeding**: `value2[0] = high−low`, `klmf[0] = ohlc4`, and a
  `i < 200` pass-through EMA warmup, per Python `calc_regime_filter`.
- **Neighbor threshold index**: `roundTiesEven(neighborsCount·3/4)` (Python
  `round`); Pine's integer division truncates; they differ only for
  neighbor counts like 6 or 10, not the default 8.
- **ATR first-bar true range**: the previous close defaults to `0.0`, so
  `TR[0] ≈ high` (Pine's `ta.atr` uses `high − low` on the first bar); the
  contamination decays geometrically and only brushes signal-relevant bars on
  datasets shorter than `maxBarsBack`.
- **ADX trend filter source**: always reads `close` (Pine feeds
  `settings.source` into `ml.filter_adx`); visible only with
  `useAdxFilter = true` and a non-close source.
- **Kernel crossover boundaries**: `isBullishCross` uses `≥`/`<` (and
  bearish `≤`/`>`) where Pine's `ta.crossover`/`ta.crossunder` pair strict
  and non-strict the other way; differs only on exact-tie bars under
  `useKernelSmoothing`.
- **Zero-denominator guards**: CCI/WaveTrend/ADX substitute `0.0` where
  Pine's division by zero yields `na` (flat-window edge cases).
- **Backtest price seeds**: `startLongPrice`/`startShortPrice` start at
  `0.0` (Pine seeds both with the first bar's market price); affects stats
  only if an exit fires before its entry.
- **`maxBarsBack = 0`**: the pipeline returns prediction `0` (a deliberate
  guard; the Python reference index-wraps through negative indices there
  and the Rust port panics; neither is a behavior worth mirroring).
- **Indicator period `0`**: lengths are clamped to `≥ 1` (Python emits
  all-`na` series for `period ≤ 0`); unreachable through `Settings` defaults
  and the CLI.
- **Training-label inversion** (a 4-bar price RISE labels SHORT) is verbatim
  ground truth from the original indicator; deliberately NOT fixed.

## Numerics

- `Float` is IEEE binary64 compiled to C; transcendentals come from the same
  libm family as Rust/CPython, which is why the output is byte-identical to
  the Rust port on the baselines.
- All sums are accumulated in the reference's exact order (left-associated;
  no FMA), banker's rounding everywhere Python `round()` appears, and the
  CSV writer reproduces CPython's `%.8f`/`%.16f` exactly (differentially
  validated against CPython on 35k+ random doubles, including subnormals).
- `na` is `Option Float` (`PSFloat`) rather than a NaN sentinel; missingness
  is visible in the types; NaN-valued cells are still rendered as empty.
