import os


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")


# App
APP_NAME = _env("APP_NAME", "Yral Analytics")
APP_VERSION = _env("APP_VERSION", "0.1.0")
ENVIRONMENT = _env("ENVIRONMENT", "development")
HOST = _env("HOST", "0.0.0.0")
PORT = _env_int("PORT", 8001)

# Database — points at a Patroni READ REPLICA, never the leader. The heavy
# analytics reads must never load the node that serves the chat path. The
# analytics_ro role is itself read-only + statement_timeout-capped at the
# DB level (see db/setup_analytics_ro.sql); the replica endpoint here is the
# second layer of that belt-and-braces. Read as a Docker-secret file first
# (see database.py) so the DSN never lives in an env var or in git.
ANALYTICS_DB_DSN = _env("ANALYTICS_DB_DSN")

# The leader (primary) DSN for the analytics_rw role — used ONLY by the hourly
# sessionization refresh job to write the small finished summary into the
# analytics schema (Option B, Rishi 2026-06-13). Empty until the rw role/secret
# exist; while empty the refresh loop stays dormant (main.py) so nothing tries
# to reach the leader. Read as a Docker-secret file first (see database.py).
ANALYTICS_DB_DSN_RW = _env("ANALYTICS_DB_DSN_RW")

# Hourly refresh of analytics.analytics_sessions (mirrors the chat service's
# _trending_stats_refresher cadence). The heavy read runs on the replica; only
# the small result is written to the leader.
SESSIONS_REFRESH_INTERVAL_SEC = _env_int("SESSIONS_REFRESH_INTERVAL_SEC", 3600)

# The refresh's heavy aggregation scan is the one analytics_ro query allowed to
# exceed the 5s default — raised to this, scoped to the refresh transaction
# only (SET LOCAL), so the 5s cap still protects every user-facing read.
SESSIONS_REFRESH_READ_TIMEOUT = _env("SESSIONS_REFRESH_READ_TIMEOUT", "60s")

# Redis — login-session storage only (ephemeral; a blip just forces re-login).
# Durable audit lives in Postgres (analytics schema), not here. Same Sentinel
# cluster the chat service uses, reachable from rishi-6.
REDIS_HOST = _env("REDIS_HOST", "redis-sentinel-rishi-4")
REDIS_PORT = _env_int("REDIS_PORT", 26379)
REDIS_SENTINEL_MASTER = _env("REDIS_SENTINEL_MASTER", "mymaster")

# Sessionization knob — a new "session" (one sitting of back-and-forth) starts
# when the gap since the previous message in a conversation exceeds this. 20,
# not 30: mobile users idle a lot and 30 would merge two genuine sittings;
# Replika-style research uses ~15, so 20 is a safe middle (design §3.2).
# Hot-editable so we can tune without a redeploy.
SESSION_GAP_MINUTES = _env_int("SESSION_GAP_MINUTES", 20)

# An "engaged session" = a sitting with at least this many USER messages — a
# genuine back-and-forth, not a one-shot "hi" and bounce. Starts at 4 (not 6)
# so at 10% rollout we surface signal instead of "n=2 engaged today"; we
# ratchet to 6 once a week of data exists (design §1.2).
ENGAGED_MIN_USER_MSGS = _env_int("ENGAGED_MIN_USER_MSGS", 4)

# Below this many data points a number is too noisy to draw a trend from — we
# show "too early to trust" instead of a fake sparkline (design §8). Honesty
# about small-sample uncertainty is a first-class feature, not a footnote.
SMALL_SAMPLE_THRESHOLD = _env_int("SMALL_SAMPLE_THRESHOLD", 30)

# Temporary shared-secret token gating the headline route so Rishi can see
# first signal BEFORE Google login (Phase B) is wired. Set via Swarm secret /
# env; empty by default so the route denies everyone until a token exists.
# RETIRED in Phase B (PR B2) once real auth lands — it is not real auth.
HEADLINE_TOKEN = _env("HEADLINE_TOKEN")

# Google Workspace OAuth — restricts login to @gobazzinga.io. The client ID +
# secret are created by Rishi in Google Cloud Console at Phase B and injected
# via Swarm secrets; these are placeholders so the module imports cleanly
# before that exists. NOT wired in Phase 0 (no auth flow yet).
GOOGLE_OAUTH_CLIENT_ID = _env("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = _env("GOOGLE_OAUTH_CLIENT_SECRET")
GOOGLE_OAUTH_REDIRECT_URI = _env(
    "GOOGLE_OAUTH_REDIRECT_URI",
    "https://analytics.rishi.yral.com/auth/google/callback",
)
ALLOWED_EMAIL_DOMAIN = _env("ALLOWED_EMAIL_DOMAIN", "gobazzinga.io")

# Login sessions (Phase B). The login session is an opaque high-entropy id
# stored in Redis (a blip just forces re-login); the cookie holds only that id.
SESSION_COOKIE_NAME = _env("SESSION_COOKIE_NAME", "analytics_session")
SESSION_TTL_SECONDS = _env_int("SESSION_TTL_SECONDS", 7 * 24 * 3600)
# Secure cookie by default (HTTPS-only). Set false only for local http testing.
SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", True)

# Signs the SHORT-LIVED OAuth-handshake cookie (Starlette SessionMiddleware,
# used only to carry state/nonce between /auth/login and the callback — NOT the
# login session itself). Must be stable + shared across workers/replicas, so it
# comes from a Swarm secret; read as a file first (see auth.py). Auth stays
# dormant until this exists.
SESSION_SECRET = _env("SESSION_SECRET")
