//! The per-bar classification engine: features, kernels, kNN prediction,
//! filters, entries/exits, and trade statistics.
//!
//! Statement-for-statement port of `calculate` in the Python reference so the
//! full result series matches bit-for-bit.

use std::cmp::Ordering;

use crate::ann::AnnState;
use crate::display::{color_with_transparency, pine_color, prediction_green, prediction_red};
use crate::filters::calc_regime_filter;
use crate::indicators::{calc_atr, calc_feature, calc_raw_adx_filter, ema, sma};
use crate::kernels::{kernel_gaussian, kernel_rational_quadratic};
use crate::types::{is_missing, Bar, ResultRow, Settings, Source};

const TRADE_STATS_HEADER: &str = "\u{1f4c8} Trade Stats";

/// Projects each bar onto the configured price source series.
#[must_use]
pub fn select_source(bars: &[Bar], source: Source) -> Vec<f64> {
    bars.iter()
        .map(|b| match source {
            Source::Open => b.open,
            Source::High => b.high,
            Source::Low => b.low,
            Source::Close => b.close,
            Source::Hl2 => (b.high + b.low) / 2.0,
            Source::Hlc3 => (b.high + b.low + b.close) / 3.0,
            Source::Ohlc4 => (b.open + b.high + b.low + b.close) / 4.0,
        })
        .collect()
}

