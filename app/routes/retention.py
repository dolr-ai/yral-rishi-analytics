"""View 1 — the Comeback Curve (cohort retention grid).

*Answers: "Do engaged users come back, and does the curve flatten above zero?"*

Rows are weekly cohorts (by the week of a user's first engaged session);
columns are weeks-since. Each cell is the % of that cohort with ANY session
that week, and every row shows its raw N. A row that flattens above zero is the
PMF tell; column W1 is the headline W1-return number, shown here per-cohort so
we can see whether the live number holds for NEW cohorts or is flattered by the
legacy base. Full stack — query in retention_repo, HTML rendered here.
"""

import datetime

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

import config
import database
from auth import require_dashboard_access
from repositories import retention_repo

router = APIRouter(tags=["Views"])

MAX_COLS = 8  # show W0..W8; wider grids stop being legible on a phone


def _monday(d: datetime.date) -> datetime.date:
    return d - datetime.timedelta(days=d.weekday())


def _cell(active: int, size: int) -> str:
    pct = round(100 * active / size) if size else 0
    return f"{pct}%<br><small>{active}</small>"


@router.get("/retention", response_class=HTMLResponse)
async def retention(_access: str = Depends(require_dashboard_access)) -> str:
    pool = await database.get_pool()
    try:
        rows = await retention_repo.cohort_retention(pool)
    except asyncpg.exceptions.UndefinedTableError:
        return _shell(
            "Comeback Curve",
            "<p>Warming up… numbers appear after the first refresh.</p>",
        )

    if not rows:
        return _shell("Comeback Curve", "<p>No engaged cohorts yet — too early.</p>")

    # Pivot into {cohort_week_date: {"size": n, "cells": {offset: active}}}.
    cohorts: dict[datetime.date, dict] = {}
    for r in rows:
        wk = r["cohort_week"].date()
        c = cohorts.setdefault(wk, {"size": r["cohort_size"], "cells": {}})
        if r["week_offset"] is not None:
            c["cells"][r["week_offset"]] = r["active_users"]

    current_week = _monday(datetime.datetime.now(datetime.timezone.utc).date())
    # Widest column any cohort is actually old enough to have reached, capped.
    max_col = min(
        MAX_COLS,
        max((current_week - wk).days // 7 for wk in cohorts),
    )

    header = "".join(f"<th>W{k}</th>" for k in range(max_col + 1))
    body = []
    for wk in sorted(cohorts, reverse=True):
        c = cohorts[wk]
        size = c["size"]
        faint = " class='faint'" if size < config.SMALL_SAMPLE_THRESHOLD else ""
        max_off = (current_week - wk).days // 7
        tds = []
        for k in range(max_col + 1):
            if k > max_off:
                tds.append("<td class='future'>·</td>")  # cohort not this old yet
            else:
                tds.append(f"<td>{_cell(c['cells'].get(k, 0), size)}</td>")
        body.append(
            f"<tr{faint}><th class='rowhead'>{wk.isoformat()}<br>"
            f"<small>n={size}</small></th>{''.join(tds)}</tr>"
        )

    table = (
        "<table><thead><tr><th class='rowhead'>Cohort (first engaged week)</th>"
        f"{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )
    caption = (
        f"<p class='caption'>Engaged = ≥{config.ENGAGED_MIN_USER_MSGS} user "
        "messages in a sitting. Each cell: % of the cohort with any session that "
        "week (small number = how many users). <strong>W1</strong> is the return "
        "number; a row that stays above zero as it moves right is the love signal. "
        f"Faint rows are below n={config.SMALL_SAMPLE_THRESHOLD} — too small to "
        "trust yet.</p>"
    )
    return _shell("Comeback Curve", table + caption)


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analytics — {title}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem;
         color: #1a1a1a; }}
  h1 {{ font-size: 1.3rem; }}
  table {{ border-collapse: collapse; font-variant-numeric: tabular-nums; }}
  th, td {{ border: 1px solid #ececec; padding: 0.5rem 0.7rem; text-align: center; }}
  .rowhead {{ text-align: left; color: #444; font-weight: 600; }}
  thead th {{ background: #fafafa; color: #666; font-weight: 600; }}
  small {{ color: #999; font-weight: 400; }}
  tr.faint td, tr.faint .rowhead {{ opacity: 0.45; }}
  td.future {{ color: #ccc; }}
  .caption {{ color: #666; font-size: 0.85rem; max-width: 44rem; margin-top: 1rem; }}
</style></head><body>
<h1>{title}</h1>
{body}
</body></html>"""
