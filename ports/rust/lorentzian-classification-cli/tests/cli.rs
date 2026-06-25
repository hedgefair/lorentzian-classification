use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn temp_path(name: &str) -> PathBuf {
    std::env::temp_dir().join(format!(
        "lorentzian-rust-cli-{}-{name}.csv",
        std::process::id()
    ))
}

#[test]
fn run_accepts_quoted_csv_and_writes_full_schema() {
    let input = temp_path("input");
    let output = temp_path("output");
    fs::write(
        &input,
        "time,open,high,low,close\n\"2026-01-01, 00:00\",1.2345,1.2400,1.2300,1.2350\n",
    )
    .unwrap();

    let status = Command::new(env!("CARGO_BIN_EXE_lorentzian-classification"))
        .args(["run", input.to_str().unwrap(), output.to_str().unwrap()])
        .status()
        .unwrap();

    assert!(status.success());
    let content = fs::read_to_string(&output).unwrap();
    fs::remove_file(&input).unwrap();
    fs::remove_file(&output).unwrap();

    let mut lines = content.lines();
    let header = lines.next().unwrap();
    assert_eq!(header.split(',').count(), 40);
    assert!(header.starts_with("time,open,high,low,close,F1_RSI"));

    let row = lines.next().unwrap();
    assert!(
        row.starts_with("\"2026-01-01, 00:00\",1.23450000,1.24000000,1.23000000,1.23500000"),
        "unexpected first output row: {row}"
    );
}

#[test]
fn run_neutralizes_formula_injection_in_time_cell() {
    let input = temp_path("inject-input");
    let output = temp_path("inject-output");
    fs::write(
        &input,
        "time,open,high,low,close\n=cmd|'/c calc'!A1,1.0,1.1,0.9,1.05\n2,1.0,1.1,0.9,1.05\n",
    )
    .unwrap();

    let status = Command::new(env!("CARGO_BIN_EXE_lorentzian-classification"))
        .args(["run", input.to_str().unwrap(), output.to_str().unwrap()])
        .status()
        .unwrap();
    assert!(status.success());

    let content = fs::read_to_string(&output).unwrap();
    fs::remove_file(&input).unwrap();
    fs::remove_file(&output).unwrap();

    let first_data_row = content.lines().nth(1).unwrap();
    // The leading '=' must be defused so a spreadsheet treats it as text.
    assert!(
        first_data_row.starts_with("\"'=cmd") || first_data_row.starts_with("'=cmd"),
        "formula prefix not neutralized: {first_data_row}"
    );
}

#[test]
fn run_does_not_panic_on_pathological_decimal_precision() {
    // A cell with >=19 significant fractional digits previously overflowed
    // `10_i64.pow(..)` in detect_price_scale (debug panic / release wrap).
    let input = temp_path("precision-input");
    let output = temp_path("precision-output");
    fs::write(
        &input,
        "time,open,high,low,close\n1,1.0,1.0,1.0,0.0000000000000000001\n2,1.0,1.0,1.0,1.0\n",
    )
    .unwrap();

    let status = Command::new(env!("CARGO_BIN_EXE_lorentzian-classification"))
        .args(["run", input.to_str().unwrap(), output.to_str().unwrap()])
        .status()
        .unwrap();

    fs::remove_file(&input).unwrap();
    let _ = fs::remove_file(&output);
    assert!(status.success(), "CLI aborted on high-precision input");
}