/// Runs the full Lorentzian Classification over `bars`.
///
/// `price_scale` is the decimal scale used for ADX quantization (`0.0` disables
/// quantization); see [`crate::csv_io::detect_price_scale`].
#[must_use]
#[allow(clippy::too_many_lines)]
pub fn calculate(bars: &[Bar], settings: &Settings, price_scale: f64) -> Vec<ResultRow> {
    let n = bars.len();
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let high: Vec<f64> = bars.iter().map(|b| b.high).collect();
    let low: Vec<f64> = bars.iter().map(|b| b.low).collect();
    let open_: Vec<f64> = bars.iter().map(|b| b.open).collect();
    let hlc3: Vec<f64> = (0..n)
        .map(|i| (high[i] + low[i] + close[i]) / 3.0)
        .collect();
    let ohlc4: Vec<f64> = (0..n)
        .map(|i| (open_[i] + high[i] + low[i] + close[i]) / 4.0)
        .collect();
    let src = select_source(bars, settings.source);

    let specs = settings.features();
    let features: Vec<Vec<f64>> = specs
        .iter()
        .map(|spec| calc_feature(*spec, &close, &high, &low, &hlc3, price_scale))
        .collect();

    let vol_atr1 = calc_atr(&high, &low, &close, 1);
    let vol_atr10 = calc_atr(&high, &low, &close, 10);
    let (reg_abs_slope, reg_ema_abs_slope) = calc_regime_filter(&ohlc4, &high, &low);
    let adx_filter = calc_raw_adx_filter(&high, &low, &close, 14);
    let ema_filter = ema(&close, settings.ema_period);
    let sma_filter = sma(&close, settings.sma_period);

    let mut yhat1 = vec![0.0; n];
    let mut yhat2 = vec![0.0; n];
    let mut ann = AnnState::new();
    let mut direction_buffer = vec![0_i64; n];
    let mut buy_buffer = vec![false; n];
    let mut sell_buffer = vec![false; n];
    let mut results: Vec<ResultRow> = Vec::with_capacity(n);

    let mut signal = 0_i64;
    let mut bars_held = 0_i64;
    let mut bars_since_start_long = 999_999_i64;
    let mut bars_since_start_short = 999_999_i64;
    let mut bars_since_alert_bull = 999_999_i64;
    let mut bars_since_alert_bear = 999_999_i64;
    let mut prev_signal_change = 0_i64;
    let mut prev_signal_change1 = 0_i64;
    let mut prev_signal_change2 = 0_i64;
    let mut prev_valid_long_exit = false;
    let mut prev_valid_short_exit = false;
    let mut start_long_price = 0.0_f64;
    let mut start_short_price = 0.0_f64;
    let mut total_wins = 0_i64;
    let mut total_losses = 0_i64;
    let mut total_early_flips = 0_i64;
    let last_bar_index = n as i64 - 1;
    let max_bars_back_index = if last_bar_index >= settings.max_bars_back {
        last_bar_index - settings.max_bars_back
    } else {
        0
    };

    for i in 0..n {
        let i_i64 = i as i64;
        // Pine: src[i-4] < src[i] ? -1 : src[i-4] > src[i] ? 1 : 0
        // (NaN comparisons fall through to 0, matching the reference.)
        let train_label = if i >= 4 {
            match src[i - 4].partial_cmp(&src[i]) {
                Some(Ordering::Less) => -1,
                Some(Ordering::Greater) => 1,
                _ => 0,
            }
        } else {
            0
        };
        let feature_values: [f64; 5] = [
            features[0][i],
            features[1][i],
            features[2][i],
            features[3][i],
            features[4][i],
        ];
        ann.push(&feature_values, train_label);

        yhat1[i] = kernel_rational_quadratic(
            &src,
            i,
            settings.kernel_h,
            settings.kernel_r,
            settings.kernel_x,
        );
        let lag_h = (settings.kernel_h - settings.kernel_lag).max(1);
        yhat2[i] = kernel_gaussian(&src, i, lag_h, settings.kernel_x);

        let prediction = if i_i64 >= max_bars_back_index {
            ann.run(&feature_values, settings, last_bar_index)
        } else {
            0
        };

        let mut filt_vol = true;
        if settings.use_volatility_filter && !is_missing(vol_atr1[i]) && !is_missing(vol_atr10[i]) {
            filt_vol = vol_atr1[i] > vol_atr10[i];
        }
        let mut filt_regime = true;
        if settings.use_regime_filter && reg_ema_abs_slope[i] != 0.0 {
            let norm_slope = (reg_abs_slope[i] - reg_ema_abs_slope[i]) / reg_ema_abs_slope[i];
            filt_regime = norm_slope >= settings.regime_threshold;
        }
        let filt_adx = if settings.use_adx_filter {
            adx_filter[i] > settings.adx_threshold as f64
        } else {
            true
        };
        let filter_all = filt_vol && filt_regime && filt_adx;

        let is_ema_up = if settings.use_ema_filter && !is_missing(ema_filter[i]) {
            close[i] > ema_filter[i]
        } else {
            !settings.use_ema_filter
        };
        let is_ema_down = if settings.use_ema_filter && !is_missing(ema_filter[i]) {
            close[i] < ema_filter[i]
        } else {
            !settings.use_ema_filter
        };
        let is_sma_up = if settings.use_sma_filter && !is_missing(sma_filter[i]) {
            close[i] > sma_filter[i]
        } else {
            !settings.use_sma_filter
        };
        let is_sma_down = if settings.use_sma_filter && !is_missing(sma_filter[i]) {
            close[i] < sma_filter[i]
        } else {
            !settings.use_sma_filter
        };

        let is_bullish_rate = i >= 2 && yhat1[i - 1] < yhat1[i];
        let is_bearish_rate = i >= 2 && yhat1[i - 1] > yhat1[i];
        let was_bullish_rate = i >= 3 && yhat1[i - 2] < yhat1[i - 1];
        let was_bearish_rate = i >= 3 && yhat1[i - 2] > yhat1[i - 1];
        let is_bullish_change = is_bullish_rate && was_bearish_rate;
        let is_bearish_change = is_bearish_rate && was_bullish_rate;
        let is_bullish_cross = i >= 1 && yhat2[i] >= yhat1[i] && yhat2[i - 1] < yhat1[i - 1];
        let is_bearish_cross = i >= 1 && yhat2[i] <= yhat1[i] && yhat2[i - 1] > yhat1[i - 1];
        let is_bullish_smooth = yhat2[i] >= yhat1[i];
        let is_bearish_smooth = yhat2[i] <= yhat1[i];
        let alert_bullish = if settings.use_kernel_smoothing {
            is_bullish_cross
        } else {
            is_bullish_change
        };
        let alert_bearish = if settings.use_kernel_smoothing {
            is_bearish_cross
        } else {
            is_bearish_change
        };
        let is_bullish = if settings.use_kernel_filter {
            if settings.use_kernel_smoothing {
                is_bullish_smooth
            } else {
                is_bullish_rate
            }
        } else {
            true
        };
        let is_bearish = if settings.use_kernel_filter {
            if settings.use_kernel_smoothing {
                is_bearish_smooth
            } else {
                is_bearish_rate
            }
        } else {
            true
        };

        let previous_signal = signal;
        if prediction > 0 && filter_all {
            signal = 1;
        } else if prediction < 0 && filter_all {
            signal = -1;
        }
        direction_buffer[i] = signal;

        let signal_change = signal - previous_signal;
        let is_diff_signal_type = signal_change != 0;
        let is_early_flip = is_diff_signal_type
            && (prev_signal_change != 0 || prev_signal_change1 != 0 || prev_signal_change2 != 0);
        prev_signal_change2 = prev_signal_change1;
        prev_signal_change1 = prev_signal_change;
        prev_signal_change = signal_change;

        if is_diff_signal_type {
            bars_held = 0;
        } else {
            bars_held += 1;
        }
        let is_held_four_bars = bars_held == 4;
        let is_held_less_than_four_bars = bars_held > 0 && bars_held < 4;

        let is_buy = signal == 1 && is_ema_up && is_sma_up;
        let is_sell = signal == -1 && is_ema_down && is_sma_down;
        let start_long = is_buy && is_diff_signal_type && is_bullish;
        let start_short = is_sell && is_diff_signal_type && is_bearish;

        let is_last_buy = i >= 4 && direction_buffer[i - 4] == 1;
        let is_last_sell = i >= 4 && direction_buffer[i - 4] == -1;

        if start_long {
            bars_since_start_long = 0;
        } else {
            bars_since_start_long += 1;
        }
        if start_short {
            bars_since_start_short = 0;
        } else {
            bars_since_start_short += 1;
        }
        if alert_bullish {
            bars_since_alert_bull = 0;
        } else {
            bars_since_alert_bull += 1;
        }
        if alert_bearish {
            bars_since_alert_bear = 0;
        } else {
            bars_since_alert_bear += 1;
        }

        let end_long_strict = ((is_held_four_bars && is_last_buy)
            || (is_held_less_than_four_bars && start_short && is_last_buy))
            && i >= 4
            && buy_buffer[i - 4];
        let end_short_strict = ((is_held_four_bars && is_last_sell)
            || (is_held_less_than_four_bars && start_long && is_last_sell))
            && i >= 4
            && sell_buffer[i - 4];
        let is_valid_long_exit = bars_since_alert_bear > bars_since_start_long;
        let is_valid_short_exit = bars_since_alert_bull > bars_since_start_short;
        let end_long_dynamic = is_bearish_change && prev_valid_long_exit;
        let end_short_dynamic = is_bullish_change && prev_valid_short_exit;
        let dynamic_valid =
            !settings.use_ema_filter && !settings.use_sma_filter && !settings.use_kernel_smoothing;
        let end_long = if settings.use_dynamic_exits && dynamic_valid {
            end_long_dynamic
        } else {
            end_long_strict
        };
        let end_short = if settings.use_dynamic_exits && dynamic_valid {
            end_short_dynamic
        } else {
            end_short_strict
        };

        buy_buffer[i] = start_long;
        sell_buffer[i] = start_short;
        prev_valid_long_exit = is_valid_long_exit;
        prev_valid_short_exit = is_valid_short_exit;

        let market_price = if settings.use_worst_case {
            src[i]
        } else {
            (high[i] + low[i] + open_[i] + open_[i]) / 4.0
        };
        if i_i64 > max_bars_back_index {
            let mut early_flips = 0_i64;
            let mut wins = 0_i64;
            let mut losses = 0_i64;
            if start_long {
                start_short_price = 0.0;
                early_flips = i64::from(is_early_flip);
                start_long_price = market_price;
            }
            if end_long {
                let delta = market_price - start_long_price;
                wins = i64::from(delta > 0.0);
                losses = i64::from(delta < 0.0);
            }
            if start_short {
                start_long_price = 0.0;
                start_short_price = market_price;
            }
            if end_short {
                if is_early_flip {
                    early_flips = 1;
                }
                let delta = start_short_price - market_price;
                if delta > 0.0 {
                    wins = 1;
                }
                if delta < 0.0 {
                    losses = 1;
                }
            }
            total_wins += wins;
            total_losses += losses;
            total_early_flips += early_flips;
        }

        let total_trades = total_wins + total_losses;
        let win_loss_ratio = if total_trades != 0 {
            total_wins as f64 / total_trades as f64
        } else {
            f64::NAN
        };
        let table_wl_ratio = if total_losses != 0 {
            total_wins as f64 / total_losses as f64
        } else {
            f64::NAN
        };
        let win_rate = if total_wins + total_losses != 0 {
            total_wins as f64 / (total_wins + total_losses) as f64
        } else {
            f64::NAN
        };
        let backtest_stream = if start_long {
            1
        } else if end_long {
            2
        } else if start_short {
            -1
        } else if end_short {
            -2
        } else {
            0
        };
        let stop_buy = end_long && settings.show_exits;
        let stop_sell = end_short && settings.show_exits;
        let c_green = pine_color("#009988", 20);
        let c_red = pine_color("#CC3311", 20);
        let transparent = pine_color("#000000", 100);
        let kernel_bullish = if settings.use_kernel_smoothing {
            is_bullish_smooth
        } else {
            is_bullish_rate
        };
        let kernel_plot_color = if settings.show_kernel_estimate {
            if kernel_bullish {
                c_green
            } else {
                c_red
            }
        } else {
            transparent
        };
        let neutral_color = pine_color("#787b86", 25);
        let prediction_color = match prediction.cmp(&0) {
            Ordering::Greater => {
                prediction_green(prediction as f64, settings.use_confidence_gradient)
            }
            Ordering::Less => prediction_red(-prediction as f64, settings.use_confidence_gradient),
            Ordering::Equal => neutral_color,
        };
        let prediction_label_color = if settings.show_bar_predictions {
            prediction_color.clone()
        } else {
            String::new()
        };
        let bar_color = if settings.show_bar_colors {
            color_with_transparency(
                &prediction_color,
                if settings.use_confidence_gradient {
                    50
                } else {
                    30
                },
            )
        } else {
            String::new()
        };
        let prediction_label_y = if settings.use_atr_offset {
            if prediction > 0 {
                high[i] + vol_atr1[i]
            } else {
                low[i] - vol_atr1[i]
            }
        } else {
            let hl2 = (high[i] + low[i]) / 2.0;
            if prediction > 0 {
                high[i] + hl2 * settings.bar_predictions_offset / 20.0
            } else {
                low[i] - hl2 * settings.bar_predictions_offset / 30.0
            }
        };

        results.push(ResultRow {
            bar: bars[i].clone(),
            f1: features[0][i],
            f2: features[1][i],
            f3: features[2][i],
            f4: features[3][i],
            f5: features[4][i],
            kernel: yhat1[i],
            prediction,
            direction: signal,
            buy: start_long,
            sell: start_short,
            exit_buy: end_long,
            exit_sell: end_short,
            stop_buy,
            stop_sell,
            backtest_stream,
            open_long_alert: start_long,
            close_long_alert: end_long,
            open_short_alert: start_short,
            close_short_alert: end_short,
            open_position_alert: start_long || start_short,
            close_position_alert: end_long || end_short,
            kernel_bullish_alert: alert_bullish,
            kernel_bearish_alert: alert_bearish,
            kernel_plot_color,
            prediction_label: prediction.to_string(),
            prediction_label_y,
            prediction_label_color,
            bar_color,
            trade_stats_visible: settings.show_trade_stats,
            trade_stats_header: TRADE_STATS_HEADER.to_string(),
            total_wins,
            total_losses,
            total_early_signal_flips: total_early_flips,
            total_trades,
            win_loss_ratio,
            table_wl_ratio,
            win_rate,
        });
    }

    results
}
