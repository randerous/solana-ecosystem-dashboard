#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Solana Ecosystem Auto-Updating Report & Dashboard — collector
Generates report.json / report.md / index.html from public, key-free sources:
  - Solana JSON-RPC (public endpoints)
  - DeFiLlama (TVL / DEX volume / stablecoins / prices)
No API keys required. Stdlib only (Python 3.8+).
"""
import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(OUT_DIR, "history.jsonl")
MAX_HISTORY = 60

RPC_ENDPOINTS = [
    "https://solana-rpc.publicnode.com",
    "https://solana.publicnode.com",
    "https://api.mainnet-beta.solana.com",
    "https://solana-api.projectserum.com",
]
TIMEOUT = 20
UA = "Mozilla/5.0 (X11; Linux x86_64) solana-ecosystem-report/1.0"

# curated static context (refreshed manually; see ECOSYSTEM_NOTES_SOURCE)
ECOSYSTEM_NOTES = [
    {
        "title": "Alpenglow (upcoming upgrade)",
        "note": "Solana's next major upgrade focusing on network performance headroom; "
                "community trackers and validator discussions reference Alpenglow as a "
                "key 2026 development (feature activation tracked in SIMD/Agave releases).",
        "source": "https://github.com/anza-xyz/agave",
    },
    {
        "title": "SIMD-525 (scheduler/execution)",
        "note": "SIMD proposals around scheduling and execution improvements are under "
                "active discussion; monitor SIMD repo for final status.",
        "source": "https://github.com/solana-foundation/solana-improvement-documents",
    },
]

# known major SOL-token addresses for balance spot-checks (optional)
KNOWN_ADDRESSES = {
    "system_program": "11111111111111111111111111111111",
    "config_program": "Config1111111111111111111111111111111111111",
    "stake_program": "Stake11111111111111111111111111111111111111",
    "vote_program": "Vote111111111111111111111111111111111111111",
}


def http_json(url, headers=None, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def rpc_call(endpoint, method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    req = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def rpc(method, params=None, rpc_endpoints=None):
    last_err = None
    for ep in rpc_endpoints or RPC_ENDPOINTS:
        try:
            resp = rpc_call(ep, method, params)
            if "result" in resp:
                return resp["result"]
            last_err = resp.get("error")
        except Exception as e:  # noqa: BLE001
            last_err = f"{ep}: {e}"
    if last_err is not None:
        raise RuntimeError(f"RPC {method} failed: {last_err}")
    return None


def collect_network():
    out = {}
    try:
        health = rpc("getHealth")
        out["health"] = health if isinstance(health, str) else "ok"
    except Exception as e:  # noqa: BLE001
        out["health"] = f"error: {e}"
    try:
        slot = rpc("getSlot")
        out["slot"] = int(slot)
    except Exception:
        out["slot"] = None
    try:
        out["block_height"] = int(rpc("getBlockHeight"))
    except Exception:
        out["block_height"] = None
    try:
        epoch_info = rpc("getEpochInfo")
        out["epoch"] = int(epoch_info["epoch"])
        out["epoch_slot_index"] = int(epoch_info["slotIndex"])
        out["epoch_slots_total"] = int(epoch_info["slotsInEpoch"])
        out["epoch_progress_pct"] = round(100.0 * epoch_info["slotIndex"] / epoch_info["slotsInEpoch"], 2)
    except Exception:
        out["epoch"] = None
    # performance samples -> TPS + slot time
    try:
        samples = rpc("getRecentPerformanceSamples", [2])
        if samples:
            s = samples[-1]
            out["tps"] = round(s["numTransactions"] / max(s["samplePeriodSecs"], 1), 2)
            out["slot_time_ms"] = round(1000.0 * s["samplePeriodSecs"] / max(s["numSlots"], 1), 1)
    except Exception:
        out["tps"] = None
        out["slot_time_ms"] = None
    # validators
    try:
        votes = rpc("getVoteAccounts")
        active = votes.get("current") or votes.get("active") or []
        delinquent = votes.get("delinquent") or []
        out["active_validators"] = len(active)
        out["delinquent_validators"] = len(delinquent)
        total_stake = sum(v.get("activatedStake", 0) for v in active) / 1e9
        del_stake = sum(v.get("activatedStake", 0) for v in delinquent) / 1e9
        out["total_stake_sol"] = round(total_stake, 1)
        out["delinquent_stake_sol"] = round(del_stake, 1)
        out["delinquent_stake_pct"] = round(100.0 * del_stake / max(total_stake + del_stake, 1), 3)
        top = sorted(active, key=lambda v: v.get("activatedStake", 0), reverse=True)[:10]
        out["top_validators"] = [
            {
                "node_pubkey": v.get("nodePubkey"),
                "vote_pubkey": v.get("votePubkey"),
                "stake_sol": round(v.get("activatedStake", 0) / 1e9, 1),
                "commission_pct": v.get("commission"),
                "last_vote": v.get("lastVote"),
            }
            for v in top
        ]
    except Exception as e:  # noqa: BLE001
        out["validators_error"] = str(e)
    # supply
    try:
        sup = rpc("getSupply")
        val = sup["value"]
        out["supply"] = {
            "total_sol": round(val["total"] / 1e9, 2),
            "circulating_sol": round(val["circulating"] / 1e9, 2),
            "non_circulating_sol": round(val["nonCirculating"] / 1e9, 2),
        }
    except Exception:
        out["supply"] = None
    return out


def collect_market():
    out = {}
    try:
        chains = http_json("https://api.llama.fi/v2/chains")
        for c in chains:
            if (c.get("name") or "").lower() == "solana":
                out["tvl_usd"] = round(float(c.get("tvl") or 0), 2)
                break
    except Exception as e:  # noqa: BLE001
        out["tvl_error"] = str(e)
    try:
        dex = http_json("https://api.llama.fi/overview/dexs/solana?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true")
        out["dex_volume_24h_usd"] = round(float(dex.get("total24h") or 0), 2)
        out["dex_volume_7d_usd"] = round(float(dex.get("total7d") or 0), 2)
        out["dex_volume_change_1d_pct"] = round(float(dex.get("change_1d") or 0), 2)
    except Exception as e:  # noqa: BLE001
        out["dex_error"] = str(e)
    # stablecoins on Solana
    try:
        sc = http_json("https://stablecoins.llama.fi/stablecoins?includePrices=true")
        chain_totals = {}
        assets = sc.get("peggedAssets") or []
        for a in assets:
            chain_circ = a.get("chainCirculating") or {}
            if not isinstance(chain_circ, dict):
                continue
            for chain_key, chain_val in chain_circ.items():
                if "solana" in chain_key.lower():
                    cur = chain_val.get("current") if isinstance(chain_val, dict) else chain_val
                    circ = cur.get("peggedUSD") if isinstance(cur, dict) else cur
                    sym = a.get("symbol") or a.get("name") or chain_key
                    try:
                        chain_totals[sym] = round(float(circ or 0), 2)
                    except (TypeError, ValueError):
                        continue
                    break
        out["stablecoins_usd"] = round(sum(chain_totals.values()), 2)
        out["stablecoin_breakdown"] = {k: v for k, v in sorted(chain_totals.items(), key=lambda kv: -kv[1])}
    except Exception as e:  # noqa: BLE001
        out["stablecoins_error"] = str(e)
    # prices
    try:
        ids = ["coingecko:solana", "coingecko:tether", "coingecko:usd-coin", "coingecko:jupiter-exchange-solana", "coingecko:bonk", "coingecko:pyth-network"]
        prices = http_json(f"https://coins.llama.fi/prices/current/{','.join(ids)}")
        coins = prices.get("coins") or {}
        out["prices"] = {
            "SOL": coins.get("coingecko:solana", {}).get("price"),
            "USDT": coins.get("coingecko:tether", {}).get("price"),
            "USDC": coins.get("coingecko:usd-coin", {}).get("price"),
            "JUP": coins.get("coingecko:jupiter-exchange-solana", {}).get("price"),
            "BONK": coins.get("coingecko:bonk", {}).get("price"),
            "PYTH": coins.get("coingecko:pyth-network", {}).get("price"),
            "timestamp": coins.get("coingecko:solana", {}).get("timestamp"),
        }
    except Exception as e:  # noqa: BLE001
        out["prices"] = {"error": str(e)}
    return out


def load_history():
    rows = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return rows


def append_history(snapshot):
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot) + "\n")
    rows = load_history()
    if len(rows) > MAX_HISTORY:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            for r in rows[-MAX_HISTORY:]:
                f.write(json.dumps(r) + "\n")


def delta(prev, cur, factor=1.0):
    if prev is None or cur is None:
        return None
    if prev == 0:
        return None
    return round((cur - prev) / abs(prev) * 100.0 * factor, 2)


def build_alerts(net, market, hist):
    alerts = []
    health = net.get("health")
    if health and health != "ok":
        alerts.append({"level": "CRITICAL", "metric": "RPC health", "message": f"getHealth returned: {health}"})
    tps = net.get("tps")
    if tps is not None and tps < 1500:
        alerts.append({"level": "WARN", "metric": "TPS", "message": f"TPS {tps} below 1500"})
    st = net.get("slot_time_ms")
    if st is not None and st > 500:
        alerts.append({"level": "WARN", "metric": "Slot time", "message": f"Slot time {st}ms above 500ms"})
    dsp = net.get("delinquent_stake_pct")
    if dsp is not None and dsp > 5:
        alerts.append({"level": "WARN", "metric": "Validator delinquency", "message": f"Delinquent stake {dsp}% above 5%"})
    ep = net.get("epoch_progress_pct")
    if ep is not None and ep > 95:
        alerts.append({"level": "INFO", "metric": "Epoch boundary", "message": f"Epoch {net.get('epoch')} at {ep}% — boundary soon"})
    # history deltas
    prev = hist[-2] if len(hist) >= 2 else None
    if prev:
        dt = delta((prev.get("network") or {}).get("tps"), tps)
        if dt is not None and dt < -20:
            alerts.append({"level": "WARN", "metric": "TPS trend", "message": f"TPS dropped {abs(dt)}% vs previous run"})
        dtv = delta((prev.get("market") or {}).get("tvl_usd"), market.get("tvl_usd"))
        if dtv is not None and dtv < -10:
            alerts.append({"level": "WARN", "metric": "TVL trend", "message": f"Solana TVL dropped {abs(dtv)}% vs previous run"})
        dp = delta((prev.get("market") or {}).get("prices", {}).get("SOL"), market.get("prices", {}).get("SOL"))
        if dp is not None and dp < -5:
            alerts.append({"level": "WARN", "metric": "SOL price", "message": f"SOL down {abs(dp)}% vs previous run"})
        ddex = delta((prev.get("market") or {}).get("dex_volume_24h_usd"), market.get("dex_volume_24h_usd"))
        if ddex is not None and ddex > 50:
            alerts.append({"level": "INFO", "metric": "DEX volume", "message": f"24h DEX volume up {ddex}% vs previous run"})
    return alerts


def build_snapshot():
    net = collect_network()
    market = collect_market()
    hist = load_history()
    alerts = build_alerts(net, market, hist)
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network": net,
        "market": market,
        "alerts": alerts,
        "ecosystem_notes": ECOSYSTEM_NOTES,
    }
    append_history(snapshot)
    return snapshot


def gen_markdown(snap, hist):
    net = snap["network"]
    market = snap["market"]
    prices = market.get("prices") or {}
    now = snap["generated_at"]
    L = []
    L.append("# Solana Ecosystem Report (auto-updated)\n")
    L.append(f"_Generated: {now} UTC — refreshed automatically; see `collector.py` and `history.jsonl`._\n")
    L.append("## Network health\n")
    L.append(f"- RPC health: `{net.get('health')}`")
    L.append(f"- Slot: {net.get('slot', 'n/a')} | Block height: {net.get('block_height', 'n/a')}")
    L.append(f"- Epoch: {net.get('epoch', 'n/a')} ({net.get('epoch_progress_pct', 'n/a')}% complete)")
    L.append(f"- TPS (recent sample): {net.get('tps', 'n/a')}")
    L.append(f"- Slot time: {net.get('slot_time_ms', 'n/a')} ms")
    L.append("")
    L.append("## Validators\n")
    L.append(f"- Active: {net.get('active_validators', 'n/a')} | Delinquent: {net.get('delinquent_validators', 'n/a')}")
    L.append(f"- Total active stake: {net.get('total_stake_sol', 'n/a')} SOL | Delinquent stake: {net.get('delinquent_stake_pct', 'n/a')}%")
    tv = net.get("top_validators") or []
    if tv:
        L.append("\nTop validators by stake:")
        L.append("| # | Node pubkey | Stake (SOL) | Commission (%) |")
        L.append("|---|-------------|-------------|----------------|")
        for i, v in enumerate(tv[:8], 1):
            L.append(f"| {i} | `{v['node_pubkey'][:16]}…` | {v['stake_sol']} | {v['commission_pct']} |")
    L.append("")
    sup = net.get("supply")
    if sup:
        L.append("## Supply\n")
        L.append(f"- Total: {sup['total_sol']:,.0f} SOL | Circulating: {sup['circulating_sol']:,.0f} SOL | Non-circulating: {sup['non_circulating_sol']:,.0f} SOL\n")
    L.append("## Market & ecosystem\n")
    sol_price = prices.get("SOL")
    if sol_price:
        mcap = None
        if sup:
            mcap = sol_price * sup["circulating_sol"]
        L.append(f"- SOL price: ${sol_price:,.4f}" + (f" | implied market cap ≈ ${mcap:,.0f}" if mcap else ""))
    L.append(f"- Solana TVL (DeFiLlama): ${market.get('tvl_usd', 0):,.0f}")
    L.append(f"- DEX volume 24h: ${market.get('dex_volume_24h_usd', 0):,.0f} (7d: ${market.get('dex_volume_7d_usd', 0):,.0f}, 24h change: {market.get('dex_volume_change_1d_pct', 0)}%)")
    L.append(f"- Stablecoins on Solana: ${market.get('stablecoins_usd', 0):,.0f}")
    bd = market.get("stablecoin_breakdown") or {}
    if bd:
        L.append("  - " + ", ".join(f"{k}: ${v:,.0f}" for k, v in list(bd.items())[:6]))
    L.append("")
    L.append("## Alerts\n")
    alerts = snap.get("alerts") or []
    if alerts:
        for a in alerts:
            L.append(f"- **[{a['level']}]** {a['metric']}: {a['message']}")
    else:
        L.append("- No anomalies detected.")
    L.append("")
    L.append("## Ecosystem context (curated, refreshed periodically)\n")
    for n in ECOSYSTEM_NOTES:
        L.append(f"- **{n['title']}**: {n['note']} _({n['source']})_")
    L.append("")
    L.append("## History trend\n")
    if len(hist) >= 2:
        last2 = hist[-2:]
        L.append("| Run (UTC) | TPS | Slot time (ms) | SOL price ($) | TVL ($B) | DEX 24h ($M) |")
        L.append("|-----------|-----|----------------|---------------|----------|---------------|")
        for r in last2:
            n2 = r.get("network") or {}
            m2 = r.get("market") or {}
            p2 = (m2.get("prices") or {}).get("SOL")
            L.append(f"| {r.get('generated_at','')[:16]} | {n2.get('tps','n/a')} | {n2.get('slot_time_ms','n/a')} | "
                     f"{p2 if p2 is None else round(p2,4)} | {((m2.get('tvl_usd') or 0)/1e9):.2f} | {((m2.get('dex_volume_24h_usd') or 0)/1e6):.2f} |")
    else:
        L.append("Collecting trend data… (runs append to `history.jsonl`)")
    L.append("")
    L.append("---")
    L.append("_Sources: Solana JSON-RPC (public), DeFiLlama (TVL/DEX/stablecoins/prices). No API keys required._")
    return "\n".join(L)


def gen_html(snap, hist):
    net = snap["network"]
    market = snap["market"]
    prices = market.get("prices") or {}
    sup = net.get("supply") or {}
    tv = net.get("top_validators") or []
    alerts = snap.get("alerts") or []
    sol_price = prices.get("SOL")
    kpi = [
        ("Health", str(net.get("health", "n/a"))),
        ("Epoch", f"{net.get('epoch','n/a')} · {net.get('epoch_progress_pct','n/a')}%"),
        ("TPS", str(net.get("tps", "n/a"))),
        ("Slot time", f"{net.get('slot_time_ms','n/a')} ms"),
        ("Active Vals", str(net.get("active_validators", "n/a"))),
        ("Delinquent", f"{net.get('delinquent_stake_pct','n/a')}% stake"),
        ("SOL", f"${sol_price:,.4f}" if sol_price else "n/a"),
        ("TVL", f"${(market.get('tvl_usd') or 0)/1e9:,.2f}B"),
        ("DEX 24h", f"${(market.get('dex_volume_24h_usd') or 0)/1e6:,.1f}M"),
        ("Stables", f"${(market.get('stablecoins_usd') or 0)/1e9:,.2f}B"),
    ]
    cards = "".join(f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div></div>' for k, v in kpi)
    alert_html = "".join(f'<div class="alert {a["level"].lower()}"><b>[{a["level"]}] {a["metric"]}</b> — {a["message"]}</div>' for a in alerts) or '<div class="ok">No anomalies detected.</div>'
    top_val_rows = "".join(
        f"<tr><td>{i}</td><td class='mono'>{v['node_pubkey'][:20]}…</td><td>{v['stake_sol']:,.0f}</td><td>{v['commission_pct']}</td></tr>"
        for i, v in enumerate(tv[:8], 1)
    )
    price_rows = "".join(f"<tr><td>{k}</td><td>{'$' + f'{v:,.4f}' if isinstance(v,(int,float)) else 'n/a'}</td></tr>" for k, v in prices.items() if k != "timestamp")
    stables_rows = "".join(f"<tr><td>{k}</td><td>${v:,.0f}</td></tr>" for k, v in (market.get("stablecoin_breakdown") or {}).items())
    sol_price_fmt = f"{sol_price:,.4f}" if sol_price else "n/a"
    notes_html = "".join(f'<li><b>{n["title"]}</b> — {n["note"]} <span class="src">({n["source"]})</span></li>' for n in snap.get("ecosystem_notes") or [])
    trend_html = ""
    if len(hist) >= 2:
        rows = hist[-14:]
        pts = [f"{{label:'{r.get('generated_at','')[:13]}',tps:{((r.get('network') or {}).get('tps') or 0)},slot:{((r.get('network') or {}).get('slot_time_ms') or 0)},price:{((r.get('market') or {}).get('prices') or {}).get('SOL') or 0}}}" for r in rows]
        trend_html = f"<script>const TREND={json.dumps([{'label':r.get('generated_at','')[:16],'tps':(r.get('network') or {}).get('tps'),'slot':(r.get('network') or {}).get('slot_time_ms'),'price':((r.get('market') or {}).get('prices') or {}).get('SOL')} for r in rows])};</script>"
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Solana Ecosystem Dashboard</title>
<style>
:root{{--bg:#0b1020;--card:#131a2e;--line:#223;--txt:#dbe4f5;--dim:#7d8bb0;--acc:#4f8cff;--warn:#ffb84d;--crit:#ff5d5d;--ok:#3ddc84}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--txt);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;padding:24px}}
h1{{font-size:22px;margin-bottom:4px}} .sub{{color:var(--dim);margin-bottom:20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}}
.k{{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}} .v{{font-size:18px;font-weight:600;margin-top:4px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}}
h2{{font-size:15px;margin-bottom:12px;color:#aebce0}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:6px 8px;text-align:left;border-bottom:1px solid var(--line)}}
.mono{{font-family:ui-monospace,Menlo,monospace;font-size:12px}}
.alert{{padding:8px 10px;border-radius:8px;margin-bottom:8px;font-size:13px}}
.alert.warn{{background:rgba(255,184,77,.12);border:1px solid rgba(255,184,77,.4)}}
.alert.critical{{background:rgba(255,93,93,.12);border:1px solid rgba(255,93,93,.4)}}
.alert.info{{background:rgba(79,140,255,.12);border:1px solid rgba(79,140,255,.4)}}
.ok{{color:var(--ok)}} .src{{color:var(--dim);font-size:11px}}
.bar{{display:inline-block;height:9px;border-radius:5px;background:var(--acc)}}
ul{{padding-left:18px}} li{{margin-bottom:8px}}
#trend{{width:100%;height:220px}}
.foot{{color:var(--dim);font-size:11px;margin-top:20px}}
</style></head><body>
<h1>Solana Ecosystem Dashboard</h1>
<div class="sub">Auto-updated report · generated {snap['generated_at']} UTC · sources: public Solana RPC + DeFiLlama (no API keys)</div>
<div class="grid">{cards}</div>
<section><h2>Alerts &amp; Anomaly Detection</h2>{alert_html}</section>
<section><h2>Network</h2>
<table><tr><th>Metric</th><th>Value</th></tr>
<tr><td>Slot</td><td>{net.get('slot','n/a')} (block height {net.get('block_height','n/a')})</td></tr>
<tr><td>Epoch</td><td>{net.get('epoch','n/a')} — {net.get('epoch_progress_pct','n/a')}% complete</td></tr>
<tr><td>TPS (recent sample)</td><td>{net.get('tps','n/a')}</td></tr>
<tr><td>Slot time</td><td>{net.get('slot_time_ms','n/a')} ms</td></tr>
<tr><td>Validators</td><td>{net.get('active_validators','n/a')} active / {net.get('delinquent_validators','n/a')} delinquent</td></tr>
<tr><td>Stake</td><td>{net.get('total_stake_sol','n/a')} SOL active · {net.get('delinquent_stake_pct','n/a')}% delinquent</td></tr>
<tr><td>Supply</td><td>{sup.get('total_sol','n/a')} total / {sup.get('circulating_sol','n/a')} circulating</td></tr>
</table></section>
<section><h2>Top Validators by Stake</h2><table><tr><th>#</th><th>Node pubkey</th><th>Stake (SOL)</th><th>Commission (%)</th></tr>{top_val_rows}</table></section>
<section><h2>Market &amp; Ecosystem</h2>
<table><tr><th>Metric</th><th>Value</th></tr>
<tr><td>SOL price</td><td>${sol_price_fmt}</td></tr>
<tr><td>TVL (DeFiLlama)</td><td>${(market.get('tvl_usd') or 0):,.0f}</td></tr>
<tr><td>DEX volume 24h</td><td>${(market.get('dex_volume_24h_usd') or 0):,.0f} (7d ${(market.get('dex_volume_7d_usd') or 0):,.0f})</td></tr>
<tr><td>Stablecoins on Solana</td><td>${(market.get('stablecoins_usd') or 0):,.0f}</td></tr></table>
<h2 style="margin-top:14px">Stablecoin breakdown</h2><table><tr><th>Asset</th><th>Circulating</th></tr>{stables_rows}</table>
<h2 style="margin-top:14px">Prices (DeFiLlama)</h2><table><tr><th>Asset</th><th>USD</th></tr>{price_rows}</table>
</section>
<section><h2>Ecosystem context</h2><ul>{notes_html}</ul></section>
<section><h2>Trend (last runs)</h2><canvas id="trend"></canvas>{trend_html}</section>
<div class="foot">Machine-readable: <span class="mono">report.json</span> · code: <span class="mono">collector.py</span> · history: <span class="mono">history.jsonl</span></div>
<script>
const c=document.getElementById('trend');if(c&&typeof TREND!=='undefined'&&TREND.length>1){{
const dpr=window.devicePixelRatio||1;const w=c.parentElement.clientWidth,h=220;c.width=w*dpr;c.height=h*dpr;c.style.width=w+'px';c.style.height=h+'px';
const ctx=c.getContext('2d');ctx.scale(dpr,dpr);const pad=36;
const series=['tps','slot','price'];const colors={{tps:'#4f8cff',slot:'#ffb84d',price:'#3ddc84'}};
const xs=TREND.map((_,i)=>pad+(w-pad*2)*i/Math.max(TREND.length-1,1));
for(const s of series){{const vals=TREND.map(r=>r[s]||0).filter(v=>v>0);if(!vals.length)continue;
const mn=Math.min(...vals),mx=Math.max(...vals);const ys=TREND.map(r=>h-pad-(h-pad*2)*((r[s]||0)-mn)/Math.max(mx-mn,1));
ctx.strokeStyle=colors[s];ctx.lineWidth=1.6;ctx.beginPath();TREND.forEach((r,i)=>{{if(i===0)ctx.moveTo(xs[i],ys[i]);else ctx.lineTo(xs[i],ys[i]);}});ctx.stroke();
ctx.fillStyle=colors[s];ctx.font='11px sans-serif';ctx.fillText(s,pad,10+Object.keys(series).indexOf(s)*16);}}
ctx.strokeStyle='#223';ctx.strokeRect(pad,pad,w-pad*2,h-pad*2);}}
</script>
</body></html>"""
    return html


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT_DIR
    snap = build_snapshot()
    hist = load_history()
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, ensure_ascii=False)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write(gen_markdown(snap, hist))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(gen_html(snap, hist))
    print(f"[OK] {datetime.now(timezone.utc).isoformat()} — report.json / report.md / index.html written")
    print(json.dumps({"health": snap["network"].get("health"), "tps": snap["network"].get("tps"),
                      "slot_time_ms": snap["network"].get("slot_time_ms"),
                      "sol_price": (snap["market"].get("prices") or {}).get("SOL"),
                      "tvl_usd": snap["market"].get("tvl_usd"),
                      "alerts": len(snap.get("alerts") or [])}, indent=1))


if __name__ == "__main__":
    main()
