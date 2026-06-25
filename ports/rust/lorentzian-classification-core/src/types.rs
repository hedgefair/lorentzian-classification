//! Core data types: bars, settings, feature specifications, and result rows.
//!
//! These mirror the dataclasses in the Python reference port (`core.py`) so the
//! two implementations stay structurally aligned and bit-for-bit comparable.

use std::fmt;

/// Sentinel for a missing value, equivalent to Pine `na` / Python `math.nan`.
pub const MISSING: f64 = f64::NAN;

/// Returns `true` when `value` represents a missing (`NaN`) observation.
#[inline]
#[must_use]
pub fn is_missing(value: f64) -> bool {
    value.is_nan()
}

/// Returns `fallback` when `value` is missing, otherwise `value`
/// (equivalent to Pine `nz`).
#[inline]
#[must_use]
pub fn nz(value: f64, fallback: f64) -> f64 {
    if is_missing(value) {
        fallback
    } else {
        value
    }
}

/// A single OHLC price bar. `time` is preserved verbatim from the input feed.
#[derive(Debug, Clone, PartialEq)]
pub struct Bar {
    pub time: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
}

/// The price series a feature or kernel reads from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Source {
    Open,
    High,
    Low,
    #[default]
    Close,
    Hl2,
    Hlc3,
    Ohlc4,
}

impl Source {
    /// Parses a Pine-style source name (case-insensitive).
    ///
    /// # Errors
    /// Returns [`ParseError`] when `name` is not a recognized source.
    pub fn parse(name: &str) -> Result<Self, ParseError> {
        match name.to_ascii_lowercase().as_str() {
            "open" => Ok(Self::Open),
            "high" => Ok(Self::High),
            "low" => Ok(Self::Low),
            "close" => Ok(Self::Close),
            "hl2" => Ok(Self::Hl2),
            "hlc3" => Ok(Self::Hlc3),
            "ohlc4" => Ok(Self::Ohlc4),
            other => Err(ParseError::UnsupportedSource(other.to_string())),
        }
    }
}

/// The kind of normalized feature computed for a feature slot.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FeatureKind {
    /// Normalized RSI of the close.
    Rsi,
    /// Normalized WaveTrend of hlc3.
    Wt,
    /// Normalized CCI of the close.
    Cci,
    /// Normalized ADX of high/low/close.
    Adx,
}

impl FeatureKind {
    /// Parses a Pine feature-kind token (case-insensitive).
    ///
    /// # Errors
    /// Returns [`ParseError`] when `name` is not a recognized feature kind.
    pub fn parse(name: &str) -> Result<Self, ParseError> {
        match name.to_ascii_uppercase().as_str() {
            "RSI" => Ok(Self::Rsi),
            "WT" => Ok(Self::Wt),
            "CCI" => Ok(Self::Cci),
            "ADX" => Ok(Self::Adx),
            other => Err(ParseError::UnsupportedFeature(other.to_string())),
        }
    }
}

/// A feature slot specification: kind plus the two Pine smoothing parameters.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FeatureSpec {
    pub kind: FeatureKind,
    pub param_a: i64,
    pub param_b: i64,
}

impl FeatureSpec {
    /// Convenience constructor.
    #[must_use]
    pub const fn new(kind: FeatureKind, param_a: i64, param_b: i64) -> Self {
        Self {
            kind,
            param_a,
            param_b,
        }
    }

    /// Parses a `KIND:a:b` specification string (e.g. `"CCI:34:2"`).
    ///
    /// # Errors
    /// Returns [`ParseError`] when the string is malformed.
    pub fn parse(spec: &str) -> Result<Self, ParseError> {
        let mut parts = spec.split(':');
        let kind = parts
            .next()
            .ok_or_else(|| ParseError::MalformedFeatureSpec(spec.to_string()))?;
        let a = parts
            .next()
            .ok_or_else(|| ParseError::MalformedFeatureSpec(spec.to_string()))?;
        let b = parts
            .next()
            .ok_or_else(|| ParseError::MalformedFeatureSpec(spec.to_string()))?;
        if parts.next().is_some() {
            return Err(ParseError::MalformedFeatureSpec(spec.to_string()));
        }
        let parse_int = |s: &str| {
            s.trim()
                .parse::<i64>()
                .map_err(|_| ParseError::MalformedFeatureSpec(spec.to_string()))
        };
        Ok(Self::new(
            FeatureKind::parse(kind.trim())?,
            parse_int(a)?,
            parse_int(b)?,
        ))
    }
}

/// Errors produced while parsing user-supplied configuration values.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    UnsupportedSource(String),
    UnsupportedFeature(String),
    MalformedFeatureSpec(String),
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnsupportedSource(s) => write!(f, "unsupported source: {s}"),
            Self::UnsupportedFeature(s) => write!(f, "unsupported feature type: {s}"),
            Self::MalformedFeatureSpec(s) => write!(f, "malformed feature spec: {s}"),
        }
    }
}

impl std::error::Error for ParseError {}

