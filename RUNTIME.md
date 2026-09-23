# OpenWEC — Production Runtime

This document describes the runtime that is **currently in production**, captured
directly from the running host. Its purpose is to make the runtime reproducible
from this repository alone, without reverse-engineering the server.

It describes what exists today. It is not a proposal and not a migration plan.
Where the current setup is fragile, that is recorded as a known limitation
rather than silently corrected.

Captured: 2026-09-23. Source of truth: the production host.

---

## 1. Host

| Item | Value |
|---|---|
| Hostname | `openwec-prod` |
| Address | `157.230.0.20` |
| Architecture | `x86_64` |
| Kernel | `6.8.0-136-generic` |
| Timezone | `Etc/UTC` |
| Docker | `29.5.3` (storage driver `overlayfs`, containerd snapshotter) |
| Nginx | system service, `active` and `enabled` |

The host also runs an unrelated stack (`alphecca-homolog-*`). It is not part of
OpenWEC and is out of scope here.

---

## 2. Architecture

```
                    Internet
                       │
                 443 / 80 (nginx, TLS via certbot)
                       │
        ┌──────────────┴───────────────┐
        │                              │
  openwec.com                    api.openwec.com
  static files                   proxy_pass → 127.0.0.1:8000
  /opt/openwec/dashboard-dist              │
                                           │
                              ┌────────────┴────────────┐
                              │   openwec-api (uvicorn) │
                              └────────────┬────────────┘
                                           │  network: openwec_default
                          ┌────────────────┴────────────────┐
                          │                                 │
                   openwec-db                         openwec-redis
                   TimescaleDB / PG16                 Redis 7
                   volume openwec_pgdata              no persistence
```

Every container port is published on `127.0.0.1` only. Nothing in the OpenWEC
stack is reachable from the internet except through nginx.

---

## 3. Containers

Compose project lives at `/opt/openwec`. The project name is therefore
`openwec`, which is what prefixes the network and volume names.

| Container | Image | Restart | Published port |
|---|---|---|---|
| `openwec-api` | `openwec-api:latest` (built locally from `Dockerfile`) | `unless-stopped` | `127.0.0.1:8000 → 8000` |
| `openwec-db` | `timescale/timescaledb:latest-pg16` | `unless-stopped` | `127.0.0.1:5433 → 5432` |
| `openwec-redis` | `redis:7-alpine` | `unless-stopped` | `127.0.0.1:6379 → 6379` |

- Network: `openwec_default` (bridge), created by compose.
- Volume: `openwec_pgdata` → `/var/lib/postgresql/data` on the db container.
- Redis gets an anonymous volume for `/data` because the image declares one.
  Nothing depends on it; Redis holds only rate-limiting state.
- None of the three services defines a healthcheck.
- The API container runs as `root` and declares no resource limits.

### Compose

`docker-compose.prod.yml` in this repository reproduces the production compose
file. Both obtain the database password from `DB_PASSWORD` in `.env`; neither
holds a literal value (see section 5).

**The volume name depends on the project name.** Running the compose file from a
directory not named `openwec` creates a new, empty `pgdata` volume and the
database will look empty. Keep the directory named `openwec` or pass
`-p openwec`.

### Dockerfile

`Dockerfile` in this repository is a byte-for-byte reproduction of
`/opt/openwec/Dockerfile` (sha256 `1cd7ec91…0100`).

It installs **only** `requirements-api.txt`. Playwright and the collector
dependencies are deliberately absent from the API image.

---

## 4. Images and reproducibility

Production runs these tags:

| Component | Tag | Effective version in production |
|---|---|---|
| Database | `timescale/timescaledb:latest-pg16` | PostgreSQL 16.14, TimescaleDB 2.27.2 |
| Cache | `redis:7-alpine` | Redis 7.4.10 |
| API base | `python:3.12-slim` | Python 3.12.14 |

`latest-pg16` and `7-alpine` are **mutable**. Both have already moved since
production pulled them: the digests currently published under those tags do not
match what production is running.

**The exact digests in production cannot be recovered.** The host uses Docker's
containerd image store, where `RepoDigests` reports the local manifest digest —
it equals the image `Id`, and the locally built `openwec-api` image, which was
never pushed anywhere, is reported the same way. The values are therefore not
registry digests and cannot be used to pin an image.

Consequence: pulling these tags today produces different builds than the ones
running. This is a real reproducibility gap, recorded here as a **pending
decision** rather than fixed. Note also that pinning a platform-specific digest
would defeat multi-architecture resolution; only a manifest-index digest is
portable across architectures.

All three tags do publish `linux/arm64` today.

---

## 5. Configuration and secrets

