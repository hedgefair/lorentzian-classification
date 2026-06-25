//! Criterion benchmark for the end-to-end `calculate` pipeline.
//!
//! Run with `cargo bench`. Uses the committed COINBASE daily baseline when
//! available, otherwise falls back to a synthetic series so the bench is
//! self-contained.

use std::hint::black_box;
use std::path::Path;

use criterion::{criterion_group, criterion_main, Criterion};
use lorentzian_classification_core::{calculate, read_pine_export, Bar, Settings};

fn load_bars() -> Vec<Bar> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .map(|root| {
            root.join("tests/parity/baselines/pine_coinbase_btcusd_1d_limited_history.csv")
        });
    if let Some(path) = path {
        if let Ok((bars, _expected, _scale)) = read_pine_export(&path) {
            return bars;
        }
    }
    // Synthetic fallback: a gently trending, oscillating series.
    (0..2_000)
        .map(|i| {
            let t = f64::from(i);
            let base = 100.0 + t * 0.05 + (t / 7.0).sin() * 5.0;
            Bar {
                time: i.to_string(),
                open: base,
                high: base + 1.5,
                low: base - 1.5,
                close: base + (t / 3.0).cos(),
            }
        })
        .collect()
}

fn bench_calculate(c: &mut Criterion) {
    let bars = load_bars();
    let settings = Settings::default();

    let mut group = c.benchmark_group("calculate");
    group.bench_function("default_limited_history", |b| {
        b.iter(|| calculate(black_box(&bars), black_box(&settings), 0.0));
    });
    group.bench_function("full_history", |b| {
        let full = Settings {
            include_full_history: true,
            ..Settings::default()
        };
        b.iter(|| calculate(black_box(&bars), black_box(&full), 0.0));
    });
    group.finish();
}

criterion_group!(benches, bench_calculate);
criterion_main!(benches);
