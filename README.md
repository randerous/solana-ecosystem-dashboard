# Solana Ecosystem Auto-Updating Report & Interactive Dashboard

A zero-dependency, API-key-free system that continuously collects the state of the Solana
ecosystem from public sources and renders it as:

- **Interactive dark-theme HTML dashboard** (`index.html`)
- **Human-readable Markdown report** (`report.md`)
- **Machine-readable JSON snapshot** (`report.json`)
- **Append-only trend history** (`history.jsonl`) used for anomaly detection

Built for the Superteam Canada bounty: *"Develop Solana Ecosystem Auto-Updating Report &
Interactive Dashboard"* (listing `develop-solana-ecosystem-auto-updating-report-and-interactive-dashboard`).

## Data sources (no API keys required)

| Source | Data |
|---|---|
| Solana JSON-RPC (public RPC, e.g. `solana-rpc.publicnode.com`) | health, slot, block height, epoch progress, TPS, slot time, validator set (active/delinquent, stake, commissions, top validators), token supply |
| DeFiLlama `/v2/chains` | Solana chain TVL |
| DeFiLlama `/overview/dexs/solana` | DEX volume (24h / 7d / change) |
| DeFiLlama `stablecoins.llama.fi` | Stablecoin supply on Solana + per-asset breakdown |
| DeFiLlama `coins.llama.fi` | SOL / USDT / USDC / JUP / BONK / PYTH prices |

> CoinGecko and Dune were intentionally avoided: CoinGecko is unreachable from some
> networks and Dune requires an API key. The design goal is *no API keys, no external
> dependencies* (Python standard library only).

## Quickstart

```bash
python3 collector.py          # regenerates report.json / report.md / index.html
```

## Automation

Keep the report fresh without any manual work:

**Cron (simple):**
```cron
*/30 * * * * cd /path/to/repo && python3 collector.py && git add -A && git commit -m "auto-refresh" && git push
```

**GitHub Actions (recommended):** `.github/workflows/refresh.yml` is included — it re-runs
the collector every 30 minutes and commits the fresh data automatically.

```yaml
# .github/workflows/refresh.yml
name: refresh-report
on:
  schedule:
    - cron: "*/30 * * * *"
  workflow_dispatch:
permissions:
  contents: write
jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python3 collector.py
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "chore: auto-refresh ecosystem report"
```

## Anomaly detection

Built-in alerting rules (visible on the dashboard and in `report.json → alerts`):

- RPC health failures (CRITICAL)
- TPS below 1,500 (WARN)
- Slot time above 500 ms (WARN)
- Delinquent validator stake above 5% (WARN)
- Epoch progress > 95% (INFO — epoch boundary soon)
- Cross-run deltas: TPS drop > 20%, TVL drop > 10%, SOL price drop > 5%,
  24h DEX volume spike > 50% (WARN/INFO)

## Project layout

```
collector.py     # data collection + report/dashboard generation (stdlib only)
run.sh           # convenience wrapper (collect + commit)
report.json      # latest machine-readable snapshot
report.md        # latest human-readable report
index.html       # latest interactive dashboard (self-contained, dark theme)
history.jsonl    # append-only history for trends & anomaly detection
screenshot.png   # rendered dashboard preview
```

## Notes

- All metrics are gathered live at collection time; timestamps are embedded in every output.
- Public RPC endpoints can rate-limit; `collector.py` tries multiple endpoints in order.
- The ecosystem-news section is a curated static context block (refreshed periodically),
  while all numeric data is fully automated.
