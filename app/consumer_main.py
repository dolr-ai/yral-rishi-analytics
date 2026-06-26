"""analytics-events-consumer — its OWN Swarm service (not the dashboard app).

Snowplow enriched events off the Strimzi bridge → ClickHouse. The flow:
  poll the bridge → base64-decode → parse the Snowplow TSV → buffer →
  on 1000 rows OR 5s, batch-insert to ClickHouse → THEN commit the bridge
  offsets (commit-after-write = at-least-once; CH ReplacingMergeTree dedups).

Runs as a small FastAPI app so /health can expose buffer + throughput; the
consume loop is a lifespan background task. Sentry captures parse/insert errors.
"""

import asyncio
import base64
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
from services import snowplow
from services.clickhouse_sink import ClickHouseSink
from services.kafka_bridge import BridgeConsumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

if config.SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(dsn=config.SENTRY_DSN, traces_sample_rate=0.0)

# Health/observability counters (true broker lag needs an offsets query against
# the bridge — a follow-up; last_poll_records is the practical backpressure
# signal: sustained max-size polls mean we're behind).
_stats = {
    "buffer": 0,
    "inserted_total": 0,
    "parse_errors": 0,
    "last_poll_records": 0,
    "seconds_since_flush": 0.0,
    "consumer_alive": True,
}


def _capture(exc: Exception) -> None:
    if config.SENTRY_DSN:
        import sentry_sdk

        sentry_sdk.capture_exception(exc)


async def _consume_loop(bridge: BridgeConsumer, sink: ClickHouseSink) -> None:
    buffer: list[tuple] = []
    last_flush = time.monotonic()
    while True:
        values = await bridge.poll()
        _stats["last_poll_records"] = len(values)
        for value in values:
            try:
                tsv = base64.b64decode(value).decode("utf-8")
                record = snowplow.parse(tsv)
                # event_id + collector_tstamp are required by the schema (and
                # the dedup/partition keys); skip anything missing them.
                if not record.get("event_id") or not record.get("collector_tstamp"):
                    _stats["parse_errors"] += 1
                    continue
                buffer.append(snowplow.to_row(record))
            except Exception as exc:  # noqa: BLE001 — never let one bad record kill the loop
                _stats["parse_errors"] += 1
                logger.exception("snowplow parse error")
                _capture(exc)

        _stats["buffer"] = len(buffer)
        _stats["seconds_since_flush"] = time.monotonic() - last_flush
        due = len(buffer) >= config.EVENTS_BATCH_SIZE or (
            _stats["seconds_since_flush"] >= config.EVENTS_BATCH_SECONDS
        )
        if buffer and due:
            try:
                await sink.insert(buffer)
                await bridge.commit()  # only AFTER the write succeeds
                _stats["inserted_total"] += len(buffer)
                buffer.clear()
            except Exception as exc:  # noqa: BLE001
                # Don't commit — the same records re-deliver next round and CH
                # dedups. Keep the buffer; retry on the next flush.
                logger.exception("insert/commit failed; batch will retry")
                _capture(exc)
            last_flush = time.monotonic()
            _stats["buffer"] = len(buffer)

        # The bridge's `timeout=` blocks idle polls server-side, but guard
        # against a misbehaving bridge that returns 200 [] instantly — never
        # hot-loop. Only fires when there were no records (no event latency).
        if not values:
            await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting analytics-events-consumer")
    sink = ClickHouseSink()
    try:
        await sink.ensure_table()
        logger.info("raw_events table ensured")
    except Exception as exc:  # noqa: BLE001
        logger.exception("ensure_table failed at startup")
        _capture(exc)

    bridge = BridgeConsumer()
    task = asyncio.create_task(_consume_loop(bridge, sink))

    yield

    _stats["consumer_alive"] = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await bridge.close()
    await sink.close()
    logger.info("Shutdown complete")


app = FastAPI(title="analytics-events-consumer", lifespan=lifespan)


@app.get("/health")
async def health():
    # 200 always (the service is up); the body carries the signals to watch.
    return {"status": "ok", **_stats}
