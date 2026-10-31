"""
OpenWEC API — Dependencies
Reusable FastAPI dependencies for DB connection and authentication.
"""

import hashlib
import ipaddress
import logging
import socket
import threading
import time
from typing import Generator
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
import psycopg2
import psycopg2.extras

from api.config import settings

logger = logging.getLogger("openwec.api")


# ── Database ─────────────────────────────────────────────────

def get_db() -> Generator:
    """Yields a psycopg2 connection. Closes after request."""
    conn = psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
    )
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def get_cursor(conn=Depends(get_db)):
    """Yields a RealDictCursor so rows come back as dicts."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        yield cur


# ── Rate limiting ─────────────────────────────────────────────
# Uses Redis when available (production), falls back to in-memory (development).

_RATE_WINDOW_SECONDS = 60
_ALERT_THRESHOLD     = 10   # log warning after this many 429s in one window
_redis_client        = None

# How long a failed probe is remembered. Without this, every request retries
# the connection, and an unreachable Redis costs seconds per request rather
# than failing over.
_REDIS_RETRY_SECONDS = 30.0
_REDIS_PROBE_TIMEOUT = 0.5
_redis_unavailable_until = 0.0
_redis_fallback_logged   = False


def _porta_responde(host: str, port: int, timeout: float) -> bool:
    """
    A bounded TCP probe, because the client library's own timeout is not one.

    Measured against a port that black-holes packets rather than refusing
    them: `socket_connect_timeout=1` produced a **15 second** failure, the
    library's retries multiplying what looked like a one-second bound. Every
    request would pay that.

    `socket.create_connection`'s own timeout does not cover the name lookup
    that runs first, and in a compose network the host IS a name -- one that
    stops resolving the moment the container stops. Measured in that state:
    `timeout=0.5` took **2.3 seconds**, all of it in `getaddrinfo`, against
    0.001s for the same call with a literal address. So the probe runs in a
    thread and we wait on the clock, not on the socket's good intentions.
    """
    resultado: list[bool] = []

    def tentar():
        try:
            with socket.create_connection((host, port), timeout=timeout):
                resultado.append(True)
        except OSError:
            resultado.append(False)

    t = threading.Thread(target=tentar, daemon=True)
    t.start()
    t.join(timeout)
    # A thread still resolving when the clock runs out is left to finish on
    # its own and ignored; it is a daemon and holds nothing.
    return bool(resultado) and resultado[0]


def _esquecer_redis(motivo: str) -> None:
    """
    Drop the cached client and stop trying for a while.

    Called both when acquisition fails and when a client that *was* working
    stops working, which is the case the cache made invisible.
    """
    global _redis_client, _redis_unavailable_until, _redis_fallback_logged
    _redis_client = None
    _redis_unavailable_until = time.time() + _REDIS_RETRY_SECONDS
    if not _redis_fallback_logged:
        # Once per outage, not once per request. No address or credential in
        # the record -- only the consequence, which is the part an operator
        # needs.
        logger.warning(
            "rate_limit_degraded",
            extra={
                "reason":        motivo,
                "effect":        "limits are per process, not shared",
                "retry_seconds": _REDIS_RETRY_SECONDS,
            },
        )
        _redis_fallback_logged = True


def _get_redis():
    """Returns a Redis client, or None if Redis is not available."""
    global _redis_client, _redis_unavailable_until, _redis_fallback_logged
    if _redis_client is not None:
        return _redis_client

    agora = time.time()
    if agora < _redis_unavailable_until:
        return None

    try:
        import redis
        if not _porta_responde(settings.redis_host, settings.redis_port,
                               _REDIS_PROBE_TIMEOUT):
            raise OSError("no answer on the Redis port")
        r = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        r.ping()
        _redis_client = r
        _redis_fallback_logged = False
        return r
    except Exception:
        _esquecer_redis("redis_unavailable")
        return None


def _key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:8]


# Fallback in-memory store (single process, dev only)
_rate_state: dict[str, tuple[int, float]] = {}
_rate_limit_hits: dict[str, int] = {}   # tracks 429s for alerting


def _fire_rate_limit_alert(api_key: str, limit: int, window: str):
    """Log a structured warning when a key hits rate limit repeatedly."""
    hits = _rate_limit_hits.get(api_key, 0) + 1
    _rate_limit_hits[api_key] = hits
    if hits >= _ALERT_THRESHOLD:
        logger.warning(
            "rate_limit_alert",
            extra={
                "api_key_hash": _key_hash(api_key),
                "limit":        limit,
                "window":       window,
                "hits":         hits,
            }
        )
        _rate_limit_hits[api_key] = 0  # reset counter after alert


def _retry_after() -> dict[str, str]:
    """Seconds left in the current fixed window, so a client can be told when
    to come back instead of guessing."""
    restante = _RATE_WINDOW_SECONDS - (time.time() % _RATE_WINDOW_SECONDS)
    return {"Retry-After": str(max(1, int(restante)))}


def _check_rate_limit_redis(r, api_key: str, limit_per_minute: int):
    """Redis fixed-window rate limiter using INCR + EXPIRE."""
    window = int(time.time() // _RATE_WINDOW_SECONDS)
    key    = f"rl:{api_key}:{window}"
    count  = r.incr(key)
    if count == 1:
        r.expire(key, _RATE_WINDOW_SECONDS)
    if count > limit_per_minute:
        _fire_rate_limit_alert(api_key, limit_per_minute, f"minute:{window}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({limit_per_minute} requests/minute). Try again shortly.",
            headers=_retry_after(),
        )


def _check_rate_limit_memory(api_key: str, limit_per_minute: int):
    """In-memory fixed-window rate limiter (dev fallback)."""
    now = time.time()
    count, window_start = _rate_state.get(api_key, (0, now))
    if now - window_start >= _RATE_WINDOW_SECONDS:
        count, window_start = 0, now
    count += 1
    _rate_state[api_key] = (count, window_start)
    if count > limit_per_minute:
        _fire_rate_limit_alert(api_key, limit_per_minute, "minute:memory")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({limit_per_minute} requests/minute). Try again shortly.",
            headers=_retry_after(),
        )


def _check_quota_redis(r, api_key: str, daily_limit: int | None, monthly_limit: int | None):
    """Check daily and monthly quotas using Redis counters."""
    now = time.gmtime()

    if daily_limit:
        day_key   = f"quota:day:{api_key}:{now.tm_year}{now.tm_mon:02d}{now.tm_mday:02d}"
        day_count = r.incr(day_key)
        if day_count == 1:
            r.expire(day_key, 86400 + 3600)  # 25h TTL
        if day_count > daily_limit:
            _fire_rate_limit_alert(api_key, daily_limit, f"day:{day_key}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily quota exceeded ({daily_limit} requests/day). Resets at midnight UTC.",
            )

    if monthly_limit:
        month_key   = f"quota:month:{api_key}:{now.tm_year}{now.tm_mon:02d}"
        month_count = r.incr(month_key)
        if month_count == 1:
            r.expire(month_key, 32 * 86400)  # 32 days TTL
        if month_count > monthly_limit:
            _fire_rate_limit_alert(api_key, monthly_limit, f"month:{month_key}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Monthly quota exceeded ({monthly_limit} requests/month). Resets on the 1st UTC.",
            )


def _check_rate_limit(api_key: str, limit_per_minute: int,
                      daily_limit: int | None = None,
                      monthly_limit: int | None = None):
    r = _get_redis()
    if r is None:
        _check_rate_limit_memory(api_key, limit_per_minute)
        return

    try:
        _check_rate_limit_redis(r, api_key, limit_per_minute)
        _check_quota_redis(r, api_key, daily_limit, monthly_limit)
    except HTTPException:
        # The 429 itself. It travels through here and must not be mistaken
        # for an outage -- catching it would hand every rate-limited caller
        # a free pass.
        raise
    except Exception:
        # A client acquired while Redis was healthy keeps being handed out by
        # the cache, and its first failure used to escape as a 500. Measured
        # against a stopped Redis container: `redis.exceptions.ConnectionError
        # ... Name or service not known` reaching the client, 500 after ~30s,
        # and no degradation warning -- the fallback this module advertises
        # never ran, because it only ever applied at acquisition time.
        #
        # Losing Redis must cost shared counting, not availability.
        _esquecer_redis("redis_connection_lost")
        _check_rate_limit_memory(api_key, limit_per_minute)


# ── Authentication ────────────────────────────────────────────

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_KEY_CACHE_TTL = 30  # seconds
_key_cache: dict[str, tuple[str | None, int, int | None, int | None, float]] = {}


def _lookup_dynamic_key(api_key: str, conn) -> tuple[str | None, int, int | None, int | None]:
    """Returns (status, requests_per_minute, daily_limit, monthly_limit)."""
    now    = time.time()
    cached = _key_cache.get(api_key)
    if cached and now - cached[4] < _KEY_CACHE_TTL:
        return cached[0], cached[1], cached[2], cached[3]

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT status, requests_per_minute, daily_limit, monthly_limit
               FROM api_key_requests WHERE api_key = %s""",
            (api_key,),
        )
        row = cur.fetchone()

    if row:
        result = (row["status"], row["requests_per_minute"],
                  row["daily_limit"], row["monthly_limit"])
    else:
        result = (None, 0, None, None)

    _key_cache[api_key] = (*result, now)
    return result


