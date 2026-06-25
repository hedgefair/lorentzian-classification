# Parity Baselines (TradingView gold exports)

These CSVs are **TradingView/Pine exports of the original Lorentzian Classification
indicator**, used as ground-truth to validate every port in this repo. They are the
authoritative per-bar reference for `Prediction`, `Direction`, `Buy`, `Sell`,
`StopBuy`, `StopSell`, the five normalized features, and the kernel estimate.

## Export schema (17 columns)

```
time, open, high, low, close,
F1_RSI, F2_WT, F3_CCI, F4_ADX, F5_RSI9,
Kernel Regression Estimate, Prediction, Direction,
Buy, Sell, StopBuy, StopSell
```

## Parity contract

| Column | Tolerance |
|--------|-----------|
| `F1_RSI` … `F5_RSI9`, `Kernel Regression Estimate` | `1e-6` |
| `Prediction`, `Direction` | exact (integer) |
| `Buy`, `Sell`, `StopBuy`, `StopSell` | exact (bool) |

Comparison starts at the first bar with a non-empty TradingView prediction
(i.e. after `maxBarsBackIndex`); warmup bars before that are not compared.

## Baselines and the settings each was exported with

| File | Bars | Settings |
|------|------|----------|
| `pine_oanda_eurusd_1d_full_history.csv` | 6216 | defaults, `includeFullHistory=true` |
| `pine_tastyfx_eurusd_1d_full_history.csv` | 7988 | defaults, `includeFullHistory=true` |
| `pine_coinbase_btcusd_1d_limited_history.csv` | 4155 | defaults, `includeFullHistory=false` |
| `pine_btcusd_h1_trimmed_limited_history.csv` | 2063 | trimmed H1 BTCUSD window, `includeFullHistory=false` |

## How to validate the Python port against these

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification parity \
  "tests/parity/baselines/pine_oanda_eurusd_1d_full_history.csv" \
  --tolerance 1e-6 --include-full-history

# or run the registered suite:
PYTHONPATH=ports/python python3 -m lorentzian_classification validate-fixtures \
  --fixture-dir tests/parity/baselines \
  --manifest tests/parity/baselines/baselines_manifest.json
```

## Validation results (Python port `core.py`, tolerance 1e-6)

| Baseline | Prediction / Direction / Buy / Sell / Stop / Stream | Feature max-diff | Verdict |
|----------|------------------------------------------------------|------------------|---------|
| `pine_oanda_eurusd_1d_full_history.csv` | all 0 mismatches | <= 2.8e-13, kernel 0.0 | **PASS** |
| `pine_tastyfx_eurusd_1d_full_history.csv` | all 0 mismatches | <= 2.2e-13, kernel 0.0 | **PASS** |
| `pine_coinbase_btcusd_1d_limited_history.csv` | all 0 mismatches | <= 2.4e-14, kernel 0.0 | **PASS** |
| `pine_btcusd_h1_trimmed_limited_history.csv` | all 0 mismatches | see current validation output | **PASS** |
