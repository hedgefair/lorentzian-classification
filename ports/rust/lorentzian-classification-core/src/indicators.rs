//! Moving averages, oscillators, and normalized features.
//!
//! Every function is a line-faithful port of the corresponding helper in the
//! Python reference (`core.py`), preserving accumulation order and rounding so
//! results are bit-for-bit identical.

use crate::types::{is_missing, FeatureKind, FeatureSpec, MISSING};

/// Linear rescale of `src` from `[old_min, old_max]` to `[new_min, new_max]`.
#[must_use]
pub fn rescale(src: f64, old_min: f64, old_max: f64, new_min: f64, new_max: f64) -> f64 {
    new_min + (new_max - new_min) * (src - old_min) / (old_max - old_min).max(1e-9)
}

/// Simple moving average.
#[must_use]
pub fn sma(src: &[f64], period: i64) -> Vec<f64> {
    let mut out = vec![MISSING; src.len()];
    if period <= 0 {
        return out;
    }
    for i in 0..src.len() {
        if (i as i64) < period - 1 {
            continue;
        }
        let start = (i as i64 - period + 1) as usize;
        let window = &src[start..=i];
        if window.iter().any(|v| is_missing(*v)) {
            continue;
        }
        out[i] = window.iter().sum::<f64>() / period as f64;
    }
    out
}

/// Exponential moving average, seeded with an SMA of the first full window.
#[must_use]
pub fn ema(src: &[f64], period: i64) -> Vec<f64> {
    let mut out = vec![MISSING; src.len()];
    if period <= 0 {
        return out;
    }
    let alpha = 2.0 / (period + 1) as f64;
    for i in 0..src.len() {
        let value = src[i];
        if is_missing(value) {
            continue;
        }
        if i > 0 && !is_missing(out[i - 1]) {
            out[i] = alpha * value + (1.0 - alpha) * out[i - 1];
            continue;
        }
        if (i as i64) >= period - 1 {
            let start = (i as i64 - period + 1) as usize;
            let window = &src[start..=i];
            if !window.iter().any(|v| is_missing(*v)) {
                out[i] = window.iter().sum::<f64>() / period as f64;
            }
        }
    }
    out
}

/// Wilder's running moving average (RMA / SMMA), seeded with an SMA.
#[must_use]
pub fn rma(src: &[f64], period: i64) -> Vec<f64> {
    let mut out = vec![MISSING; src.len()];
    if period <= 0 {
        return out;
    }
    let alpha = 1.0 / period as f64;
    for i in 0..src.len() {
        let value = src[i];
        if is_missing(value) {
            continue;
        }
        if i > 0 && !is_missing(out[i - 1]) {
            out[i] = alpha * value + (1.0 - alpha) * out[i - 1];
            continue;
        }
        if (i as i64) >= period - 1 {
            let start = (i as i64 - period + 1) as usize;
            let window = &src[start..=i];
            if !window.iter().any(|v| is_missing(*v)) {
                out[i] = window.iter().sum::<f64>() / period as f64;
            }
        }
    }
    out
}

/// Wilder's non-normalized running smoother used by the ADX calculation.
#[must_use]
pub fn wilder_smooth(src: &[f64], period: i64) -> Vec<f64> {
    let mut out = vec![MISSING; src.len()];
    if period <= 0 {
        return out;
    }
    let period_f = period as f64;
    for i in 0..src.len() {
        let value = src[i];
        if i == 0 || is_missing(out[i - 1]) {
            out[i] = value;
        } else {
            out[i] = out[i - 1] - out[i - 1] / period_f + value;
        }
    }
    out
}

/// Running min/max normalization to `[out_min, out_max]` (Pine `normalize`).
#[must_use]
pub fn normalize_running(src: &[f64], out_min: f64, out_max: f64) -> Vec<f64> {
    let mut out = vec![MISSING; src.len()];
    let mut historic_min = 1e11_f64;
    let mut historic_max = -1e11_f64;
    for i in 0..src.len() {
        let value = src[i];
        if is_missing(value) {
            continue;
        }
        historic_min = historic_min.min(value);
        historic_max = historic_max.max(value);
        out[i] = out_min
            + (out_max - out_min) * (value - historic_min)
                / (historic_max - historic_min).max(1e-9);
    }
    out
}

