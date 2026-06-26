"""View — Product analytics overview (/events), Mixpanel/PostHog-style.

Big active-users number → trends → top events / screens bars → stickiness →
platform breakdown → funnel (configurable; steps TBD). Google-auth-gated via the
shared dashboard gate. Chart.js (CDN) for the time-series + bars; the rest is
server-rendered HTML in the same calm style as /headline. Honest about small N —
the events pipeline only started flowing recently, so we show counts + a
"still filling in" banner and never draw a confident trend over a day of data.
"""

import json

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

import clickhouse
import config
from auth import require_dashboard_access
from repositories import events_repo

router = APIRouter(tags=["Views"])

_CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4"


def _pct(x: float | None) -> str:
    return f"{x * 100:.0f}%" if x is not None else "—"


def _delta(x: float | None) -> str:
    # WoW change badge; green up / red down, blank when there's no prior week.
    if x is None:
        return ""
    sign, cls = ("+", "up") if x >= 0 else ("−", "down")
    return f" <span class='delta {cls}'>{sign}{abs(x) * 100:.0f}% WoW</span>"


def _funnel_html(funnel: list[dict]) -> str:
    # Conversion (vs the first step) + per-step drop-off — the headline view.
    if not funnel:
        return "<p class='muted'>No funnel data in the window yet.</p>"
    base = funnel[0]["users"] or 0
    rows, prev = [], None
    for f in funnel:
        drop = (
            "—"
            if prev is None or not prev
            else f"−{(prev - f['users']) / prev * 100:.0f}%"
        )
        rows.append(
            f"<tr><td>{f['step']}</td><td>{f['users']}</td>"
            f"<td>{_pct(f['conversion'])}</td><td>{drop}</td></tr>"
        )
        prev = f["users"]
    overall = _pct(funnel[-1]["conversion"])
    return (
        f"<div class='big'>{overall}</div><div class='label'>"
        f"{funnel[0]['step']} → {funnel[-1]['step']} &nbsp;(n={base})</div>"
        "<table><thead><tr><th>Step</th><th>Users</th><th>vs start</th>"
        "<th>Drop-off</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _influencer_html(influencers: list[tuple]) -> str:
    if not influencers:
        return (
            "<p class='muted'>No influencer id on the chat events in this window "
            "yet.</p>"
        )
    rows = "".join(
        f"<tr><td>{i}</td><td>{t or '—'}</td><td>{int(ch)}</td>"
        f"<td>{int(m)}</td><td>{int(u)}</td></tr>"
        for i, t, ch, m, u in influencers
    )
    return (
        "<table><thead><tr><th>Influencer</th><th>Type</th><th>Chats</th>"
        f"<th>Messages</th><th>Users</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _retention_html(retention: list[tuple]) -> str:
    # PostHog-style weekly cohort grid; a row flattening above zero is the tell.
    if not retention:
        return "<p class='muted'>No cohorts yet.</p>"
    cohorts: dict[str, dict] = {}
    for wk, sz, off, users in retention:
        c = cohorts.setdefault(str(wk), {"size": int(sz), "cells": {}})
        if off is not None:
            c["cells"][int(off)] = int(users)
    max_col = min(
        8, max((max(c["cells"], default=0) for c in cohorts.values()), default=0)
    )
    header = "".join(f"<th>W{k}</th>" for k in range(max_col + 1))
    body = []
    for wk in sorted(cohorts, reverse=True):
        c = cohorts[wk]
        sz = c["size"]
        faint = " class='faint'" if sz < config.SMALL_SAMPLE_THRESHOLD else ""
        tds = []
        for k in range(max_col + 1):
            if k in c["cells"]:
                pct = round(100 * c["cells"][k] / sz) if sz else 0
                tds.append(f"<td>{pct}%<br><small>{c['cells'][k]}</small></td>")
            else:
                tds.append("<td class='muted'>·</td>")
        body.append(
            f"<tr{faint}><th class='rowhead'>{wk}<br><small>n={sz}</small></th>"
            f"{''.join(tds)}</tr>"
        )
    return (
        "<table><thead><tr><th class='rowhead'>Cohort (first-seen week)</th>"
        f"{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


@router.get("/events", response_class=HTMLResponse)
async def events(_access: str = Depends(require_dashboard_access)) -> str:
    client = await clickhouse.get_client()
    try:
        active = await events_repo.active_users(client)
        dau = await events_repo.dau_trend(client)
        volume = await events_repo.event_volume(client)
        top_events = await events_repo.top_events(client)
        top_views = await events_repo.top_views(client)
        platforms = await events_repo.platform_breakdown(client)
        span_days = await events_repo.data_span_days(client)
        funnel = await events_repo.funnel(client, events_repo.FUNNEL_STEPS)
        influencers = await events_repo.influencer_engagement(client)
        depth = await events_repo.message_depth(client)
        returning = await events_repo.returning_user_rate(client)
        retention = await events_repo.event_retention(client)
        wow = await events_repo.wow(client)
        by_action = await events_repo.events_by_action(client)
        recent = await events_repo.recent_activity(client)
    except Exception:
        # raw_events not there yet / no reader access yet — warming up, not 500.
        return _shell("<p>Events are still warming up — check back shortly.</p>")

    total_events = sum(int(n) for _, n in volume)
    # Chart.js specs (lines + bars), injected as JSON; one tiny init loops them.
    charts = [
        {
            "id": "dau",
            "type": "line",
            "label": "Daily active users",
            "labels": [str(d) for d, _ in dau],
            "data": [int(v) for _, v in dau],
        },
        {
            "id": "vol",
            "type": "line",
            "label": "Events / day",
            "labels": [str(d) for d, _ in volume],
            "data": [int(v) for _, v in volume],
        },
        {
            "id": "topev",
            "type": "bar",
            "label": "Events (7d)",
            "labels": [f"{c}/{a}" for c, a, _ in top_events],
            "data": [int(n) for _, _, n in top_events],
        },
        {
            "id": "views",
            "type": "bar",
            "label": "Views (7d)",
            "labels": [s for s, _ in top_views],
            "data": [int(n) for _, n in top_views],
        },
        {
            "id": "byaction",
            "type": "bar",
            "label": "Events by type (7d)",
            "labels": [a for a, _ in by_action],
            "data": [int(n) for _, n in by_action],
        },
    ]

    banner = ""
    if span_days < 3:
        banner = (
            "<div class='banner'>📈 The events pipeline started flowing "
            f"~{span_days:.1f} days ago — {total_events} events so far. Numbers "
            "are real but <strong>still filling in</strong>; don't read a trend "
            "into a partial day yet.</div>"
        )

    plat_rows = "".join(
        f"<tr><td>{p or '—'}</td><td>{v or '—'}</td><td>{int(n)}</td></tr>"
        for p, v, n in platforms
    )
    if funnel:
        charts.append(
            {
                "id": "funnel",
                "type": "bar",
                "label": "Users",
                "labels": [f["step"] for f in funnel],
                "data": [int(f["users"]) for f in funnel],
            }
        )
    funnel_html = _funnel_html(funnel)
    if depth:
        charts.append(
            {
                "id": "depth",
                "type": "bar",
                "label": "Users",
                "labels": [b for b, _ in depth],
                "data": [int(u) for _, u in depth],
            }
        )

    funnel_conv = _pct(funnel[-1]["conversion"]) if funnel else "—"
    glance = f"""
<div class="glance">
  <div class="gtile"><div class="label">Funnel conversion</div><div class="huge">{funnel_conv}</div>
    <div class="sub">home → first message</div></div>
  <div class="gtile"><div class="label">Stickiness</div><div class="huge">{_pct(active["stickiness"])}</div>
    <div class="sub">DAU / MAU</div></div>
  <div class="gtile"><div class="label">Returning users</div><div class="huge">{_pct(returning["rate"])}</div>
    <div class="sub">seen on &gt;1 day &nbsp;<small>n={returning["n"]}</small></div></div>
</div>"""

    body = f"""
{banner}
<h1>Product analytics</h1>

<div class="card pmf"><h2>PMF glance</h2>{glance}</div>

<div class="card recent"><h2>Right now</h2>
  <div id="recent" class="big">{recent["events_15m"]} events · {recent["users_15m"]} users <small>last 15 min</small></div>
  <div class="muted">{recent["events_60m"]} in the last hour · auto-refreshes every 30s</div>
</div>

<div class="tiles">
  <div class="tile"><div class="label">Daily active users</div><div class="big">{active["dau"]}</div></div>
  <div class="tile"><div class="label">Weekly active</div><div class="big">{active["wau"]}</div></div>
  <div class="tile"><div class="label">Monthly active</div><div class="big">{active["mau"]}</div> <small>n={active["mau"]}</small></div>
  <div class="tile"><div class="label">Stickiness (DAU/MAU)</div><div class="big">{_pct(active["stickiness"])}</div></div>
</div>

<div class="grid2">
  <div class="card"><h2>Active users (30d){_delta(wow["users_delta"])}</h2><canvas id="dau"></canvas></div>
  <div class="card"><h2>Event volume (30d){_delta(wow["events_delta"])}</h2><canvas id="vol"></canvas></div>
  <div class="card"><h2>Top events (7d)</h2><canvas id="topev"></canvas></div>
  <div class="card"><h2>Top views (7d)</h2><canvas id="views"></canvas></div>
  <div class="card"><h2>Events by type (7d)</h2><canvas id="byaction"></canvas></div>
</div>

<div class="card"><h2>By platform &amp; app version (7d)</h2>
  <table><thead><tr><th>Platform</th><th>App version</th><th>Events</th></tr></thead>
  <tbody>{plat_rows or "<tr><td colspan=3 class='muted'>no data</td></tr>"}</tbody></table>
</div>

<div class="card"><h2>Funnel — home → first message</h2>
  {('<canvas id="funnel"></canvas>' if funnel else "")}
  {funnel_html}
</div>

<div class="card"><h2>Which bots create love (30d)</h2>{_influencer_html(influencers)}</div>

<div class="card"><h2>New-user weekly retention</h2>{_retention_html(retention)}</div>

<div class="card"><h2>Engagement depth — messages / user</h2>
  {('<canvas id="depth"></canvas>' if depth else "<p class='muted'>no messages yet</p>")}
  <p class='muted'>How many users sent 1 / 2-5 / 6-20 / 21+ messages — the power-user curve.</p>
</div>

<script src="{_CHART_JS}"></script>
<script>
const CHARTS = {json.dumps(charts)};
for (const c of CHARTS) {{
  new Chart(document.getElementById(c.id), {{
    type: c.type,
    data: {{ labels: c.labels, datasets: [{{ label: c.label, data: c.data,
      borderColor: '#2563eb', backgroundColor: 'rgba(37,99,235,0.5)', tension: 0.2 }}] }},
    options: {{ plugins: {{ legend: {{ display: false }} }},
      indexAxis: c.type === 'bar' ? 'y' : 'x',
      scales: {{ x: {{ grid: {{ display: false }} }} }} }}
  }});
}}
async function refreshRecent() {{
  try {{
    const r = await (await fetch('/events/recent')).json();
    document.getElementById('recent').innerHTML =
      `${{r.events_15m}} events · ${{r.users_15m}} users <small>last 15 min</small>`;
  }} catch (e) {{}}
}}
setInterval(refreshRecent, 30000);
</script>
"""
    return _shell(body)


@router.get("/events/recent")
async def events_recent(_access: str = Depends(require_dashboard_access)):
    # Lightweight JSON for the auto-refreshing "Right now" tile.
    client = await clickhouse.get_client()
    try:
        return await events_repo.recent_activity(client)
    except Exception:
        return {"events_15m": 0, "events_60m": 0, "users_15m": 0}


def _shell(body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analytics — product</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 1.5rem;
         color: #1a1a1a; max-width: 60rem; }}
  h1 {{ font-size: 1.4rem; }} h2 {{ font-size: 0.95rem; color: #444; }}
  .banner {{ background: #fff8e1; border: 1px solid #ffe082; border-radius: 10px;
            padding: 0.75rem 1rem; font-size: 0.9rem; margin-bottom: 1rem; }}
  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(8rem,1fr));
           gap: 0.75rem; }}
  .tile {{ border: 1px solid #eee; border-radius: 12px; padding: 1rem; }}
  .label {{ color: #666; font-size: 0.8rem; }} .big {{ font-size: 1.8rem; font-weight: 600; }}
  small {{ color: #999; }}
  .grid2 {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(18rem,1fr));
           gap: 1rem; margin-top: 1rem; }}
  .card {{ border: 1px solid #eee; border-radius: 12px; padding: 1rem; margin-top: 1rem; }}
  table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
  th, td {{ border-bottom: 1px solid #f0f0f0; padding: 0.4rem 0.6rem; text-align: left; }}
  .muted {{ color: #999; font-size: 0.85rem; }}
  code {{ background: #f4f4f4; padding: 0 0.25rem; border-radius: 4px; }}
  .pmf {{ background: #f7faff; }}
  .glance {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(10rem,1fr)); gap: 1rem; }}
  .gtile .huge {{ font-size: 2.4rem; font-weight: 700; color: #1d4ed8; line-height: 1.1; }}
  .gtile .sub {{ color: #888; font-size: 0.8rem; }}
  .rowhead {{ font-weight: 600; color: #444; }}
  tr.faint td, tr.faint .rowhead {{ opacity: 0.45; }}
  .delta {{ font-size: 0.75rem; font-weight: 500; }}
  .delta.up {{ color: #16a34a; }} .delta.down {{ color: #dc2626; }}
  .recent {{ background: #f6fff8; }}
  h2 {{ font-weight: 600; }}
</style></head><body>
{body}
</body></html>"""
