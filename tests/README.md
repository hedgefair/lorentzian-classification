# Tests

Cross-port tests and parity artifacts.

Tests prefer exported data and deterministic comparisons over screenshots.
Screenshots are used only when they clarify visual behavior that exported data
cannot capture.

## Python Parity

[`tests/parity/`](parity/README.md) contains the Python CLI parity suite. It
validates the Python business logic against manifest-pinned PineScript sources,
compares the currently available TradingView/Pine export CSVs, inventories the
required non-default exports that are still missing, and reports release
readiness through the Python CLI.

Run from the repository root:

```bash
PYTHONPATH=ports/python python3 -m unittest discover -s tests
PYTHONPATH=ports/python python3 -m lorentzian_classification readiness
```

Parity fixture lookup is shared with the CLI: `--fixture-dir` overrides
`LORENTZIAN_PARITY_FIXTURE_DIR`, which overrides the default repo-local
baselines under `tests/parity/baselines/`.
