"""Strimzi Kafka HTTP-bridge client (httpx) for the events consumer.

Drives the bridge REST API: create consumer → subscribe → poll /records →
commit. Two probe-confirmed quirks (2026-06-25):
  - the create-consumer response returns an `http://` base_uri that 301-redirects,
    so we build every instance URL from the PUBLIC base + instance_id ourselves;
  - the bridge is a single in-memory replica — the consumer instance dies on a
    bridge-pod restart, surfacing as 404 → we recreate + resubscribe.

Auth is the `X-Bearer-Token` header (Vault OIDC → Swarm secret). Offsets are
committed manually (enable.auto.commit=false) only after the ClickHouse write
succeeds, so re-delivery on crash/restart is bounded (at-least-once). Because
commits persist to the consumer GROUP, a recreated instance resumes from the
last committed offset; auto.offset.reset=latest only applies on the very first
start (skip the historical backlog).

Recreate discipline (added 2026-08-07 after the bridge-memory incident): a
flapping/overloaded bridge 404s our instance repeatedly. Naively recreating on
every poll spawned a new consumer instance every ~0.5s and never deleted the
old ones — each orphan holds a Kafka consumer + fetch buffers on the bridge, so
its memory ran away. We now (1) track the instance_id and best-effort DELETE any
stale one before creating a new one, and (2) back off exponentially between
recreate attempts (reset on a healthy poll).
"""

import asyncio
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_JSON = "application/vnd.kafka.v2+json"
_BINARY = "application/vnd.kafka.binary.v2+json"

# Exponential backoff between recreate attempts. Caps how fast a flapping bridge
# can be handed new consumer instances; a healthy poll resets the counter.
_RECREATE_BACKOFF_BASE_S = 1.0
_RECREATE_BACKOFF_MAX_S = 30.0


class BridgeConsumer:
    def __init__(self):
        self._http = httpx.AsyncClient(
            headers={"X-Bearer-Token": config.KAFKA_BRIDGE_TOKEN},
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
        )
        self._base = None  # public instance base, set on create
        self._instance_id = None  # tracked so we can DELETE a stale instance
        self._recreate_attempts = 0  # drives backoff; reset on a healthy poll

    async def _delete_instance(self) -> None:
        """Best-effort DELETE of the currently-tracked instance so orphaned
        consumers don't pile up on the bridge. A 404/error just means it's
        already gone — idempotent."""
        if self._instance_id is None:
            return
        group = config.KAFKA_CONSUMER_GROUP
        url = (
            f"{config.KAFKA_BRIDGE_URL}/consumers/{group}/instances/{self._instance_id}"
        )
        try:
            await self._http.delete(url)
        except httpx.HTTPError:
            pass
        self._instance_id = None

    async def _create(self) -> None:
        group = config.KAFKA_CONSUMER_GROUP
        # Delete any instance we still hold a handle to BEFORE creating a new
        # one, so a flapping bridge can't accumulate orphaned consumer instances.
        await self._delete_instance()
        resp = await self._http.post(
            f"{config.KAFKA_BRIDGE_URL}/consumers/{group}",
            headers={"Content-Type": _JSON},
            json={
                "format": "binary",
                "auto.offset.reset": config.KAFKA_AUTO_OFFSET_RESET,
                "enable.auto.commit": False,
            },
        )
        resp.raise_for_status()
        instance_id = resp.json()["instance_id"]
        self._instance_id = instance_id
        # Build from the PUBLIC base — the returned base_uri is http:// and 301s.
        self._base = (
            f"{config.KAFKA_BRIDGE_URL}/consumers/{group}/instances/{instance_id}"
        )
        sub = await self._http.post(
            f"{self._base}/subscription",
            headers={"Content-Type": _JSON},
            json={"topics": [config.KAFKA_TOPIC]},
        )
        sub.raise_for_status()
        logger.info("bridge consumer created + subscribed (%s)", instance_id)

    async def _open(self) -> None:
        """Ensure a live consumer instance. Applies exponential backoff between
        recreate attempts so a flapping/overloaded bridge isn't hammered with a
        new instance every poll. The first open (no prior failures) is immediate."""
        if self._recreate_attempts > 0:
            delay = min(
                _RECREATE_BACKOFF_BASE_S * 2 ** (self._recreate_attempts - 1),
                _RECREATE_BACKOFF_MAX_S,
            )
            logger.warning(
                "bridge consumer recreate #%d — backing off %.1fs",
                self._recreate_attempts,
                delay,
            )
            await asyncio.sleep(delay)
        await self._create()

    async def poll(self) -> list[str]:
        """Return this poll's record values (base64 strings). On a 404 (bridge
        restart/eviction) recreate WITH backoff and return empty this round; on
        other transient HTTP errors log and return empty — the loop polls again."""
        if self._base is None:
            await self._open()
        try:
            resp = await self._http.get(
                f"{self._base}/records?timeout={config.KAFKA_POLL_TIMEOUT_MS}",
                headers={"Accept": _BINARY},
            )
            if resp.status_code == 404:
                logger.warning("bridge consumer gone (404) — recreating")
                self._base = None
                self._recreate_attempts += 1
                await self._open()
                return []
            resp.raise_for_status()
            self._recreate_attempts = 0  # healthy poll → clear the backoff
            return [rec["value"] for rec in resp.json()]
        except httpx.HTTPError as exc:
            logger.warning("bridge poll error: %s", exc)
            return []

    async def commit(self) -> None:
        """Commit consumed offsets for the group (manual commit). Called only
        after the ClickHouse insert succeeds."""
        if self._base is None:
            return
        resp = await self._http.post(
            f"{self._base}/offsets", headers={"Content-Type": _JSON}
        )
        if resp.status_code == 404:
            # Instance died between poll and commit — drop it; the next poll
            # recreates and re-delivers the uncommitted records (at-least-once).
            self._base = None
            return
        resp.raise_for_status()

    async def close(self) -> None:
        # Graceful shutdown deletes the instance so it isn't left orphaned.
        await self._delete_instance()
        self._base = None
        await self._http.aclose()
