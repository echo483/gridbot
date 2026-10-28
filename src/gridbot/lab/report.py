"""The research report: one self-contained HTML page per scan batch.

Why a page and not markdown: the numbers that matter here are *distributions and
intervals*, and those are read at a glance or not at all. Every hypothesis gets a
card with its equity curve, its interval bars, and its verdict; the page also
carries the two facts a reader must not have to hunt for — the number of trials
the search has run (because it deflates every Sharpe on the page) and the fill
rate (because an edge that never fills is not an edge).

The page is standalone: no CDN, no fonts, no network, so it renders identically
on an air-gapped host and inside a preview pane. Colours come from CSS variables
with fallbacks, so it themes with the surrounding app and still looks right when
opened directly in a browser.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gridbot.lab.scan import ScanResult, Verdict

VERDICT_COLORS = {
    Verdict.PASS: "#22c55e",
    Verdict.FAIL: "#ef4444",
    Verdict.UNDER_POWERED: "#eab308",
    Verdict.NO_OBSERVATIONS: "#94a3b8",
    Verdict.NOT_RUNNABLE: "#94a3b8",
}


@dataclass
class ReportMeta:
    """Provenance for the whole run — a report without this is an anecdote."""

    title: str = "gridbot research scan"
    source: str = ""
    universe: str = ""
    n_trials: int = 0
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(UTC).isoformat()


def _svg_equity(equity: list[float], width: int = 320, height: int = 70) -> str:
    """Inline SVG equity curve. Flat and empty cases are drawn, not skipped."""
    if len(equity) < 2:
        return (
            f'<svg class="eq" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
            f'<text x="6" y="{height // 2}" class="muted">no fills</text></svg>'
        )
    lo, hi = min(equity), max(equity)
    span = (hi - lo) or 1.0
    step = width / (len(equity) - 1)
    pts = [
        f"{i * step:.2f},{height - (v - lo) / span * (height - 8) - 4:.2f}"
        for i, v in enumerate(equity)
    ]
    zero_y = height - (0 - lo) / span * (height - 8) - 4
    color = "#22c55e" if equity[-1] >= 0 else "#ef4444"
    return (
        f'<svg class="eq" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        f'<line x1="0" y1="{zero_y:.2f}" x2="{width}" y2="{zero_y:.2f}" class="zero"/>'
        f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{" ".join(pts)}"/>'
        "</svg>"
    )


def _fmt(value: float | None, digits: int = 4, suffix: str = "") -> str:
    if value is None:
        return '<span class="muted">undefined</span>'
    return f"{value:+.{digits}f}{suffix}"


def _ci_span(lo: float | None, hi: float | None) -> str:
    if lo is None or hi is None:
        return '<span class="muted">no interval</span>'
    excludes_zero = (lo > 0 and hi > 0) or (lo < 0 and hi < 0)
    klass = "sig" if excludes_zero else "notsig"
    tag = "excludes 0" if excludes_zero else "includes 0"
    return f'<span class="{klass}">[{lo:+.4f}, {hi:+.4f}] {tag}</span>'


def _card(result: ScanResult) -> str:
    color = VERDICT_COLORS.get(result.verdict, "#94a3b8")
    mean = result.filled.mean_pnl
    hit = result.filled.hit_rate
    sharpe = result.filled.sharpe
    ba = result.beta_alpha
    dsr = result.deflated
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in result.notes)
    beta_row = (
        f"<tr><td>beta</td><td>{_fmt(ba.beta.value, 3)}</td>"
        f"<td>{_ci_span(*(ba.beta.ci or (None, None)))}</td>"
        f"<td>{ba.beta.n} obs / {ba.beta.n_series} series</td></tr>"
        f"<tr><td>alpha</td><td>{_fmt(ba.alpha.value, 5)}</td>"
        f"<td>{_ci_span(*(ba.alpha.ci or (None, None)))}</td>"
        f"<td>r&sup2;={ba.r_squared:.3f}</td></tr>"
        if ba
        else ""
    )
    dsr_row = (
        f"<tr><td>deflated Sharpe</td><td>{dsr.value:.4f}</td><td>"
        f"{'<span class=\"notsig\">search handed you ' + format(dsr.meta.get('expected_max_sharpe', 0), '+.3f') + '</span>' if dsr.meta else ''}"
        f"</td><td>n_trials={result.n_trials}</td></tr>"
        if dsr.defined
        else ""
    )
    return f"""
    <section class="card" style="--vc:{color}">
      <header>
        <span class="pill">{result.verdict.value}</span>
        <h2>{html.escape(result.hypothesis_id)}</h2>
        <span class="hash">cfg {html.escape(result.config_hash)}</span>
      </header>
      <p class="reason">{html.escape(result.verdict_reason)}</p>
      {_svg_equity(result.equity)}
      <table>
        <tr><th></th><th>estimate</th><th>95% interval (clustered by series)</th><th></th></tr>
        <tr><td>mean PnL / contract</td><td>{_fmt(mean.value)}</td>
            <td>{_ci_span(*(mean.ci or (None, None)))}</td>
            <td>n={mean.n} series={mean.n_series}</td></tr>
        <tr><td>hit rate</td><td>{_fmt(hit.value, 3)}</td>
            <td>{_ci_span(*(hit.ci or (None, None)))}</td><td></td></tr>
        <tr><td>Sharpe (per trade)</td><td>{_fmt(sharpe.value, 3)}</td>
            <td>{_ci_span(*(sharpe.ci or (None, None)))}</td>
            <td>maxDD {result.filled.max_drawdown:+.2f}</td></tr>
        {beta_row}
        {dsr_row}
      </table>
      <div class="cols">
        <div><span class="k">opportunities</span> {result.n_opportunities}</div>
        <div><span class="k">filled</span> {result.n_filled} ({result.fill_rate:.1%})</div>
        <div><span class="k">selection cost</span> {result.selection_cost:+.4f}/contract</div>
        <div><span class="k">min detectable effect</span>
             {'&infin;' if result.mde == float('inf') else f'{result.mde:.4f}'}</div>
      </div>
      {'<ul class="notes">' + notes + '</ul>' if notes else ''}
    </section>"""


def _summary_table(results: list[ScanResult]) -> str:
    rows = []
    for r in results:
        color = VERDICT_COLORS.get(r.verdict, "#94a3b8")
        rows.append(
            f"<tr><td style='border-left:4px solid {color}'>{html.escape(r.hypothesis_id)}</td>"
            f"<td>{r.verdict.value}</td>"
            f"<td>{_fmt(r.filled.mean_pnl.value)}</td>"
            f"<td>{r.n_opportunities}</td><td>{r.n_filled}</td>"
            f"<td>{r.fill_rate:.1%}</td><td>{r.n_series}</td></tr>"
        )
    return (
        "<table class='summary'><tr><th>hypothesis</th><th>verdict</th><th>mean PnL</th>"
        "<th>opps</th><th>filled</th><th>fill rate</th><th>series</th></tr>"
        + "".join(rows)
        + "</table>"
    )


CSS = """
:root { color-scheme: light dark; }
body { font-family: var(--font-family, ui-sans-serif, system-ui, sans-serif);
  color: var(--foreground, #1a1a1a); background: transparent; margin: 0; padding: 16px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0; }
.sub { color: var(--muted-foreground, #666); font-size: 12px; margin: 0 0 16px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; margin: 8px 0; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--border, #ddd); }
th { color: var(--muted-foreground, #666); font-weight: 600; }
.card { border: 1px solid var(--border, #ddd); border-left: 4px solid var(--vc, #999);
  border-radius: 8px; padding: 12px 14px; margin: 0 0 14px;
  background: var(--card, transparent); }
.card header { display: flex; align-items: center; gap: 8px; }
.pill { font-size: 11px; font-weight: 700; letter-spacing: .04em;
  background: var(--vc, #999); color: #fff; padding: 2px 8px; border-radius: 999px; }
.hash { margin-left: auto; color: var(--muted-foreground, #666); font-size: 11px;
  font-family: ui-monospace, monospace; }
.reason { font-size: 12px; color: var(--muted-foreground, #666); margin: 6px 0; }
.eq { width: 100%; height: 70px; display: block; margin: 8px 0;
  background: var(--accent, rgba(127,127,127,.06)); border-radius: 6px; }
.eq .zero { stroke: var(--border, #ccc); stroke-dasharray: 3 3; stroke-width: 1; }
.eq .muted { fill: var(--muted-foreground, #888); font-size: 10px; }
.cols { display: flex; flex-wrap: wrap; gap: 6px 20px; font-size: 12px; margin-top: 6px; }
.cols .k { color: var(--muted-foreground, #666); }
.sig { color: #16a34a; }
.notsig, .muted { color: var(--muted-foreground, #888); }
.notes { font-size: 12px; color: var(--muted-foreground, #666); margin: 8px 0 0 16px; }
.summary td { font-variant-numeric: tabular-nums; }
"""


def render_report(results: list[ScanResult], meta: ReportMeta | None = None) -> str:
    """Render the full HTML page for a batch of scans."""
    meta = meta or ReportMeta()
    counts: dict[str, int] = {}
    for r in results:
        counts[r.verdict.value] = counts.get(r.verdict.value, 0) + 1
    tally = " · ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(meta.title)}</title><style>{CSS}</style></head>
<body>
  <h1>{html.escape(meta.title)}</h1>
  <p class="sub">
    {html.escape(meta.generated_at)} · universe: {html.escape(meta.universe or '—')}
    · source: {html.escape(meta.source or '—')} · search size: {meta.n_trials} trial(s)
    <br>{html.escape(tally)}
  </p>
  {_summary_table(results)}
  {''.join(_card(r) for r in results)}
  <p class="sub">
    Every interval resamples whole series, not rows. A Sharpe is deflated by the
    declared search size; a "PASS" requires the registered bar, an interval that
    excludes zero, and the registered minimum sample.
  </p>
</body></html>"""


def write_report(
    results: list[ScanResult],
    path: Path,
    meta: ReportMeta | None = None,
) -> Path:
    """Write the HTML page plus the machine-readable JSON beside it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(results, meta), encoding="utf-8")
    payload = {
        "meta": (meta or ReportMeta()).__dict__,
        "results": [r.to_dict() for r in results],
    }
    path.with_suffix(".json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path
