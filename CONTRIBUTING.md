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

## Submitting changes

1. Fork the repository
2. Create a branch: `git checkout -b feat/your-feature`
3. Make your changes
4. Test locally (API + dashboard)
5. Open a pull request with a clear description

## Data sourcing

OpenWEC collects data from Al Kamel Systems public timing exports. We do not scrape or redistribute proprietary data. All data used is publicly available at race weekends.

## Questions

Open an issue — happy to discuss before you spend time building something.