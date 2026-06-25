<div align="center">

<h1>Lorentzian Classification</h1>

<p>
  <strong>The <a href="https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/">Lorentzian Classification</a> indicator by <a href="https://www.tradingview.com/u/jdehorty/">Justin Dehorty</a>,<br>ported to Python, Rust, and Lean 4.</strong>
</p>

<!-- tradingview-badges:start -->
<p>
  <a href="https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/"><img alt="TradingView Editors' Picks" src="https://img.shields.io/badge/TradingView-Editors%27%20Picks-2962FF?style=for-the-badge&amp;logo=tradingview&amp;logoColor=white&amp;labelColor=4A4A4A"></a>
</p>
<p>
  <a href="https://www.tradingview.com/chart/BTCUSD/LYCOEW6Z-TradingView-Community-Awards-2023/"><img alt="Community Awards: Most Valuable PineScript 2023" src="https://img.shields.io/badge/Community%20Awards-Most%20Valuable%20PineScript%20%282023%29-2962FF?style=flat-square&amp;logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xOSA1aC0yVjNIN3YySDVjLTEuMSAwLTIgLjktMiAydjFjMCAyLjU1IDEuOTIgNC42MyA0LjM5IDQuOTQuNjMgMS41IDEuOTggMi42MyAzLjYxIDIuOTZWMTlIN3YyaDEwdi0yaC00di0zLjFjMS42My0uMzMgMi45OC0xLjQ2IDMuNjEtMi45NkMxOS4wOCAxMi42MyAyMSAxMC41NSAyMSA4VjdjMC0xLjEtLjktMi0yLTJ6TTUgOFY3aDJ2My44MkM1Ljg0IDEwLjQgNSA5LjMgNSA4em0xNCAwYzAgMS4zLS44NCAyLjQtMiAyLjgyVjdoMnYxeiIvPjwvc3ZnPg%3D%3D&amp;logoColor=white&amp;labelColor=4A4A4A"></a>
</p>
<p>
  <a href="https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/"><img alt="1,149,546 TradingView views" src="https://img.shields.io/badge/Views-1.15M-2962FF?style=flat-square&amp;logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xMiA0LjVDNyA0LjUgMi43MyA3LjYxIDEgMTJjMS43MyA0LjM5IDYgNy41IDExIDcuNXM5LjI3LTMuMTEgMTEtNy41Yy0xLjczLTQuMzktNi03LjUtMTEtNy41ek0xMiAxN2MtMi43NiAwLTUtMi4yNC01LTVzMi4yNC01IDUtNSA1IDIuMjQgNSA1LTIuMjQgNS01IDV6bTAtOGMtMS42NiAwLTMgMS4zNC0zIDNzMS4zNCAzIDMgMyAzLTEuMzQgMy0zLTEuMzQtMy0zLTN6Ii8%2BPC9zdmc%2B&amp;logoColor=white&amp;labelColor=4A4A4A"></a>
  <a href="https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/"><img alt="33,931 TradingView boosts" src="https://img.shields.io/badge/Boosts-33.9K-2962FF?style=flat-square&amp;logo=rocket&amp;logoColor=white&amp;labelColor=4A4A4A"></a>
  <a href="https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/"><img alt="806 TradingView comments" src="https://img.shields.io/badge/Comments-806-2962FF?style=flat-square&amp;logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0yMCAySDRjLTEuMSAwLTIgLjktMiAydjE4bDQtNGgxNGMxLjEgMCAyLS45IDItMlY0YzAtMS4xLS45LTItMi0yeiIvPjwvc3ZnPg%3D%3D&amp;logoColor=white&amp;labelColor=4A4A4A"></a>
</p>
<!-- tradingview-badges:end -->

<p>
  <a href="ports/pinescript/"><img alt="PineScript v6 port" src="https://img.shields.io/badge/PineScript%20v6-%E2%9C%93-2E7D32?style=flat-square&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xMiAyTDE1IDdMMTMuNSA3TDE3IDEyTDE1IDEyTDE5IDE4TDEzLjUgMThMMTMuNSAyMkwxMC41IDIyTDEwLjUgMThMNSAxOEw5IDEyTDcgMTJMMTAuNSA3TDkgN1oiLz48L3N2Zz4%3D&logoColor=white&labelColor=4A4A4A"></a>
  <a href="ports/python/"><img alt="Python port" src="https://img.shields.io/badge/Python-%E2%9C%93-2E7D32?style=flat-square&logo=python&logoColor=white&labelColor=4A4A4A"></a>
  <a href="ports/rust/"><img alt="Rust port" src="https://img.shields.io/badge/Rust-%E2%9C%93-2E7D32?style=flat-square&logo=rust&logoColor=white&labelColor=4A4A4A"></a>
  <a href="ports/lean/"><img alt="Lean 4 port" src="https://img.shields.io/badge/Lean%204-%E2%9C%93-2E7D32?style=flat-square&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNTYgMjU2IiBmaWxsPSJ3aGl0ZSI%2BPHBhdGggZD0iTTU1IDQ0aDEwdjY0aDM4djEwSDU1Wk0xNTEgNDRoNDh2MTBoLTQ4Wk0xNjEgNzRoMzh2MTBoLTM4Wk0xNTEgMTEyaDQ4djEwaC00OFpNMTg5IDQ0aDEwdjc4aC0xMFpNNTUgMTM5aDEwbDIyIDcyaC0xMFpNMTA5IDEzOWgxMGwtMjIgNzJoLTEwWk02MyAxNjZoNDJ2OUg2M1pNMTQzIDEzOWgxMHY3MmgtMTBaTTE5MCAxMzloMTB2NzJoLTEwWk0xNTIgMTM5aDhsMzggNzJoLTlaIi8%2BPC9zdmc%2B&logoColor=white&labelColor=4A4A4A"></a>