The API reads its settings through `api/config.py` (pydantic-settings). The
production `.env` at `/opt/openwec/.env` defines exactly eight variables:

| Variable | Production value | Secret |
|---|---|---|
| `DB_HOST` | `db` | no |
| `DB_PORT` | `5432` | no |
| `DB_NAME` | `openwec` | no |
| `DB_USER` | `openwec` | no |
| `DB_PASSWORD` | — | **yes** |
| `REDIS_HOST` | `redis` | no |
| `REDIS_PORT` | `6379` | no |
| `API_KEYS` | set | **yes** |

See `.env.example`. `.env` is gitignored and its values are not recorded
anywhere in this repository.

Other secrets that exist on the host and are **not** in this repository:

| File | Purpose |
|---|---|
| `/opt/openwec/.env` | database password and API keys |
| `/opt/openwec/.env.notifications` | Resend API credentials for the daily key report |
| `~/.s3cfg` | s3cmd credentials for the backup bucket |
| `/etc/letsencrypt/…` | TLS certificates and private keys |

### Resolved — plaintext password in the production compose file

Kept as a record. This is no longer the case.

The audit that produced this document found the database password written as a
literal string in `/opt/openwec/docker-compose.yml`. The credential was then
rotated, and the production compose file was changed to read `DB_PASSWORD` from
`.env`, the same mechanism the versioned file uses. No literal password remains
in either file, and no real credential was ever committed to this repository.

The production `.env` is owned by `root:root` with mode `0600`.

---

## 6. Nginx

Site: `/etc/nginx/sites-available/openwec`, symlinked into `sites-enabled`.
It is certbot-managed and is **not** versioned here, because the repository has
no established location for server configuration and the file carries
host-specific certificate paths. Its structure:

- **`openwec.com`, `www.openwec.com`** — TLS on 443; static root
  `/opt/openwec/dashboard-dist`; `try_files $uri $uri/ /index.html` for SPA
  routing.
- **`api.openwec.com`** — TLS on 443; `proxy_pass http://127.0.0.1:8000` with
  `Host`, `X-Real-IP`, `X-Forwarded-For` and `X-Forwarded-Proto` forwarded.
  `OPTIONS` requests are answered directly by nginx with `204` and CORS headers
  (`Access-Control-Allow-Origin: *`, methods `GET, POST, OPTIONS`, headers
  `Content-Type, X-API-Key`). `/.well-known/acme-challenge/` is served from
  `/var/www/html` for renewals.
- Port 80 redirects to HTTPS for all three names.

Certificates are issued per-domain under `/etc/letsencrypt/live/openwec.com/`
and `/etc/letsencrypt/live/api.openwec.com/`, renewed by the `certbot` entry in
`/etc/cron.d`.

---

## 7. Database

| Item | Value |
|---|---|
| Engine | PostgreSQL 16.14 (Alpine/musl build) |
| Extensions | `timescaledb 2.27.2`, `plpgsql 1.0` |
| Volume | `openwec_pgdata` |
| Schema | `database/schema.sql` in this repository |

### Moving this database

The PostgreSQL data directory is **architecture- and build-specific**. Copying
the `openwec_pgdata` volume from this x86_64 host to an ARM64 host is not
supported and must not be attempted.

Any move requires a logical dump and restore:

- `pg_dump -Fc` on the source,
- a **new, empty** database on the target,
- `pg_restore` on the target.

With TimescaleDB, the restore additionally needs `timescaledb_pre_restore()`
before and `timescaledb_post_restore()` after, so that hypertable chunks and
the catalog are restored consistently. The target must run a TimescaleDB
version compatible with 2.27.2.

The production database must never be the target of a restore test.

*No dump or restore was performed while writing this document.*

---

## 8. Redis

Started with `redis-server --maxmemory 64mb --maxmemory-policy allkeys-lru`.
No persistence is configured or relied upon; it holds rate-limiting state only.
The API falls back to in-memory limiting when Redis is unavailable.

---

## 9. Dashboard

The dashboard is a Vite + React build. It is **not** containerized: nginx serves
the built files directly.

- Source: `dashboard/`
- Build: `npm run build` (Vite 5, React 18)
- Output: `dashboard/dist/` — gitignored
- Deployed to: `/opt/openwec/dashboard-dist/` (currently ~6.5 MB)

Build-time variables come from `dashboard/src/.env.production`, which is
gitignored because it contains an API key:

- `VITE_API_BASE_URL`
- `VITE_API_KEY`

**Known gap:** the build is produced on a developer machine and copied to the
host. There is no pipeline, no build container, and no record in the repository
of which commit produced the files currently being served.

---

## 10. Scheduled jobs

