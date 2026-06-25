#!/usr/bin/env bash
# Cross-port parity harness: prove the Rust, Python, and Lean ports produce
# identical output on the same inputs, across the FULL 40-column result schema
# (including backtest stream, alerts, colors, and trade stats that the Pine
# gold baselines do not contain).
#
# For each baseline it runs all three CLIs and diffs their outputs with the
# implementation-independent tests/parity/compare_csv.py comparator.
#
# Usage:  tests/parity/cross_port_parity.sh
# Env:    PYTHON (default python3), TOLERANCE (default 1e-9)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PYTHON:-python3}"
TOLERANCE="${TOLERANCE:-1e-9}"
BASELINES="$REPO_ROOT/tests/parity/baselines"
RUST_DIR="$REPO_ROOT/ports/rust"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> Building Rust CLI (release)"
cargo build --release --manifest-path "$RUST_DIR/Cargo.toml" -p lorentzian-classification-cli >/dev/null
RUST_BIN="$RUST_DIR/target/release/lorentzian-classification"

LEAN_DIR="$REPO_ROOT/ports/lean"
echo "==> Building Lean CLI"
(cd "$LEAN_DIR" && lake build lorentzian-classification >/dev/null)
LEAN_BIN="$LEAN_DIR/.lake/build/bin/lorentzian-classification"

# fixture filename | extra flags
FIXTURES=(
  "pine_oanda_eurusd_1d_full_history.csv|--include-full-history"
  "pine_tastyfx_eurusd_1d_full_history.csv|--include-full-history"
  "pine_coinbase_btcusd_1d_limited_history.csv|"
  "pine_btcusd_h1_trimmed_limited_history.csv|"
)

status=0
for entry in "${FIXTURES[@]}"; do
  fixture="${entry%%|*}"
  flags="${entry##*|}"
  input="$BASELINES/$fixture"
  py_out="$WORK/py.csv"
  rs_out="$WORK/rs.csv"
  lean_out="$WORK/lean.csv"

  echo ""
  echo "==> $fixture (flags: ${flags:-none})"

  # Python's `run` takes the output via -o; the Rust CLI takes it positionally.
  # shellcheck disable=SC2086
  PYTHONPATH="$REPO_ROOT/ports/python" "$PYTHON" -m lorentzian_classification run \
    "$input" -o "$py_out" $flags

  # shellcheck disable=SC2086
  "$RUST_BIN" run "$input" "$rs_out" $flags

  # shellcheck disable=SC2086
  "$LEAN_BIN" run "$input" "$lean_out" $flags >/dev/null

  if "$PYTHON" "$REPO_ROOT/tests/parity/compare_csv.py" "$py_out" "$rs_out" --tolerance "$TOLERANCE"; then
    echo "    OK: Rust == Python for $fixture"
  else
    echo "    FAIL: Rust != Python for $fixture"
    status=1
  fi

  if "$PYTHON" "$REPO_ROOT/tests/parity/compare_csv.py" "$py_out" "$lean_out" --tolerance "$TOLERANCE"; then
    echo "    OK: Lean == Python for $fixture"
  else
    echo "    FAIL: Lean != Python for $fixture"
    status=1
  fi

  if "$PYTHON" "$REPO_ROOT/tests/parity/compare_csv.py" "$rs_out" "$lean_out" --tolerance "$TOLERANCE"; then
    echo "    OK: Lean == Rust for $fixture"
  else
    echo "    FAIL: Lean != Rust for $fixture"
    status=1
  fi
done

echo ""
if [ "$status" -eq 0 ]; then
  echo "ALL CROSS-PORT CHECKS PASSED"
else
  echo "CROSS-PORT CHECKS FAILED"
fi
exit "$status"
