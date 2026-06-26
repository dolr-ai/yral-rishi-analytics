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
"""

import logging

import httpx

import config

logger = logging.getLogger(__name__)

_JSON = "application/vnd.kafka.v2+json"
_BINARY = "application/vnd.kafka.binary.v2+json"


class BridgeConsumer:
    def __init__(self):
        self._http = httpx.AsyncClient(
            headers={"X-Bearer-Token": config.KAFKA_BRIDGE_TOKEN},
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
        )
        self._base = None  # public instance base, set on create

    async def _create(self) -> None:
        group = config.KAFKA_CONSUMER_GROUP
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

    async def _ensure(self) -> None:
        if self._base is None:
            await self._create()

    async def poll(self) -> list[str]:
        """Return this poll's record values (base64 strings). On a 404 (bridge
        restart) recreate and return empty this round; on other transient HTTP
        errors log and return empty — the loop just polls again."""
        await self._ensure()
        try:
            resp = await self._http.get(
                f"{self._base}/records?timeout={config.KAFKA_POLL_TIMEOUT_MS}",
                headers={"Accept": _BINARY},
            )
            if resp.status_code == 404:
                logger.warning("bridge consumer gone (404) — recreating")
                self._base = None
                await self._ensure()
                return []
            resp.raise_for_status()
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
        if self._base is not None:
            try:
                await self._http.delete(self._base)
            except httpx.HTTPError:
                pass
        await self._http.aclose()