Both jobs run from the **root crontab on the host**, not inside containers. The
host timezone is `Etc/UTC`, so the cron expressions are UTC.

| Expression | UTC time | Command |
|---|---|---|
| `0 3 * * *` | 03:00 UTC | `/opt/openwec/backup.sh` → `/var/log/openwec-backup.log` |
| `0 11 * * *` | 11:00 UTC | `python3 /opt/openwec/scripts/check_pending_api_keys.py` → `/var/log/openwec-api-keys.log` |

`check_pending_api_keys.py` sends a daily report by e-mail through Resend, using
credentials from `/opt/openwec/.env.notifications`.

**Timezone caveat for any future move.** These expressions are only correct on a
host set to UTC. Copying `0 3 * * *` verbatim to a host set to
`America/Sao_Paulo` would move the job by three hours in absolute terms
(03:00 UTC becomes 06:00 UTC). Whether to preserve the absolute instant or the
local hour is an open decision that must be made before the jobs are recreated
anywhere.

---

## 11. Backup

`/opt/openwec/backup.sh`, daily at 03:00 UTC:

1. `docker exec openwec-db pg_dump -U openwec -d openwec -Fc` into the container,
2. `docker cp` the dump to `/tmp/openwec_<date>.dump` on the host,
3. `s3cmd put` to `s3://openwec-backups/db/<date>.dump`,
4. delete the local file,
5. keep only the newest 7 objects in the bucket, deleting the rest.

Credentials live in `~/.s3cfg` on the host and are not in this repository.

Known limitations, recorded and not changed:

- retention is enforced by deleting objects, so there is no protection against a
  bad dump silently overwriting good history beyond seven days;
- no restore test is automated;
- the script uses `set -e` but does not verify that the dump is readable before
  uploading it or before pruning older objects.

---

## 12. Collectors

The collectors (`collectors/`) scrape timing data and depend on Playwright.

Current state on the production host:

- Playwright is **not** installed in the API image;
- Playwright is **not** installed on the host;
- there is **no** cron entry that runs any collector;
- the collectors therefore do not run on this VM at all.

Where they run today has not been established. Their ARM64 compatibility is
consequently **inconclusive** — Playwright ships `aarch64` browser builds, but
this has not been verified for this project's usage. This does not affect the
web/API runtime.

---

## 13. Known differences between production and this repository

| Item | Production | This repository |
|---|---|---|
| `Dockerfile` | `/opt/openwec/Dockerfile` | reproduced byte-for-byte |
| Production compose | `DB_PASSWORD` from `.env` | same mechanism |
| Compose location | `/opt/openwec/docker-compose.yml` | `docker-compose.prod.yml` at repo root |
| Dev compose | not present | `docker/docker-compose.yml`, `db` service only |
| `requirements-api.txt` | identical file (sha256 `5939f6c3…`) | identical |
| `requirements.txt` | unpinned, lists `playwright` twice, includes `sqlalchemy`, `fastapi`, `uvicorn`, `pydantic` | pinned with `>=`, single `playwright`, no `sqlalchemy` |
| `api/` source | deployed by `scp`, no commit recorded | tracked in git |
| Dashboard build | `dashboard-dist/`, origin commit unknown | source only, `dist/` gitignored |
| nginx site | `/etc/nginx/sites-available/openwec` | documented here, not versioned |
| Deploy procedure | `MAINTENANCE.md`, gitignored | not versioned |
| CI | none runs against production | `.github/workflows/tests.yml`, `ubuntu-latest` (x86_64), never builds the image |

The divergent `requirements.txt` does **not** affect the API: the Dockerfile
installs `requirements-api.txt` only. The two files were not reconciled, because
reconciling them would change what the collectors install and the collectors'
runtime has not been identified.

---

## 14. Rebuilding the runtime from this repository

If the host were lost, this is what the runtime consists of. These are the
components, not an authorized procedure:

1. A Docker host with compose, in a directory named `openwec`.
2. `.env` created from `.env.example`, with a real `DB_PASSWORD` and `API_KEYS`.
3. `docker compose -f docker-compose.prod.yml up -d --build`, which builds the
   API image from `Dockerfile` and starts db and redis.
4. Schema from `database/schema.sql`, then data from a logical dump restored
   with the TimescaleDB pre/post-restore steps in section 7.
5. `dashboard/` built with `npm run build` and the output placed where nginx
   serves it, with `VITE_API_BASE_URL` and `VITE_API_KEY` set at build time.
6. nginx configured as described in section 6, with certificates issued for the
   two names.
7. The two cron jobs from section 10, after the timezone decision is made, with
   `.env.notifications` and `~/.s3cfg` provisioned out of band.
