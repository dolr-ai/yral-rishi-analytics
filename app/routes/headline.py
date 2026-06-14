"""Phase A — the first-signal headline route.

Renders the three load-bearing numbers (engaged sessions today, second-message
rate, W1 return rate) as plain tiles. Access is the shared dashboard gate
(auth.require_dashboard_access): Google login once it's live, the temporary
shared-secret token until then. This page is deliberately plain — the calm,
beautiful Glance (View 0) supersedes it in Phase C.

Honesty about small samples is built in: any number whose sample size is below
SMALL_SAMPLE_THRESHOLD renders "too early to trust" instead of a figure
(design §8), with its n shown either way.
"""

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

import config
import database
from auth import require_dashboard_access
from repositories import analytics_repo

router = APIRouter(tags=["Headline"])


def _figure(value: str, n: int) -> str:
    # Below the threshold a number is too noisy to believe — say so plainly
    # rather than print a figure that lies (design §8). n is always shown.
    if n < config.SMALL_SAMPLE_THRESHOLD:
        return f"<em>too early to trust</em> <small>(n={n})</small>"
    return f"{value} <small>(n={n})</small>"


def _pct(rate: float | None) -> str:
    return f"{rate * 100:.0f}%" if rate is not None else "—"


def _warming_up_page() -> str:
    # Defense in depth: if the summary table doesn't exist yet (first boot,
    # before the initial refresh has run), show a calm "warming up" instead of
    # a 500. Startup ensures the table, so this is the belt to that braces.
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analytics — warming up</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; margin: 4rem auto;
         max-width: 28rem; color: #1a1a1a; text-align: center; }
</style></head><body>
<h1>Warming up…</h1>
<p>The first numbers appear after the hourly refresh completes. Check back shortly.</p>
</body></html>"""


@router.get("/headline", response_class=HTMLResponse)
async def headline(_access: str = Depends(require_dashboard_access)) -> str:
    pool = await database.get_pool()

    try:
        engaged = await analytics_repo.engaged_sessions_today(pool)
        second = await analytics_repo.second_message_rate(pool)
        w1 = await analytics_repo.w1_return_rate(pool)
    except asyncpg.exceptions.UndefinedTableError:
        # The summary table hasn't been created yet — warming up, not broken.
        return _warming_up_page()

    # Plain markup only — the beautiful version is Phase C. Template lines are
    # exempt from the 100-line logic rule (CLAUDE.md Rule 4).
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analytics — headline</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem;
         color: #1a1a1a; max-width: 40rem; }}
  .tile {{ border: 1px solid #e5e5e5; border-radius: 12px; padding: 1.25rem;
          margin: 1rem 0; }}
  .label {{ color: #666; font-size: 0.85rem; }}
  .value {{ font-size: 1.6rem; font-weight: 600; margin-top: 0.25rem; }}
  .why {{ color: #888; font-size: 0.8rem; margin-top: 0.5rem; }}
  small {{ color: #999; font-weight: 400; }}
</style></head><body>
<h1>Is anyone falling in love?</h1>
<p class="label">Temporary token-gated view. UTC. Numbers are honest about small N.</p>

<div class="tile">
  <div class="label">Engaged sessions today</div>
  <div class="value">{_figure(str(engaged["count"]), engaged["n"])}</div>
  <div class="why">Sittings with ≥{config.ENGAGED_MIN_USER_MSGS} user messages — a real back-and-forth, not a one-shot "hi".</div>
</div>

<div class="tile">
  <div class="label">Second-message rate (last 7 days)</div>
  <div class="value">{_figure(_pct(second["rate"]), second["n"])}</div>
  <div class="why">Of first sittings, the share that got a second user message. The leading indicator.</div>
</div>

<div class="tile">
  <div class="label">W1 return rate of newly-engaged users</div>
  <div class="value">{_figure(_pct(w1["rate"]), w1["n"])}</div>
  <div class="why">Of users first engaged 7–21 days ago, the share who had another <em>engaged</em> conversation within 7 days. THE love number.</div>
</div>
</body></html>"""
