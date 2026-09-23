# Contributing to OpenWEC

Thanks for your interest in contributing. This document explains how the project is structured and how to get started.

## What we need help with

- **New series coverage** — if you know of timing exports from other endurance series (SUPER GT, DTM, IMPC, etc.), open an issue
- **Data quality** — driver name corrections, team merges, missing nationalities
- **SDK features** — new analysis methods, better plotting, more DataFrame columns
- **Dashboard** — new visualizations, UX improvements
- **Documentation** — examples, tutorials, Jupyter notebooks

## Development setup

See [README.md](README.md) for full setup instructions.

## Project conventions

- Python 3.12+, type hints where practical
- FastAPI endpoints follow existing patterns in `api/routers/`
- Database changes go in `database/migrations/` as numbered SQL files
- React components follow the existing design system (CSS variables, `var(--accent)`, etc.)
- Commit messages: `feat:`, `fix:`, `chore:`, `docs:` prefixes

## What must not be committed

This repository is public. Keep it a description of **how to run the project**,
not an inventory of how any particular instance is deployed.

Never commit:

1. Credentials of any kind — passwords, tokens, API keys, certificates. This
   includes test values, if the same value is used anywhere real.
2. IP addresses, hostnames or server names of a live deployment.
3. Absolute paths of a live host (`/opt/...`, `/etc/...`, `/var/log/...`), or the
   location of its configuration and secret files.
4. Bucket names, backup paths, object naming patterns or retention policies.
5. Exact patch versions of what is running, kernel versions, container runtime
   versions, or the security posture of running containers. State requirements
   as ranges (`PostgreSQL 16+`), never as an inventory.
6. Operational runbooks, job schedules, maintenance windows, or restore
   procedures specific to a deployment.
7. References to unrelated services or projects that share infrastructure.
8. Incident reports that describe live configuration. Recording that something
   was fixed is fine; describing the configuration is not.

Operational documentation belongs in a private repository, not here.

## Submitting changes

1. Fork the repository
2. Create a branch: `git checkout -b feat/your-feature`
3. Make your changes
4. Test locally (API + dashboard)
5. Open a pull request with a clear description

## Data sourcing

OpenWEC collects data from Al Kamel Systems public timing exports. We do not scrape or redistribute proprietary data. All data used is publicly available at race weekends.

## Questions

Open an issue. I'm happy to discuss before you spend time building something.