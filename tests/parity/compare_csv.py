#!/usr/bin/env python3
"""Generic, implementation-independent CSV comparator.

Compares two CSV files cell-by-cell:

* numeric cells must agree within ``--tolerance`` (default 1e-9),
* empty/NaN cells must both be empty/NaN,
* every other cell must match exactly.

This script depends only on the standard library and does NOT import either port,
so using it to diff the Rust and Python outputs is not circular.

Exit status is 0 on full agreement, 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys


def is_blank(value: str) -> bool:
    return value.strip().lower() in ("", "na", "nan")


def as_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two CSV files numerically.")
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--tolerance", type=float, default=1e-9)
    parser.add_argument("--max-report", type=int, default=20)
    args = parser.parse_args(argv)

    with open(args.left, newline="") as handle:
        left = list(csv.reader(handle))
    with open(args.right, newline="") as handle:
        right = list(csv.reader(handle))

    mismatches: list[str] = []

    if left and right and left[0] != right[0]:
        mismatches.append(f"header differs:\n  L: {left[0]}\n  R: {right[0]}")

    if len(left) != len(right):
        mismatches.append(f"row count differs: left={len(left)} right={len(right)}")

    max_diff = 0.0
    compared_cells = 0
    for row_idx, (lrow, rrow) in enumerate(zip(left, right)):
        if len(lrow) != len(rrow):
            mismatches.append(f"row {row_idx}: column count differs ({len(lrow)} vs {len(rrow)})")
            continue
        header = left[0] if left else []
        for col_idx, (lcell, rcell) in enumerate(zip(lrow, rrow)):
            compared_cells += 1
            colname = header[col_idx] if col_idx < len(header) else str(col_idx)
            if is_blank(lcell) and is_blank(rcell):
                continue
            lf, rf = as_float(lcell), as_float(rcell)
            if lf is not None and rf is not None:
                if math.isnan(lf) and math.isnan(rf):
                    continue
                diff = abs(lf - rf)
                max_diff = max(max_diff, diff)
                if diff > args.tolerance:
                    mismatches.append(
                        f"row {row_idx} col '{colname}': {lcell!r} != {rcell!r} (diff={diff:g})"
                    )
                continue
            if lcell != rcell:
                mismatches.append(f"row {row_idx} col '{colname}': {lcell!r} != {rcell!r}")

    print(f"compared {compared_cells} cells across {min(len(left), len(right))} rows")
    print(f"max numeric diff: {max_diff:g} (tolerance {args.tolerance:g})")
    if mismatches:
        print(f"MISMATCH: {len(mismatches)} differences")
        for line in mismatches[: args.max_report]:
            print("  " + line)
        return 1
    print("MATCH: files are equal within tolerance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
