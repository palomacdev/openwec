# ADR 0004 — How the public dashboard reads

Status: **accepted and implemented** by the change this document accompanies.
It adds no service and no deployment unit. It moves four routes from
"requires a key" to "requires no key, metered per address".

---

## 1. The question

The dashboard at `openwec.com` is a Vite build: static files served by nginx.
It needed four endpoints that required `X-API-Key`, so it was built with a key
inlined — `.env.example` still says to use "a key from `API_KEYS` above".

Vite replaces `import.meta.env.VITE_API_KEY` at build time. The key is not
fetched, not hidden and not scoped: it becomes a string constant in a file
downloaded by every visitor. Measured on the bundle served in production, the
whole indirection is gone and what remains is `n["X-API-Key"]=<the key>`.

So the question is not *how to protect the key*. There is no way to protect a
secret you hand to the browser. The question is **what the dashboard should be
given instead**.

---

## 2. Observed facts

**F1 — The key is already public.** It is one `curl` away from anyone, and has
been since the first deploy. Nothing about the current arrangement keeps it
private.

**F2 — The same key opens administration.** `require_admin_key` validated
against `settings.valid_api_keys`, the set `VITE_API_KEY` is drawn from. The
admin routes return every issued key in plaintext with the requester's name
and e-mail, and can revoke any of them. Production runs an older build without
those routes, so this is latent rather than live — it activates on deploy.
ADR-adjacent fix: a separate `ADMIN_API_KEYS`, in its own change.

**F3 — The key in the deployed bundle no longer works.** Measured against
`api.openwec.com`: all four endpoints answer 401 to it. The deployed dashboard
has been failing those four calls since some point after 2026-10-09, and the
failures were swallowed by `.catch(() => {})`, so the page showed empty charts
and a permanent "Loading race data…" instead of an error.

**F4 — Three of the dashboard's calls were already public.** `results`,
`events` and the whole series/season catalogue need no key today. The data
behind the four protected ones is the same race, from the same session, shown
on the same page.

---

## 3. The decision

**The four routes the dashboard draws read without a credential, metered per
client address. A key is still accepted, and raises the limit.**

| route | before | after |
|---|---|---|
| `sessions/{id}/stints` | key | public, key optional |
| `sessions/{id}/gaps` | key | public, key optional |
| `sessions/{id}/laps/{car}` | key | public, key optional |
| `sessions/{id}/race-control` | key | public, key optional |
| `sessions/{id}/laps` (bulk) | key | **key** |
| `sessions/{id}/pace` | key | **key** |
| `sessions/{id}/pit-window` | key | **key** |
| `drivers/{id}/consistency` | key | **key** |

The line is not "analytics versus raw". It is **what a visitor sees on a page
versus what a consumer extracts in bulk**. One car's laps draw a chart; a whole
session's laps are a dataset. `pace`, `pit-window` and `consistency` stay
closed for the simplest reason available: nothing public reads them, so nothing
public needs them. Opening a route nobody asked for is a decision to make when
someone asks.

A credential that is *presented* and wrong is still refused. Downgrading a bad
key to an anonymous request would hide a paying consumer's broken configuration
behind a page that appears to work.

### 3.1 Why not an intermediate backend

A BFF — a small server holding the key and proxying the dashboard's calls —
was the obvious alternative. It was rejected, and the reason is not effort:

**It does not keep a secret.** The proxy is reachable by the same browsers, with
no credential of its own. Whatever it will do for the dashboard, it will do for
anyone who calls it. The key stops being in the bundle and starts being in a
service that hands out the same data to the same callers. The abuse surface is
identical, so the proxy still needs per-address metering — the exact control
proposed here, with a hop in front of it.

**It adds a deployment unit to a project that has no pipeline.** The dashboard
is built on a developer's machine and copied to the host (`RUNTIME.md` §9);
there is no record of which commit produced what is being served. Adding a
second thing to deploy, with a secret to rotate, to a system that cannot yet
say what it is running, buys nothing and costs an operator.

