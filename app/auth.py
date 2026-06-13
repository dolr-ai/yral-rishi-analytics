"""Phase B — Google Workspace login, restricted to @gobazzinga.io.

This is the analytics service's OWN auth — it authenticates the internal team
via Google, completely separate from the chat app's user JWTs (different actor,
different codebase). It does NOT import or bend the chat service's auth.

Flow (design §6.1): /auth/login → Google → /auth/google/callback verifies
Google's signature AND that the email domain is exactly gobazzinga.io
(server-side — the picker `hd` hint is not security), then mints an opaque
Redis session and sets an http-only cookie. Every attempt, allow or reject, is
written to the durable Postgres audit. `require_login` guards dashboard routes.

UNVERIFIED until the Google OAuth client + Redis exist (authored 2026-06-13 per
Rishi's go). Auth stays dormant until SESSION_SECRET + the OAuth client id/secret
are provisioned (main.py only mounts it then).
"""

import hmac
import logging
import os

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import config
import database
from repositories import login_audit_repo
from services import session_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Auth"])

_GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"


def _read_secret_file(path: str, fallback: str) -> str:
    # Swarm secrets mount as files (more secure than env), same pattern as the
    # DSNs in database.py. Fall back to the env value for local/dev.
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return fallback


def read_oauth_client_secret() -> str:
    return _read_secret_file(
        "/run/secrets/analytics_google_oauth_client_secret",
        config.GOOGLE_OAUTH_CLIENT_SECRET,
    )


def read_session_secret() -> str:
    # Stable + shared across workers, so it comes from a Swarm secret first.
    return _read_secret_file(
        "/run/secrets/analytics_session_secret", config.SESSION_SECRET
    )


oauth = OAuth()
oauth.register(
    name="google",
    server_metadata_url=_GOOGLE_METADATA,
    client_id=config.GOOGLE_OAUTH_CLIENT_ID,
    client_secret=read_oauth_client_secret(),
    client_kwargs={"scope": "openid email profile"},
)


def _client_ip(request: Request) -> str | None:
    # Behind Caddy; trust the forwarded chain's first hop, else the socket peer.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


async def _audit(email, domain, allowed, request) -> None:
    # Non-fatal: a login must not break because the audit row didn't land, but
    # we log the failure loudly. Write goes to the leader (analytics_rw).
    try:
        write_pool = await database.get_write_pool()
        await login_audit_repo.record_attempt(
            write_pool,
            email,
            domain,
            allowed,
            _client_ip(request),
            request.headers.get("user-agent"),
        )
    except Exception:
        logger.exception("login audit write failed (login continues)")


@router.get("/auth/login")
async def login(request: Request):
    # hd restricts the Google picker to the workspace (a convenience hint; the
    # real enforcement is the server-side domain check in the callback).
    return await oauth.google.authorize_redirect(
        request, config.GOOGLE_OAUTH_REDIRECT_URI, hd=config.ALLOWED_EMAIL_DOMAIN
    )


@router.get("/auth/google/callback")
async def callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        logger.exception("oauth token exchange failed")
        await _audit(None, None, False, request)
        return HTMLResponse(_denied_page("Sign-in failed. Please try again."), 400)

    userinfo = token.get("userinfo") or {}
    email = (userinfo.get("email") or "").lower() or None
    verified = bool(userinfo.get("email_verified"))
    domain = email.split("@")[-1] if email else None
    allowed = bool(email and verified and domain == config.ALLOWED_EMAIL_DOMAIN)

    await _audit(email, domain, allowed, request)

    if not allowed:
        return HTMLResponse(
            _denied_page(f"{email or 'This account'} is not authorised."), 403
        )

    sid = await session_store.create_session(email)
    response = RedirectResponse(url="/headline", status_code=303)
    response.set_cookie(
        config.SESSION_COOKIE_NAME,
        sid,
        max_age=config.SESSION_TTL_SECONDS,
        httponly=True,
        secure=config.SESSION_COOKIE_SECURE,
        samesite="lax",
    )
    return response


@router.get("/auth/logout")
async def logout(request: Request):
    sid = request.cookies.get(config.SESSION_COOKIE_NAME)
    await session_store.delete_session(sid)
    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie(config.SESSION_COOKIE_NAME)
    return response


async def require_login(request: Request) -> str:
    """Dependency for dashboard routes. Returns the logged-in email, or raises
    a 307 redirect to /auth/login when there's no valid session."""
    sid = request.cookies.get(config.SESSION_COOKIE_NAME)
    email = await session_store.get_session(sid)
    if email:
        return email
    raise HTTPException(status_code=307, headers={"Location": "/auth/login"})


def auth_enabled() -> bool:
    # Google login is live only once the OAuth client (id + secret) AND the
    # handshake secret are all provisioned. Until then dashboard routes fall
    # back to the temporary HEADLINE_TOKEN (Phase A), so the page never breaks.
    return bool(
        config.GOOGLE_OAUTH_CLIENT_ID
        and read_oauth_client_secret()
        and read_session_secret()
    )


async def require_dashboard_access(
    request: Request, token: str = Query(default="")
) -> str:
    """The single gate for dashboard routes. Google login when it's live;
    otherwise the temporary shared-secret token. The token is thus RETIRED
    automatically the moment real auth is configured — no flag flip needed."""
    if auth_enabled():
        return await require_login(request)
    expected = config.HEADLINE_TOKEN
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing token")
    return "token"


def _denied_page(message: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not authorised</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 4rem auto;
         max-width: 28rem; color: #1a1a1a; text-align: center; }}
  a {{ color: #2563eb; }}
</style></head><body>
<h1>Not authorised</h1>
<p>{message}</p>
<p>Analytics is restricted to <strong>{config.ALLOWED_EMAIL_DOMAIN}</strong> accounts.</p>
<p><a href="/auth/login">Try again</a></p>
</body></html>"""
