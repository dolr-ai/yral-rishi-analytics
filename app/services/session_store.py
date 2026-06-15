"""Phase B — login sessions in Redis (Sentinel), the §6.3 ephemeral half.

The login session is an opaque high-entropy id → the team member's email,
stored in Redis with a TTL. The cookie carries only the id; the email is never
in the cookie. A Redis blip just forces a re-login (harmless), which is exactly
why sessions live here and not in Postgres. Durable audit lives in Postgres
(login_audit_repo) — that split mirrors the chat service's "live in Redis,
durable in Postgres" pattern.

Same Redis Sentinel cluster the chat service uses, reachable from rishi-6.
Connection is lazy — importing this module never touches Redis.
"""

import secrets

import config

_master = None

_KEY_PREFIX = "analytics:session:"


def _client():
    # Lazy Sentinel → current master. decode_responses so we get str back.
    # This cluster requires AUTH: the password is needed BOTH for the sentinel
    # connections (sentinel_kwargs) AND for the master connection (password=).
    # Empty password (dev) → no auth.
    global _master
    if _master is None:
        from redis.asyncio.sentinel import Sentinel

        pw = config.REDIS_PASSWORD or None
        sentinel = Sentinel(
            [(config.REDIS_HOST, config.REDIS_PORT)],
            socket_timeout=2.0,
            sentinel_kwargs={"password": pw},
        )
        _master = sentinel.master_for(
            config.REDIS_SENTINEL_MASTER,
            socket_timeout=2.0,
            decode_responses=True,
            password=pw,
        )
    return _master


async def create_session(email: str) -> str:
    sid = secrets.token_urlsafe(32)
    await _client().set(_KEY_PREFIX + sid, email, ex=config.SESSION_TTL_SECONDS)
    return sid


async def get_session(sid: str) -> str | None:
    if not sid:
        return None
    return await _client().get(_KEY_PREFIX + sid)


async def delete_session(sid: str) -> None:
    if sid:
        await _client().delete(_KEY_PREFIX + sid)
