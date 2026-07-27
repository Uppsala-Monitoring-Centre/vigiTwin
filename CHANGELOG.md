# Changelog

All notable changes to `llm-dup-dev` are recorded here.

The version follows [Semantic Versioning](https://semver.org). It is the
**consumer-contract** signal — the JSON schemas in `schemas/` plus the public
API the secure companion repo imports — not a per-commit identifier. Exact code
provenance lives in the `dev_git_commit` field of each manifest; this file and
the version number carry the *meaning* of a change. See `agents.md` §
Conventions → Versioning for the bump rules.

While pre-1.0: a breaking schema/API change bumps the **minor**
(`0.2.0` → `0.3.0`); additive or backward-compatible changes and fixes bump the
**patch** (`0.2.0` → `0.2.1`).

## [Unreleased]

## [0.2.0] - 2026-07-27

Baseline release. The version had remained `0.1.0` since the project's early
days while the schemas and public API evolved substantially; this is a one-time
honest catch-up bump to re-anchor the contract signal, and the point from which
`CHANGELOG.md` is maintained going forward.

### Added
- `CHANGELOG.md` and a documented versioning policy (`agents.md` § Conventions).