def require_api_key(api_key: str = Security(api_key_header), conn=Depends(get_db)):
    """
    Dependency for protected endpoints (laps, analytics).

    - Dev mode (no API_KEYS configured): all requests pass through.
    - Static admin keys (API_KEYS env var): full access, no rate limit or quota.
    - Dynamic keys (issued via /api-keys/request): subject to:
        - Per-minute rate limit (requests_per_minute, default 60)
        - Optional daily quota (daily_limit, NULL = unlimited)
        - Optional monthly quota (monthly_limit, NULL = unlimited)
      All limits tracked via Redis (falls back to in-memory if unavailable).
      Repeated rate limit hits trigger a structured warning log.
    """
    valid_static_keys = settings.valid_api_keys

    if not valid_static_keys:
        return True

    if api_key and api_key in valid_static_keys:
        return True

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Pass X-API-Key header.",
        )

    key_status, rpm, daily, monthly = _lookup_dynamic_key(api_key, conn)

    if key_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, unapproved, or unknown API key.",
        )

    _check_rate_limit(api_key, rpm, daily, monthly)
    return True


# ── Public reads ──────────────────────────────────────────────
# The dashboard is a static bundle served from openwec.com. Anything it is
# given to authenticate with is downloaded by every visitor, so it is given
# nothing: the endpoints it needs read without a credential, metered by client
# address instead.
#
# These limits dampen abuse; they are not a security control. An address can be
# changed, and the data behind them is public by decision, not by accident.

