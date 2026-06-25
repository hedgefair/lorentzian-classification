//! Market-regime filter (the KLMF-based normalized-slope detector).
//!
//! Port of the regime filter in `MLExtensions.pine`. Returns the per-bar
//! absolute KLMF slope and its smoothed EMA; the caller derives the normalized
//! slope and threshold comparison.

use crate::types::nz;

/// Computes `(abs_slope, ema_abs_slope)` for the regime filter.
#[must_use]
pub fn calc_regime_filter(ohlc4: &[f64], high: &[f64], low: &[f64]) -> (Vec<f64>, Vec<f64>) {
    let n = ohlc4.len();
    let mut abs_slope = vec![0.0; n];
    let mut ema_abs = vec![0.0; n];
    if n == 0 {
        return (abs_slope, ema_abs);
    }
    let mut value1 = vec![0.0; n];
    let mut value2 = vec![0.0; n];
    let mut klmf = vec![0.0; n];
    value2[0] = high[0] - low[0];
    klmf[0] = ohlc4[0];
    let alpha_ema = 2.0 / 201.0;
    for i in 1..n {
        value1[i] = 0.2 * (ohlc4[i] - ohlc4[i - 1]) + 0.8 * nz(value1[i - 1], 0.0);
        value2[i] = 0.1 * (high[i] - low[i]) + 0.8 * nz(value2[i - 1], 0.0);
        let omega = if value2[i] != 0.0 {
            (value1[i] / value2[i]).abs()
        } else {
            0.0
        };
        let alpha = (-(omega.powf(2.0)) + (omega.powf(4.0) + 16.0 * omega.powf(2.0)).sqrt()) / 8.0;
        klmf[i] = alpha * ohlc4[i] + (1.0 - alpha) * nz(klmf[i - 1], 0.0);
        abs_slope[i] = (klmf[i] - klmf[i - 1]).abs();
        let prev_ema = nz(ema_abs[i - 1], 0.0);
        if prev_ema == 0.0 && i < 200 {
            ema_abs[i] = abs_slope[i];
        } else {
            ema_abs[i] = alpha_ema * abs_slope[i] + (1.0 - alpha_ema) * prev_ema;
        }
    }
    (abs_slope, ema_abs)
}
