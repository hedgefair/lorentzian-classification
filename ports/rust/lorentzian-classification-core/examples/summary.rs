//! Minimal end-to-end example: read a TradingView CSV, run the classifier with
//! default settings, and print a short summary of the last bar.
//!
//! Run with:
//! ```text
//! cargo run --example summary -p lorentzian-classification-core -- \
//!   "../../tests/parity/baselines/pine_coinbase_btcusd_1d_limited_history.csv"
//! ```

use std::path::Path;
use std::process::ExitCode;

use lorentzian_classification_core::{calculate, read_tradingview_csv, Settings};

fn main() -> ExitCode {
    let Some(input) = std::env::args().nth(1) else {
        eprintln!("usage: summary <tradingview_export.csv>");
        return ExitCode::FAILURE;
    };

    let (bars, price_scale) = match read_tradingview_csv(Path::new(&input)) {
        Ok(parsed) => parsed,
        Err(err) => {
            eprintln!("error reading {input}: {err}");
            return ExitCode::FAILURE;
        }
    };

    let rows = calculate(&bars, &Settings::default(), price_scale);
    let Some(last) = rows.last() else {
        eprintln!("no rows produced (empty input?)");
        return ExitCode::FAILURE;
    };

    println!("bars:            {}", rows.len());
    println!("last time:       {}", last.bar.time);
    println!("last close:      {}", last.bar.close);
    println!("prediction:      {}", last.prediction);
    println!("direction:       {}", last.direction);
    println!("kernel estimate: {:.6}", last.kernel);
    println!(
        "trades: {} (wins {}, losses {})",
        last.total_trades, last.total_wins, last.total_losses
    );
    ExitCode::SUCCESS
}
