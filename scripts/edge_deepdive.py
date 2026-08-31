"""Deep-dive on live instances: clock stratification, q-band calibration, new-idea scan."""
import sys

sys.path.insert(0, "src")
import math

from gridbot.instruments.verdict import build_oracles, load_instances, resolve_instances


def wilson(w,n,z=1.96):
    if not n: return (float('nan'),)*2
    p=w/n; d=1+z*z/n; c=p+z*z/(2*n)
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return ((c-h)/d,(c+h)/d)

rows = load_instances(replay=False, include_test=False)
sids = {str(r["series_id"]) for r in rows}
oracles = build_oracles(sids)
res = resolve_instances(rows, oracles)
res = [r for r in res if r["resolved"]]
print("resolved:", len(res))

def show(label, sub):
    if not sub: return
    w=sum(1 for r in sub if r["won"]); n=len(sub)
    lo,hi=wilson(w,n)
    print(f"{label:28s} {w:>4}/{n:<4} = {w/n:.3f}  ({lo:.3f}-{hi:.3f})")

print("\n== H-MANADV1 by entry clock (pre-committed stratification) ==")
ma=[r for r in res if r["hypothesis_id"]=="H-MANADV1"]
for lo_s,hi_s in [(0,30),(30,60),(60,90),(90,300)]:
    show(f"clock {lo_s}-{hi_s}s", [r for r in ma if lo_s<=(r.get("clock_seconds") or 0)<hi_s])

print("\n== MODEL q-band calibration (all thresholds pooled, by q decile) ==")
mo=[r for r in res if r["hypothesis_id"]=="H-MODELENTRY1"]
qs=sorted(r.get("q",0) for r in mo)
for i in range(5):
    lo_q,hi_q = qs[int(i*len(qs)/5)], qs[min(int((i+1)*len(qs)/5),len(qs)-1)]
    band=[r for r in mo if lo_q<=r.get("q",0)<=hi_q+1e-9]
    show(f"q {lo_q:.2f}-{hi_q:.2f}", band)

print("\n== ECONBUY by score context ==")
ec=[r for r in res if r["hypothesis_id"]=="H-ECONBUY1"]
st=lambda r: (r.get("state") or {})
behind=[r for r in ec if st(r).get("ct",{}).get("score",0) < st(r).get("t",{}).get("score",0)]
show("buyer behind", behind); show("buyer tied/ahead", [r for r in ec if r not in behind])

print("\n== early vs late round (new-idea scan) ==")
show("rounds 1-4", [r for r in res if 1<=r.get("round_number",0)<=4])
show("rounds 5-11", [r for r in res if 5<=r.get("round_number",0)<=11])
show("rounds 12+", [r for r in res if r.get("round_number",0)>=12])

print("\n== clock-band resolution across ALL hypotheses (urgency scan) ==")
for lo_s,hi_s in [(0,30),(30,60),(60,90),(90,300)]:
    show(f"clock {lo_s}-{hi_s}s", [r for r in res if lo_s<=(r.get("clock_seconds") or 999)<hi_s])

print("\n== map_number effect (map 1 vs later maps) ==")
show("map 1", [r for r in res if r.get("map_number")==1])
show("map 2+", [r for r in res if (r.get("map_number") or 1)>=2])