/// Wilder's RSI.
#[must_use]
pub fn calc_rsi(src: &[f64], period: i64) -> Vec<f64> {
    let n = src.len();
    let mut gain = vec![MISSING; n];
    let mut loss = vec![MISSING; n];
    for i in 1..n {
        let change = src[i] - src[i - 1];
        gain[i] = if change > 0.0 { change } else { 0.0 };
        loss[i] = if change < 0.0 { -change } else { 0.0 };
    }
    let gain_rma = rma(&gain, period);
    let loss_rma = rma(&loss, period);
    let mut out = vec![MISSING; n];
    for i in 0..n {
        if is_missing(gain_rma[i]) || is_missing(loss_rma[i]) {
            continue;
        }
        if loss_rma[i] == 0.0 {
            out[i] = 100.0;
        } else {
            let rs = gain_rma[i] / loss_rma[i];
            out[i] = 100.0 - 100.0 / (1.0 + rs);
        }
    }
    out
}

/// Normalized (EMA-smoothed, rescaled to `[0, 1]`) RSI feature.
#[must_use]
pub fn calc_normalized_rsi(src: &[f64], n1: i64, n2: i64) -> Vec<f64> {
    let smoothed = ema(&calc_rsi(src, n1), n2);
    smoothed
        .into_iter()
        .map(|v| {
            if is_missing(v) {
                MISSING
            } else {
                rescale(v, 0.0, 100.0, 0.0, 1.0)
            }
        })
        .collect()
}

/// Commodity Channel Index.
#[must_use]
pub fn calc_cci(src: &[f64], period: i64) -> Vec<f64> {
    let n = src.len();
    let avg = sma(src, period);
    let mut out = vec![MISSING; n];
    for i in 0..n {
        if is_missing(avg[i]) {
            continue;
        }
        let start = (i as i64 - period + 1) as usize;
        let window = &src[start..=i];
        let mean_dev = window.iter().map(|v| (v - avg[i]).abs()).sum::<f64>() / period as f64;
        out[i] = if mean_dev != 0.0 {
            (src[i] - avg[i]) / (0.015 * mean_dev)
        } else {
            0.0
        };
    }
    out
}

/// Normalized (EMA-smoothed, running-normalized) CCI feature.
#[must_use]
pub fn calc_normalized_cci(src: &[f64], n1: i64, n2: i64) -> Vec<f64> {
    normalize_running(&ema(&calc_cci(src, n1), n2), 0.0, 1.0)
}

/// Normalized WaveTrend feature.
#[must_use]
pub fn calc_wavetrend(hlc3: &[f64], n1: i64, n2: i64) -> Vec<f64> {
    let n = hlc3.len();
    let ema1 = ema(hlc3, n1);
    let abs_dev: Vec<f64> = (0..n)
        .map(|i| {
            if is_missing(ema1[i]) {
                MISSING
            } else {
                (hlc3[i] - ema1[i]).abs()
            }
        })
        .collect();
    let ema2 = ema(&abs_dev, n1);
    let mut ci = vec![MISSING; n];
    for i in 0..n {
        if is_missing(ema1[i]) || is_missing(ema2[i]) {
            continue;
        }
        ci[i] = if ema2[i] != 0.0 {
            (hlc3[i] - ema1[i]) / (0.015 * ema2[i])
        } else {
            0.0
        };
    }
    let wt1 = ema(&ci, n2);
    let wt2 = sma(&wt1, 4);
    let raw: Vec<f64> = (0..n)
        .map(|i| {
            if !is_missing(wt1[i]) && !is_missing(wt2[i]) {
                wt1[i] - wt2[i]
            } else {
                MISSING
            }
        })
        .collect();
    normalize_running(&raw, 0.0, 1.0)
}

/// Price-scaled subtraction matching the Pine/external ADX quantization.
///
/// When `scale > 0`, both operands are rounded (ties to even, like Python's
/// `round`) at the given decimal scale before subtraction.
#[must_use]
pub fn quant_sub(a: f64, b: f64, scale: f64) -> f64 {
    if scale <= 0.0 {
        return a - b;
    }
    ((a * scale).round_ties_even() - (b * scale).round_ties_even()) / scale
}

