//! Command-line interface for the Rust Lorentzian Classification port.
//!
//! Two subcommands, mirroring the essential Python CLI verbs:
//!
//! * `run <input.csv> <output.csv>` — compute the full result series and write
//!   the 40-column output CSV.
//! * `parity <pine_export.csv>` — recompute from the export's OHLC and compare
//!   against the export's own feature/kernel/prediction/signal columns.
//!
//! The CLI has no third-party dependencies; arguments are parsed by hand to keep
//! the port dependency-light, matching the stdlib-only Python reference.

use std::path::Path;
use std::process::ExitCode;

use lorentzian_classification_core::{
    calculate, parity_summary, read_pine_export, read_tradingview_csv, Settings,
};

mod writer;

const USAGE: &str = "\
Lorentzian Classification (Rust port)

USAGE:
    lorentzian-classification <COMMAND> [OPTIONS]

COMMANDS:
    run     <input.csv> <output.csv>   Compute results and write the output CSV
    parity  <pine_export.csv>          Compare a recompute against a Pine export

OPTIONS (run, parity):
    --include-full-history             Score from the first bar (no max-bars-back warmup)
    --max-bars-back <N>                Override max bars back (default 2000)

OPTIONS (parity):
    --tolerance <T>                    Numeric tolerance for features/kernel (default 1e-6)

    -h, --help                         Print this help
    -V, --version                      Print version
";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match run(&args) {
        Ok(code) => code,
        Err(message) => {
            eprintln!("error: {message}");
            ExitCode::FAILURE
        }
    }
}

fn run(args: &[String]) -> Result<ExitCode, String> {
    let Some(command) = args.first() else {
        eprint!("{USAGE}");
        return Ok(ExitCode::FAILURE);
    };

    match command.as_str() {
        "-h" | "--help" | "help" => {
            print!("{USAGE}");
            Ok(ExitCode::SUCCESS)
        }
        "-V" | "--version" => {
            println!("lorentzian-classification {}", env!("CARGO_PKG_VERSION"));
            Ok(ExitCode::SUCCESS)
        }
        "run" => cmd_run(&args[1..]),
        "parity" => cmd_parity(&args[1..]),
        other => Err(format!("unknown command {other:?}; try --help")),
    }
}

/// Parsed options shared by the subcommands.
struct Options {
    positionals: Vec<String>,
    settings: Settings,
    tolerance: f64,
}

fn parse_options(args: &[String]) -> Result<Options, String> {
    let mut settings = Settings::default();
    let mut tolerance = 1e-6;
    let mut positionals = Vec::new();
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--include-full-history" => settings.include_full_history = true,
            "--max-bars-back" => {
                let value = iter.next().ok_or("--max-bars-back requires a value")?;
                settings.max_bars_back = value
                    .parse()
                    .map_err(|_| format!("invalid --max-bars-back: {value}"))?;
            }
            "--tolerance" => {
                let value = iter.next().ok_or("--tolerance requires a value")?;
                tolerance = value
                    .parse()
                    .map_err(|_| format!("invalid --tolerance: {value}"))?;
            }
            other if other.starts_with("--") => {
                return Err(format!("unknown option {other:?}"));
            }
            other => positionals.push(other.to_string()),
        }
    }
    Ok(Options {
        positionals,
        settings,
        tolerance,
    })
}

fn cmd_run(args: &[String]) -> Result<ExitCode, String> {
    let opts = parse_options(args)?;
    let [input, output] = opts.positionals.as_slice() else {
        return Err("run requires <input.csv> <output.csv>".to_string());
    };
    let (bars, price_scale) =
        read_tradingview_csv(Path::new(input)).map_err(|e| format!("reading {input}: {e}"))?;
    let results = calculate(&bars, &opts.settings, price_scale);
    writer::write_results(Path::new(output), &results)
        .map_err(|e| format!("writing {output}: {e}"))?;
    println!("wrote {} rows to {output}", results.len());
    Ok(ExitCode::SUCCESS)
}

fn cmd_parity(args: &[String]) -> Result<ExitCode, String> {
    let opts = parse_options(args)?;
    let [input] = opts.positionals.as_slice() else {
        return Err("parity requires <pine_export.csv>".to_string());
    };
    let (bars, expected, price_scale) =
        read_pine_export(Path::new(input)).map_err(|e| format!("reading {input}: {e}"))?;
    let results = calculate(&bars, &opts.settings, price_scale);
    let summary = parity_summary(&expected, &results, opts.tolerance, &opts.settings);

    println!("rows: {}", expected.len());
    println!("compared from index: {}", summary.max_bars_back_index);
    println!("compared rows: {}", summary.compared);
    println!("max feature diff: {:e}", summary.max_feature_diff);
    println!("max kernel diff:  {:e}", summary.max_kernel_diff);
    if summary.pass {
        println!("PARITY: PASS (tolerance {:e})", opts.tolerance);
        Ok(ExitCode::SUCCESS)
    } else {
        println!("PARITY: FAIL ({} mismatches)", summary.mismatches.len());
        for m in summary.mismatches.iter().take(10) {
            println!(
                "  [{}] {}: expected {} got {}",
                m.index, m.column, m.expected, m.actual
            );
        }
        Ok(ExitCode::FAILURE)
    }
}
