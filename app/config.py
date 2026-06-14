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