/// Average True Range.
#[must_use]
pub fn calc_atr(high: &[f64], low: &[f64], close: &[f64], period: i64) -> Vec<f64> {
    let n = close.len();
    let mut tr = Vec::with_capacity(n);
    for i in 0..n {
        let prev_close = if i > 0 { close[i - 1] } else { 0.0 };
        tr.push(
            (high[i] - low[i])
                .max((high[i] - prev_close).abs())
                .max((low[i] - prev_close).abs()),
        );
    }
    rma(&tr, period)
}

/// Normalized (rescaled to `[0, 1]`) ADX feature.
#[must_use]
pub fn calc_adx(
    high: &[f64],
    low: &[f64],
    close: &[f64],
    period: i64,
    price_scale: f64,
) -> Vec<f64> {
    let n = close.len();
    let mut tr = Vec::with_capacity(n);
    let mut dm_plus = Vec::with_capacity(n);
    let mut dm_minus = Vec::with_capacity(n);
    for i in 0..n {
        let prev_close = if i > 0 { close[i - 1] } else { 0.0 };
        let prev_high = if i > 0 { high[i - 1] } else { 0.0 };
        let prev_low = if i > 0 { low[i - 1] } else { 0.0 };
        tr.push(
            quant_sub(high[i], low[i], price_scale)
                .max(quant_sub(high[i], prev_close, price_scale).abs())
                .max(quant_sub(low[i], prev_close, price_scale).abs()),
        );
        let up_move = quant_sub(high[i], prev_high, price_scale);
        let down_move = quant_sub(prev_low, low[i], price_scale);
        dm_plus.push(if up_move > down_move && up_move > 0.0 {
            up_move
        } else {
            0.0
        });
        dm_minus.push(if down_move > up_move && down_move > 0.0 {
            down_move
        } else {
            0.0
        });
    }
    let tr_smooth = wilder_smooth(&tr, period);
    let plus_smooth = wilder_smooth(&dm_plus, period);
    let minus_smooth = wilder_smooth(&dm_minus, period);
    let mut dx = Vec::with_capacity(n);
    for i in 0..n {
        let di_plus = if tr_smooth[i] != 0.0 {
            plus_smooth[i] / tr_smooth[i] * 100.0
        } else {
            0.0
        };
        let di_minus = if tr_smooth[i] != 0.0 {
            minus_smooth[i] / tr_smooth[i] * 100.0
        } else {
            0.0
        };
        dx.push(if di_plus + di_minus != 0.0 {
            (di_plus - di_minus).abs() / (di_plus + di_minus) * 100.0
        } else {
            0.0
        });
    }
    let adx_rma = rma(&dx, period);
    adx_rma
        .into_iter()
        .map(|v| {
            if is_missing(v) {
                MISSING
            } else {
                rescale(v, 0.0, 100.0, 0.0, 1.0)
            }
        })
        .collect()
}

/// Raw (un-normalized, `0..100`) ADX used by the ADX trend filter.
#[must_use]
pub fn calc_raw_adx_filter(high: &[f64], low: &[f64], close: &[f64], period: i64) -> Vec<f64> {
    calc_adx(high, low, close, period, 0.0)
        .into_iter()
        .map(|v| if is_missing(v) { 0.0 } else { v * 100.0 })
        .collect()
}

/// Computes one normalized feature slot from the requested series.
#[must_use]
pub fn calc_feature(
    spec: FeatureSpec,
    close: &[f64],
    high: &[f64],
    low: &[f64],
    hlc3: &[f64],
    price_scale: f64,
) -> Vec<f64> {
    match spec.kind {
        FeatureKind::Rsi => calc_normalized_rsi(close, spec.param_a, spec.param_b),
        FeatureKind::Wt => calc_wavetrend(hlc3, spec.param_a, spec.param_b),
        FeatureKind::Cci => calc_normalized_cci(close, spec.param_a, spec.param_b),
        FeatureKind::Adx => calc_adx(high, low, close, spec.param_a, price_scale),
    }
}