/// Indicator settings, mirroring the Pine `input.*` declarations and the
/// Python `Settings` dataclass defaults exactly.
#[derive(Debug, Clone, PartialEq)]
pub struct Settings {
    pub source: Source,
    pub neighbors_count: i64,
    pub max_bars_back: i64,
    pub feature_count: usize,
    pub color_compression: i64,
    pub include_full_history: bool,
    pub use_volatility_filter: bool,
    pub use_regime_filter: bool,
    pub use_adx_filter: bool,
    pub regime_threshold: f64,
    pub adx_threshold: i64,
    pub use_ema_filter: bool,
    pub ema_period: i64,
    pub use_sma_filter: bool,
    pub sma_period: i64,
    pub use_kernel_filter: bool,
    pub use_kernel_smoothing: bool,
    pub use_dynamic_exits: bool,
    pub show_exits: bool,
    pub use_worst_case: bool,
    pub kernel_h: i64,
    pub kernel_r: f64,
    pub kernel_x: i64,
    pub kernel_lag: i64,
    pub show_kernel_estimate: bool,
    pub show_bar_colors: bool,
    pub show_bar_predictions: bool,
    pub use_atr_offset: bool,
    pub bar_predictions_offset: f64,
    pub use_confidence_gradient: bool,
    pub show_trade_stats: bool,
    pub f1: FeatureSpec,
    pub f2: FeatureSpec,
    pub f3: FeatureSpec,
    pub f4: FeatureSpec,
    pub f5: FeatureSpec,
}

impl Default for Settings {
    fn default() -> Self {
        use FeatureKind::{Adx, Cci, Rsi, Wt};
        Self {
            source: Source::Close,
            neighbors_count: 8,
            max_bars_back: 2000,
            feature_count: 5,
            color_compression: 1,
            include_full_history: false,
            use_volatility_filter: true,
            use_regime_filter: true,
            use_adx_filter: false,
            regime_threshold: -0.1,
            adx_threshold: 20,
            use_ema_filter: false,
            ema_period: 200,
            use_sma_filter: false,
            sma_period: 200,
            use_kernel_filter: true,
            use_kernel_smoothing: false,
            use_dynamic_exits: false,
            show_exits: false,
            use_worst_case: false,
            kernel_h: 8,
            kernel_r: 8.0,
            kernel_x: 25,
            kernel_lag: 2,
            show_kernel_estimate: true,
            show_bar_colors: true,
            show_bar_predictions: true,
            use_atr_offset: true,
            bar_predictions_offset: 0.0,
            use_confidence_gradient: true,
            show_trade_stats: true,
            f1: FeatureSpec::new(Rsi, 14, 1),
            f2: FeatureSpec::new(Wt, 10, 11),
            f3: FeatureSpec::new(Cci, 20, 1),
            f4: FeatureSpec::new(Adx, 20, 2),
            f5: FeatureSpec::new(Rsi, 9, 1),
        }
    }
}

impl Settings {
    /// Returns the five feature slots in order.
    #[must_use]
    pub fn features(&self) -> [FeatureSpec; 5] {
        [self.f1, self.f2, self.f3, self.f4, self.f5]
    }
}

/// A fully computed per-bar result row, mirroring the Python `ResultRow`.
#[derive(Debug, Clone, PartialEq)]
pub struct ResultRow {
    pub bar: Bar,
    pub f1: f64,
    pub f2: f64,
    pub f3: f64,
    pub f4: f64,
    pub f5: f64,
    pub kernel: f64,
    pub prediction: i64,
    pub direction: i64,
    pub buy: bool,
    pub sell: bool,
    pub exit_buy: bool,
    pub exit_sell: bool,
    pub stop_buy: bool,
    pub stop_sell: bool,
    pub backtest_stream: i64,
    pub open_long_alert: bool,
    pub close_long_alert: bool,
    pub open_short_alert: bool,
    pub close_short_alert: bool,
    pub open_position_alert: bool,
    pub close_position_alert: bool,
    pub kernel_bullish_alert: bool,
    pub kernel_bearish_alert: bool,
    pub kernel_plot_color: String,
    pub prediction_label: String,
    pub prediction_label_y: f64,
    pub prediction_label_color: String,
    pub bar_color: String,
    pub trade_stats_visible: bool,
    pub trade_stats_header: String,
    pub total_wins: i64,
    pub total_losses: i64,
    pub total_early_signal_flips: i64,
    pub total_trades: i64,
    pub win_loss_ratio: f64,
    pub table_wl_ratio: f64,
    pub win_rate: f64,
}

/// Full output schema, identical to Python `RESULT_FIELDNAMES`.
pub const RESULT_FIELDNAMES: [&str; 40] = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "F1_RSI",
    "F2_WT",
    "F3_CCI",
    "F4_ADX",
    "F5_RSI9",
    "Kernel Regression Estimate",
    "Prediction",
    "Direction",
    "Buy",
    "Sell",
    "StopBuy",
    "StopSell",
    "Backtest Stream",
    "Open Long Alert",
    "Close Long Alert",
    "Open Short Alert",
    "Close Short Alert",
    "Open Position Alert",
    "Close Position Alert",
    "Kernel Bullish Alert",
    "Kernel Bearish Alert",
    "Kernel Plot Color",
    "Prediction Label",
    "Prediction Label Y",
    "Prediction Label Color",
    "Bar Color",
    "Trade Stats Visible",
    "Trade Stats Header",
    "Total Wins",
    "Total Losses",
    "Total Early Signal Flips",
    "Total Trades",
    "Win Loss Ratio",
    "Table WL Ratio",
    "Win Rate",
];
