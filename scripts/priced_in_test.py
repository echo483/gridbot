"""Deep failure analysis: are discounts real edge or the market's true price?

Night-4 question: 3W-10L on fills, losers concentrated at 'discounted' LONGs.
Hypothesis under test (user): 'if it's discounted it's probably priced in' —
i.e., our model fair > book because the BOOK knows something the model
doesn't (slow-money test).

Method: for every mapentry row with a real book, compute discount = model
p_map - side_ask, then follow what HAPPENED to that price afterward using
postquote samples (+30/+60/+120s) and eventual map outcome. If discounts were
edge, price would rise toward fair (drift up). If priced-in, price stays or
falls (we'd be catching falling knives).
"""
import json
import math
from collections import defaultdict


def wilson(w, n, z=1.96):
    if not n: return (float('nan'),) * 2
    p = w/n; d = 1 + z*z/n; c = p + z*z/(2*n)
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return ((c-h)/d, (c+h)/d)

def load(path):
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # torn line: skip, never crash an evidence read
    except FileNotFoundError:
        pass
    return out

def bucket(disc):
    if disc < 4: return '0-4 (below gate)'
    if disc < 10: return '4-10'
    if disc < 20: return '10-20'
    if disc < 30: return '20-30'
    return '30+'

stats = defaultdict(lambda: {'n':0, 'filled':0, 'won':0})
post_drift = defaultdict(list)  # bucket -> [pnl points if bought at ask]

for day in ('20260826', '20260827', '20260828'):
    me = load(f'data/shadow/mapentry-{day}.jsonl')
    pq = load(f'data/shadow/postquote-{day}.jsonl')
    ledger = load(f'data/executor/intents-{day}.jsonl')
    fills = {}
    for r in ledger:
        if r.get('type') == 'fill': fills.setdefault(r.get('dedup_key'), r)
    settled = {}
    for r in ledger:
        if r.get('type') == 'settle' and r.get('won') is not None:
            settled.setdefault(r.get('dedup_key'), r['won'])

    # index postquote rows: they carry series/map/round + oriented mid drift
    pq_ix = defaultdict(list)
    for r in pq:
        if r.get('type') == 'postquote':
            pq_ix[(str(r.get('series_id')), r.get('map_number'), r.get('round_number'))].append(r)

    for r in me:
        if r.get('type') != 'mapentry': continue
        ask = r.get('side_ask'); p = r.get('p_map')
        if not isinstance(ask, (int, float)) or not isinstance(p, (int, float)): continue
        side = r.get('us_side')
        fair = p if side == 'long' else 1 - p
        disc = (fair - ask) * 100
        b = bucket(disc)
        key = (str(r.get('series_id')), r.get('map_number'), r.get('round_number'))
        st = stats[b]
        st['n'] += 1

    # fills: bucket by their discount at fill (approx via same-day mapentry nearest)
    for k, f in fills.items():
        dk = str(k).split(':')
        if len(dk) < 4: continue
        sid, mp = dk[1], dk[2]
        price = f.get('price'); ts = f.get('ts_ns')
        # find nearest mapentry for series/map before fill
        cand = [(r['ts_ns'], r) for r in me
                if str(r.get('series_id')) == sid and str(r.get('map_number')) and str(r.get('map_number')) == mp
                and r.get('p_map') and r['ts_ns'] <= ts]
        if not cand or price is None: continue
        _, r = max(cand)
        ask = r.get('side_ask'); p = r.get('p_map')
        side = r.get('us_side')
        fair = p if side == 'long' else 1 - p
        disc = (fair - price) * 100
        won = settled.get(k)
        if won is None: continue
        b = bucket(disc)
        stats[b]['filled'] += 1
        if won: stats[b]['won'] += 1

print('=== Would-be discount buckets: all real-book evals vs fill outcomes ===')
print(f"{'bucket':18s} {'evals':>6} {'fills':>6} {'won':>4} {'win%':>7} {'CI':>15}")
order = ['0-4 (below gate)', '4-10', '10-20', '20-30', '30+']
for b in order:
    s = stats[b]
    lo, hi = wilson(s['won'], s['filled'])
    print(f"{b:18s} {s['n']:>6} {s['filled']:>6} {s['won']:>4} {s['won']/max(s['filled'],1):>7.2f} ({lo:.2f}-{hi:.2f})")

# postquote drift: after model says 'cheap', does price rise (edge) or fall (priced-in)?
print()
print('=== Post-quote price drift after model-discounted states (priced-in test) ===')
pq_stats = defaultdict(lambda: [0, 0.0, 0.0])
for day in ('20260827', '20260828'):
    me = load(f'data/shadow/mapentry-{day}.jsonl')
    pq = load(f'data/shadow/postquote-{day}.jsonl')
    ix = defaultdict(list)
    for r in pq:
        if r.get('type') == 'postquote':
            ix[(str(r.get('series_id')), r.get('map_number'), str(r.get('dedup_extra')))].append(r)
    for r in me:
        if r.get('type') != 'mapentry': continue
        ask = r.get('side_ask'); p = r.get('p_map'); side = r.get('us_side')
        if not isinstance(ask, (int, float)) or not isinstance(p, (int, float)): continue
        fair = p if side == 'long' else 1 - p
        disc = (fair - ask) * 100
        if disc < 10: continue  # only rows our entry rule would care about
        b = bucket(disc)
        key = (str(r.get('series_id')), r.get('map_number'), str(r.get('dedup_extra')))
        for q in ix.get(key, []):
            horizon = q.get('dt_s')
            drift = q.get('oriented_move')
            if drift is None: continue
            pq_stats[(b, horizon)][0] += 1
            pq_stats[(b, horizon)][1] += drift
            pq_stats[(b, horizon)][2] += drift ** 2

if pq_stats:
    print(f"{'bucket':12s} {'horizon':>8s} {'n':>4} {'mean_drift':>11s}")
    for (b, h), (n, s, s2) in sorted(pq_stats.items()):
        print(f"{b:12s} {str(h)+'s':>8s} {n:>4} {s/n:>+11.4f}")
else:
    print('  (no postquote rows joined — check orientation fields)')

print()
print('=== Bottom line ===')
deep = stats['20-30']; deep2 = stats['30+']
deep_w = deep['won'] + deep2['won']; deep_n = deep['filled'] + deep2['filled']
mid = stats['10-20']
print(f"deep discounts (>=20pt): fills={deep_n} win%={deep_w/max(deep_n,1):.2f}")
print(f"mid discounts (10-20pt): fills={mid['filled']} win%={mid['won']/max(mid['filled'],1):.2f}")
print("corpus anchor was 0.83-0.85. Live verdict: see table above.")
