//! Nadaraya-Watson kernel regression estimators (Rational Quadratic, Gaussian).
//!
//! Direct ports of `KernelFunctions.pine` as expressed in the Python reference.
//! `powf`/`exp` are used so the floating-point results match libm (and therefore
//! the Python `**`/`math.exp` results) bit-for-bit.

/// Rational Quadratic kernel estimate at `bar_index`.
#[must_use]
pub fn kernel_rational_quadratic(
    src: &[f64],
    bar_index: usize,
    lookback: i64,
    relative_weight: f64,
    start_at_bar: i64,
) -> f64 {
    let mut current_weight = 0.0;
    let mut cumulative_weight = 0.0;
    let denom = ((lookback * lookback) as f64 * 2.0 * relative_weight).max(1e-10);
    let count = (1 + start_at_bar).min(bar_index as i64) + 1;
    for i in 0..count {
        let weight = (1.0 + (i * i) as f64 / denom).powf(-relative_weight);
        current_weight += src[bar_index - i as usize] * weight;
        cumulative_weight += weight;
    }
    if cumulative_weight > 0.0 {
        current_weight / cumulative_weight
    } else {
        src[bar_index]
    }
}

/// Gaussian kernel estimate at `bar_index`.
#[must_use]
pub fn kernel_gaussian(src: &[f64], bar_index: usize, lookback: i64, start_at_bar: i64) -> f64 {
    let mut current_weight = 0.0;
    let mut cumulative_weight = 0.0;
    let denom = (2.0 * (lookback * lookback) as f64).max(1e-10);
    let count = (1 + start_at_bar).min(bar_index as i64) + 1;
    for i in 0..count {
        let weight = (-((i * i) as f64) / denom).exp();
        current_weight += src[bar_index - i as usize] * weight;
        cumulative_weight += weight;
    }
    if cumulative_weight > 0.0 {
        current_weight / cumulative_weight
    } else {
        src[bar_index]
    }
}