**It would hide the failure that caused this.** F3 went unnoticed for weeks
because errors were swallowed. A proxy adds a layer that can fail in a new way,
between the page and the thing that actually has the answer.

A BFF earns its place when it holds something the browser must not have: a
per-user session, a write path, a third-party credential. None of those exist
here. What exists is public race data and a static page.

### 3.2 The limits

| | limit | basis |
|---|---|---|
| anonymous read | 60/min per address | a session page issues ~6 requests; this is roughly ten page loads a minute |
| anonymous write (`api-keys/request`) | 5/min per address | it was **unlimited**; a loop filled the table and the daily report mailed every row |
| static key | unmetered | unchanged |
| dynamic key | its own `requests_per_minute` and quotas | unchanged |

These limits dampen abuse. They are not a security control, and this document
does not pretend otherwise — an address can be changed. The data behind them is
public by decision, which is what makes that acceptable.

### 3.3 Which address, and who says so

`X-Forwarded-For` is a request header. Any caller can send one, and a caller
that reaches the API without passing through nginx sends whatever it likes.
The API port is published on `127.0.0.1:8000`, so that caller is not the
internet — but it is any process on the host and any container on its bridge
networks, which is not nobody.

Reading the header unconditionally is wrong from either end. Measured against
both implementations:

| | first entry | last entry | trusted-proxy policy |
|---|---|---|---|
| direct caller forging a header | believes the forgery | **believes the forgery** | meters the caller |
| direct caller impersonating the proxy | believes it | **believes it** | meters the caller |
| real chain through nginx | believes the client's own first entry | **meters nginx, so everyone shares one bucket** | meters the client |
| unknown intermediate in the chain | believes everything past it | meters nginx | meters the intermediate |

So the header is read only when the peer that opened the connection is listed
in `TRUSTED_PROXIES`. The chain is then walked from the right, discarding hops
that are also ours, and the first address that is not one of ours is the
client. Everything to its left was written by somebody we have no reason to
believe. A hop that does not parse stops the walk rather than guessing.

`TRUSTED_PROXIES` is **empty by default**, and empty means the header is never
read. Nothing can be forged — and behind a reverse proxy every caller shares
the proxy's bucket, which is safe and useless. The API logs
`trusted_proxies_unset` at start-up for exactly as long as that is true, since
the difference is invisible in any response and looks like the limit simply
being too strict.

### 3.4 Where the counters live

Redis holds them, shared across processes. When Redis is unreachable the
limiter falls back to an in-memory counter, which counts **per process**.

Two consequences, both recorded rather than hidden:

- **The image starts one uvicorn process** and declares no `--workers`, so the
  fallback is consistent today. A guard test fails if that changes, because
  N workers would mean N times every limit while Redis is down.
- **An unreachable Redis used to cost seconds per request.** Measured against a
  port that drops packets instead of refusing them, `socket_connect_timeout=1`
  produced a **15.17 second** failure — the client library's retries
  multiplying what read like a one-second bound — and the failure was not
  remembered, so every request paid it again. The probe is now a plain connect
  bounded at 0.5 s, and a failure is remembered for 30 s: 0.64 s once, then
  0.000 ms. One `rate_limit_degraded` warning per outage says the limits have
  stopped being shared.

The operational answer to both is the same: keep Redis up. `docker-compose.prod.yml`
now orders it before the API, which narrows the start-up window without closing
it — neither service declares a healthcheck.

---

## 4. What this does not decide

- **It does not make the API keyless.** Bulk extraction, strategy estimation
  and driver analytics still require a key, and the key system — request,
  approval, quota, revocation — is untouched.
- **It does not change what data exists.** No column, no row, no query.
- **It does not settle administration.** That is a separate change, and it must
  land before the admin routes are ever deployed.
- **It does not retire `VITE_API_KEY` from the host.** The variable is no
  longer read by any source file, so the next build drops it from the bundle;
  removing it from `dashboard/.env.production` is an operator step, not a code
  change.
