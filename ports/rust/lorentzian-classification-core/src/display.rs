//! Pine-style color encoding for display outputs.
//!
//! Colors are represented as `"#RRGGBB@<transparency>"` strings, exactly as the
//! Python reference encodes them, so display columns compare byte-for-byte.

/// Encodes a hex color with a transparency value, e.g. `pine_color("#009988", 20)`.
#[must_use]
pub fn pine_color(hex_color: &str, transparency: i64) -> String {
    format!("{hex_color}@{transparency}")
}

/// Re-encodes an existing `"#RRGGBB@t"` color with a new transparency.
#[must_use]
pub fn color_with_transparency(color: &str, transparency: i64) -> String {
    let base = color.split('@').next().unwrap_or(color);
    pine_color(base, transparency)
}

const GREEN: &str = "#009988";
const RED: &str = "#CC3311";

/// Confidence-gradient transparency ladder shared by the green/red helpers.
fn gradient_transparency(prediction: f64) -> i64 {
    let scaled = prediction.abs().min(10.0);
    if scaled >= 9.0 {
        0
    } else if scaled >= 8.0 {
        10
    } else if scaled >= 7.0 {
        20
    } else if scaled >= 6.0 {
        30
    } else if scaled >= 5.0 {
        40
    } else if scaled >= 4.0 {
        50
    } else if scaled >= 3.0 {
        60
    } else if scaled >= 2.0 {
        70
    } else if scaled >= 1.0 {
        80
    } else {
        90
    }
}

/// Bullish prediction color, optionally shaded by confidence.
#[must_use]
pub fn prediction_green(prediction: f64, use_confidence_gradient: bool) -> String {
    if !use_confidence_gradient {
        return pine_color(GREEN, 0);
    }
    pine_color(GREEN, gradient_transparency(prediction))
}

/// Bearish prediction color, optionally shaded by confidence.
#[must_use]
pub fn prediction_red(prediction: f64, use_confidence_gradient: bool) -> String {
    if !use_confidence_gradient {
        return pine_color(RED, 0);
    }
    pine_color(RED, gradient_transparency(prediction))
}
