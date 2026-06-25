# PineScript Port

Repo-local copy of the PineScript reference, the algorithmic ground truth
that every other port in this repository is validated against.

## Files

| File | Contents |
| --- | --- |
| `lorentzian-classification-v2.pine` | The pinned `lcv6.pine` indicator source. |
| `archive/lorentzian-classification-v1-020823-2301.pine` | Archived original v1 indicator source. |
| `libraries/MLExtensions.pine` | Pinned library source mirrored by the ports' business logic. |
| `libraries/KernelFunctions.pine` | Pinned kernel library source mirrored by the ports' business logic. |

The SHA-256 hashes for these files are locked in
`tests/parity/fixtures_manifest.json`. The parity tests fail if the repo-local
copies drift from those manifest pins or regress back to placeholder content.

## Validation

From the repository root:

```bash
PYTHONPATH=ports/python python3 -m unittest tests.parity.test_python_port
```

The Python tests compare defaults, input coverage, alert/plot/table surfaces,
and the major business-logic surfaces against these manifest-pinned Pine
sources.
