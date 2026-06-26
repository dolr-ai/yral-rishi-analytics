"""Snowplow enriched-TSV parsing for the events consumer.

The bridge delivers each event as a base64-encoded Snowplow ENRICHED TSV line —
131 canonical fields, ~28-29 populated for our atomic events; contexts /
unstruct empty per the 2026-06-25 probe. We parse with the official
snowplow_analytics_sdk (canonical field order + JSON context expansion → a dict
of just the populated fields), then pull the typed columns the ClickHouse schema
needs; the full parsed record is stored as JSON for everything else.
"""

import json
from datetime import datetime, timezone

from snowplow_analytics_sdk.event_transformer import transform


def parse(tsv_line: str) -> dict:
    """Snowplow enriched TSV → canonical-named record dict. Raises on malformed
    input (the caller treats that as a parse error → Sentry, skip the record)."""
    return transform(tsv_line)


def _ts(value):
    # The SDK emits ISO-8601 (…Z); ClickHouse wants a tz-aware UTC datetime.
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def to_row(record: dict) -> tuple:
    """A parsed record → one ClickHouse row (column order matches sink._COLUMNS).
    `event_id` and `collector_tstamp` are required by the schema; the caller
    skips records missing either."""
    return (
        record.get("event_id") or "",
        record.get("event_name") or "",
        _ts(record.get("collector_tstamp")),
        _ts(record.get("derived_tstamp")),
        record.get("app_id") or "",
        record.get("platform") or "",
        record.get("domain_userid") or "",
        record.get("domain_sessionid") or "",
        json.dumps(record, default=str, separators=(",", ":")),
    )
