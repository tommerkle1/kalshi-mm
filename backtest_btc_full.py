"""
Full 67-day BTC backtest at pos=10.
Samples 100 hours evenly across Jan 28 – Apr 6, 2026.
"""
import requests, time, json
from collections import defaultdict

TAKER_FEE = 0.07
OFFSET     = 0.01
MAX_POS    = 10
TARGET_HOURS = 100
MIN_VOL    = 30_000

def get_trades(ticker):
    trades, cursor = [], None
    for _ in range(40):
        params = {"ticker": ticker, "limit": 100}
        if cursor: params["cursor"] = cursor
        try:
            r = requests.get("https://api.elections.kalshi.com/trade-api/v2/markets/trades",
                             params=params, timeout=10)
            data = r.json()
        except: break
        batch = data.get("trades", [])
        if not batch: break
        trades.extend(batch)
        cursor = data.get("cursor")
        if not cursor: break
        time.sleep(0.04)
    trades.sort(key=lambda t: t["created_time"])
    return trades

def simulate(trades, yes_won):
    yes_pos = no_pos = 0
    fills = []
    yes_q = no_q = None
    for t in trades:
        yp = float(t["yes_price_dollars"])
        if not (0.05 <= yp <= 0.95):
            yes_q = no_q = None
            continue
        np_ = 1.0 - yp
        if yes_q is not None and yp <= yes_q and yes_pos < MAX_POS:
            fills.append(("yes", yes_q)); yes_pos += 1; yes_q = None
        if no_q is not None and np_ <= no_q and no_pos < MAX_POS:
            fills.append(("no", no_q)); no_pos += 1; no_q = None
        if yes_q is None and yes_pos < MAX_POS: yes_q = round(yp - OFFSET, 4)
        if no_q is None and no_pos < MAX_POS: no_q = round(np_ - OFFSET, 4)
    gross = rts = 0.0
    for side, price in fills:
        if side == "yes":
            if yes_won:
                p = 1.0 - price; gross += p - p*TAKER_FEE; rts += 1
            else: gross -= price
        else:
            if not yes_won:
                p = 1.0 - price; gross += p - p*TAKER_FEE; rts += 1
            else: gross -= price
    return gross, len(fills), int(rts)

# Build index
print("Building index (this takes ~2 min)...")
all_markets, cursor, p = [], None, 0
while p < 100:
    params = {"series_ticker": "KXBTCD", "status": "settled", "limit": 200}
    if cursor: params["cursor"] = cursor
    r = requests.get("https://api.elections.kalshi.com/trade-api/v2/markets", params=params, timeout=12)
    data = r.json()
    batch = data.get("markets", [])
    if not batch: break
    all_markets.extend(batch)
    cursor = data.get("cursor")
    p += 1
    if p % 20 == 0: print(f"  page {p}: {len(all_markets)} markets")
    if not cursor: break
    time.sleep(0.06)
print(f"Total: {len(all_markets)} markets")

# Group by hour
hours = defaultdict(list)
for m in all_markets:
    parts = m["ticker"].split("-")
    if len(parts) >= 2:
        hours[parts[1]].append(m)

candidates = []
for hour, markets in hours.items():
    best = max(markets, key=lambda m: float(m.get("volume_fp","0")))
    if float(best.get("volume_fp","0")) >= MIN_VOL:
        candidates.append((hour, best))
candidates.sort(key=lambda x: x[0])
print(f"High-vol hours: {len(candidates)} ({candidates[0][0]} to {candidates[-1][0]})")

step = max(1, len(candidates) // TARGET_HOURS)
selected = candidates[::step][:TARGET_HOURS]
print(f"Sampled: {len(selected)} hours\n")

results = []
for i, (hour, market) in enumerate(selected):
    ticker  = market["ticker"]
    yes_won = market.get("result","").lower() == "yes"
    trades  = get_trades(ticker)
    unc     = [t for t in trades if 0.05 <= float(t["yes_price_dollars"]) <= 0.95]
    if len(unc) < 5:
        print(f"  [{i+1:3d}] {hour} SKIP")
        continue
    prices = [float(t["yes_price_dollars"]) for t in unc]
    swing  = max(prices) - min(prices)
    pnl, fills, rts = simulate(unc, yes_won)
    tag = "✓" if pnl > 0 else "✗"
    print(f"  [{i+1:3d}] {hour} {tag}  swing={swing:.2f}  t={len(unc):4d}  fills={fills:2d}  rt={rts:2d}  ${pnl:+.3f}")
    results.append({"hour":hour,"pnl":pnl,"fills":fills,"rts":rts,"swing":swing,"yes_won":yes_won})

# Summary
n = len(results)
if n == 0:
    print("No results"); exit()
total_pnl = sum(r["pnl"] for r in results)
wins = sum(1 for r in results if r["pnl"] > 0)
fills = sum(r["fills"] for r in results)
rts   = sum(r["rts"] for r in results)
swing = sum(r["swing"] for r in results)/n
big_loss = [r for r in results if r["pnl"] < -5]

print(f"""
{'='*60}
FULL 67-DAY BTC BACKTEST (pos={MAX_POS})
{'='*60}
Hours:        {n}
Date range:   {results[0]['hour']} → {results[-1]['hour']}
Avg swing:    {swing:.2f} ({swing*100:.0f}¢)
Total fills:  {fills}
Round-trips:  {rts}
Win rate:     {wins}/{n} ({wins/n*100:.0f}%)
Net P&L:      ${total_pnl:+.2f}
ROI on $500:  {total_pnl/500*100:+.2f}%
P&L/hour:     ${total_pnl/n:+.4f}
P&L/day(18h): ${total_pnl/n*18:+.2f}
P&L/mo:       ${total_pnl/n*18*30:+.0f}

Big losses (>$5):  {len(big_loss)}/{n} hours ({len(big_loss)/n*100:.0f}%)""")
if big_loss:
    for r in sorted(big_loss, key=lambda r: r["pnl"])[:5]:
        print(f"  {r['hour']}  ${r['pnl']:+.2f}  swing={r['swing']:.2f}")
print("="*60)

# Save results
with open("/tmp/btc_full_results.json","w") as f:
    json.dump({"n":n,"total_pnl":total_pnl,"wins":wins,"fills":fills,"rts":rts,
               "avg_swing":swing,"big_losses":len(big_loss),"results":results},f,indent=2)
print("Results saved to /tmp/btc_full_results.json")
