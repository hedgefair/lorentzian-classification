//! Output CSV writer.
//!
//! Byte-for-byte compatible with the Python reference `write_results`: identical
//! column order, `.8f` OHLC, `.16f` feature/kernel/stat floats (empty for NaN),
//! `"1"`/`""` booleans, empty cell for a zero backtest stream, and `\r\n` line
//! endings (Python's `csv.writer` default).

use std::fs::File;
use std::io::{self, BufWriter};
use std::path::Path;

use lorentzian_classification_core::{is_missing, ResultRow, RESULT_FIELDNAMES};

/// Writes the result rows to `path` as CSV.
///
/// # Errors
/// Returns any underlying I/O error from creating or writing the file.
pub fn write_results(path: &Path, rows: &[ResultRow]) -> io::Result<()> {
    let file = File::create(path)?;
    let mut out = csv::WriterBuilder::new()
        .terminator(csv::Terminator::CRLF)
        .from_writer(BufWriter::new(file));
    out.write_record(RESULT_FIELDNAMES)
        .map_err(csv_to_io_error)?;
    for row in rows {
        let fields = [
            sanitize_text(&row.bar.time),
            price(row.bar.open),
            price(row.bar.high),
            price(row.bar.low),
            price(row.bar.close),
            float(row.f1),
            float(row.f2),
            float(row.f3),
            float(row.f4),
            float(row.f5),
            float(row.kernel),
            row.prediction.to_string(),
            row.direction.to_string(),
            flag(row.buy),
            flag(row.sell),
            flag(row.stop_buy),
            flag(row.stop_sell),
            stream(row.backtest_stream),
            flag(row.open_long_alert),
            flag(row.close_long_alert),
            flag(row.open_short_alert),
            flag(row.close_short_alert),
            flag(row.open_position_alert),
            flag(row.close_position_alert),
            flag(row.kernel_bullish_alert),
            flag(row.kernel_bearish_alert),
            row.kernel_plot_color.clone(),
            row.prediction_label.clone(),
            float(row.prediction_label_y),
            row.prediction_label_color.clone(),
            row.bar_color.clone(),
            flag(row.trade_stats_visible),
            row.trade_stats_header.clone(),
            row.total_wins.to_string(),
            row.total_losses.to_string(),
            row.total_early_signal_flips.to_string(),
            row.total_trades.to_string(),
            float(row.win_loss_ratio),
            float(row.table_wl_ratio),
            float(row.win_rate),
        ];
        out.write_record(fields).map_err(csv_to_io_error)?;
    }
    out.flush()
}

fn csv_to_io_error(error: csv::Error) -> io::Error {
    io::Error::other(error)
}

/// Neutralizes spreadsheet formula injection (CWE-1236) in the only output
/// cell copied verbatim from input (the `time` passthrough). A leading
/// `= + - @` or control character is what a spreadsheet treats as a formula;
/// prefixing such a value with `'` makes it inert. Real timestamps never begin
/// with these, so this is a no-op for legitimate data and preserves parity.
fn sanitize_text(value: &str) -> String {
    match value.as_bytes().first() {
        Some(b'=' | b'+' | b'-' | b'@' | b'\t' | b'\r' | b'\n') => format!("'{value}"),
        _ => value.to_string(),
    }
}

/// OHLC price cell: Python `f"{x:.8f}"`.
fn price(value: f64) -> String {
    format!("{value:.8}")
}

/// Feature/kernel/stat float cell: empty for NaN, else Python `f"{x:.16f}"`.
fn float(value: f64) -> String {
    if is_missing(value) {
        String::new()
    } else {
        format!("{value:.16}")
    }
}

/// Boolean cell: Python writes `"1"` for true and `""` for false.
fn flag(value: bool) -> String {
    if value { "1" } else { "" }.to_string()
}

/// Backtest-stream cell: Python writes `""` for a zero (falsy) stream.
fn stream(value: i64) -> String {
    if value == 0 {
        String::new()
    } else {
        value.to_string()
    }
}