PUBLIC_RATE_LIMIT_PER_MINUTE  = 60
PUBLIC_WRITE_LIMIT_PER_MINUTE = 5


def _parse_address(texto: str):
    """An IP address, or None. Tolerates `[::1]` and a trailing `:port`."""
    texto = texto.strip()
    if not texto:
        return None
    if texto.startswith("[") and "]" in texto:
        texto = texto[1:texto.index("]")]
    try:
        return ipaddress.ip_address(texto)
    except ValueError:
        pass
    # `1.2.3.4:5678` — a form some proxies emit. Bare IPv6 also contains
    # colons, so only strip a suffix when what remains parses.
    if texto.count(":") == 1:
        try:
            return ipaddress.ip_address(texto.rsplit(":", 1)[0])
        except ValueError:
            return None
    return None


def _is_trusted(endereco, redes) -> bool:
    return any(endereco in rede for rede in redes)


def _client_ip(request: Request) -> str:
    """
    The address to meter, under an explicit trusted-proxy policy.

    `X-Forwarded-For` is a request header: any caller can send one, and a
    caller that reaches the API without going through the proxy sends whatever
    it likes. Reading it unconditionally — from either end of the list — lets
    one caller spend another's budget, or escape its own by rotating a string.

    So the header is read only when the peer that actually opened the
    connection is a proxy we named in `TRUSTED_PROXIES`. The list is then
    walked from the right, discarding the hops we also trust, and the first
    address that is not one of ours is the client. Everything to the left of it
    was written by somebody we have no reason to believe, and is ignored.

    With `TRUSTED_PROXIES` empty the header is never read. Nothing can be
    forged, and behind a reverse proxy every caller shares one bucket — safe,
    and visibly wrong, which is what the start-up warning is for.
    """
    peer_bruto = getattr(request.client, "host", None)
    peer = _parse_address(peer_bruto) if peer_bruto else None
    if peer is None:
        # No peer address at all (ASGI transports that do not provide one).
        # There is nothing to meter and nothing to believe.
        return "unknown"

    redes = settings.trusted_proxy_networks
    if not redes or not _is_trusted(peer, redes):
        return str(peer)

    encaminhados = request.headers.get("X-Forwarded-For")
    if not encaminhados:
        return str(peer)

    for bruto in reversed(encaminhados.split(",")):
        endereco = _parse_address(bruto)
        if endereco is None:
            # A malformed hop. We cannot tell where the real client sits in
            # the rest of the list, so we stop and meter the proxy.
            return str(peer)
        if not _is_trusted(endereco, redes):
            return str(endereco)

    # Every hop was one of ours. Meter the nearest one.
    return str(peer)


def allow_public_read(
    request: Request,
    api_key: str = Security(api_key_header),
    conn=Depends(get_db),
):
    """
    Dependency for the read endpoints the public dashboard needs.

    - With a key: exactly `require_api_key`. Private consumers keep their
      authentication, their quotas and their higher limits, and a key that is
      wrong is still refused — a bad credential is never quietly downgraded to
      an anonymous request.
    - Without a key: allowed, and metered per client address.
    """
    if api_key:
        return require_api_key(api_key=api_key, conn=conn)

    _check_rate_limit(f"anon:{_client_ip(request)}", PUBLIC_RATE_LIMIT_PER_MINUTE)
    return True


# A route that takes an optional key still declares the scheme, so /docs would
# draw a padlock and a reader would conclude a key is required. The empty
# requirement says, in OpenAPI's own terms, that no credential is also valid.
PUBLIC_READ_OPENAPI = {"security": [{}, {"APIKeyHeader": []}]}


def limit_public_writes(request: Request):
    """
    The one write that is open to the public — requesting an API key — metered
    per address. Without this, a loop fills `api_key_requests` and the daily
    report mails every row of it.
    """
    _check_rate_limit(f"write:{_client_ip(request)}", PUBLIC_WRITE_LIMIT_PER_MINUTE)
    return True