</p>

<p><em>More languages coming soon.</em></p>

</div>

[![Machine Learning: Lorentzian Classification on TradingView](docs/assets/lorentzian-classification-chart.png)](https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/)

<p align="center"><em><a href="https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/">View live on TradingView →</a></em></p>

## Overview

Lorentzian Classification is an open-source TradingView indicator: a
**nearest-neighbor-based classifier** (it labels the current bar by looking at
the historical bars it most resembles) that uses **Lorentzian distance** as the
measure of resemblance to model price movement. It is a classifier, not deep
learning and not an autonomous trading agent.

- **Want to USE it?** Add the original indicator on TradingView:
  [Machine Learning: Lorentzian Classification](https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/).
  The [settings reference](https://ai-edge.io/docs/indicators/lorentzian-classification/general-settings)
  explains every input.
- **Want to BUILD ON it?** This repo has the same algorithm re-implemented in
  Python, Rust, and Lean 4, each verified to produce bit-for-bit identical
  results to the original. Jump to [Quick start](#quick-start).

A *port* is a reimplementation of a program in a different programming language
or for a different platform. The Python, Rust, and Lean 4 ports under
[`ports/`](ports/) each reproduce the original PineScript indicator's algorithm,
alongside the pinned PineScript source this repo keeps for review and parity
testing.

## Quick start

No accounts, API keys, or external data needed. This runs on a Coinbase
BTC/USD daily history committed to the repo and needs only Python 3 (standard
library, nothing to `pip install`). From the repository root, compute the full
result series (features, kernel estimate, prediction, direction, and
buy/sell/exit signals):

```bash
PYTHONPATH=ports/python python3 -m lorentzian_classification run \
  tests/parity/baselines/pine_coinbase_btcusd_1d_limited_history.csv \
  --output /tmp/btcusd_daily_signals.csv
```

View the most recent signals:

```bash
column -s, -t < /tmp/btcusd_daily_signals.csv | tail -5
```

For Rust and Lean 4 runs, library usage, and a step-by-step parity proof, see
[`docs/examples.md`](docs/examples.md).

## Quick links

| Need | Link |
| --- | --- |
| Use the original indicator | [TradingView Editors' Picks](https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/) |
| Read the settings reference | [ai-edge.io/docs](https://ai-edge.io/docs/indicators/lorentzian-classification/general-settings) |
| Explore optimizer studies | [AI Edge Optimizer](https://optimizer.ai-edge.io/studies) |
| Reproduce validation | [`docs/validation.md`](docs/validation.md) |
| Run the examples | [`docs/examples.md`](docs/examples.md) |

## What is in this repo

| Path | What it is |
| --- | --- |
| [`ports/pinescript/`](ports/pinescript/) | The SHA-pinned original TradingView indicator that everything else is checked against |
| [`ports/python/`](ports/python/) | Python CLI and library port; reproduces the gold baselines (the TradingView CSV exports we treat as ground truth) with exact signals and feature/kernel tolerance |
| [`ports/rust/`](ports/rust/) | Rust core library and CLI; bit-exact with the Python port |
| [`ports/lean/`](ports/lean/) | Lean 4 executable formal specification with proved structural invariants; byte-identical output to the Rust port |
| [`docs/`](docs/) | Validation policy and copy-pasteable cross-port usage examples ([`docs/examples.md`](docs/examples.md)) |
| [`tests/`](tests/) | Parity fixtures, gold baselines, and the cross-port parity harness (`tests/parity/cross_port_parity.sh`) |

## Parity and validation

The Python port passes every tracked gold baseline with zero signal mismatches
but is not yet release-ready pending broader non-default coverage. See
[`docs/validation.md`](docs/validation.md) for how parity is defined and
reproduced locally, and
[`tests/parity/python_port_coverage.md`](tests/parity/python_port_coverage.md)
for the current coverage matrix.

## Disclaimer

Lorentzian Classification is impersonal indicator software: it surfaces
classification signals computed from historical market data, and how you act on
them is your decision. It is a tool for knowledgeable traders, not a system
that replaces skill or judgment, and it does not provide financial advice or
personalized recommendations. Past performance does not guarantee future
results.

## License

Released under the [MIT License](LICENSE.md) — use it freely, just keep the
copyright notice. The only exception is the PineScript reference indicator and
its libraries under `ports/pinescript/`, which retain their original MPL-2.0
headers.
