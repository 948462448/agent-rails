---
id: open-eval-pnpm-lockfile-sync
title: Keep pnpm-lock.yaml in sync with package.json; never commit package-lock.json
triggers:
  - package.json
  - lockfile
  - pnpm
  - npm
  - tnpm
  - frontend build
  - o2
  - css-modules-hash
  - optional deps
  - MODULE_NOT_FOUND
applies_to:
  - frontend/
staleness: stable
source:
  - 2026-06-22 POC debugging (commit 25ffafe)
---

## Rule

The OpenEval frontend is a **pnpm project**. Only `pnpm-lock.yaml` should ever be committed under `frontend/`. `package-lock.json` (npm) and `yarn.lock` (yarn) are forbidden.

Any change to `frontend/package.json` (add / remove / bump a dependency) must be accompanied by a synchronized update to `frontend/pnpm-lock.yaml`, produced by running `pnpm install` in `frontend/`.

## Why It Matters

- **CI uses pnpm with `--frozen-lockfile`** on the o2 build platform. A stale lockfile either fails strict mode or forces pnpm to re-resolve dependencies at build time.
- **Platform-specific optional deps** (e.g. `@ice/css-modules-hash-linux-x64-gnu`) are resolved by pnpm against the lockfile. A stale lockfile can cause pnpm to miss the linux binary on a linux CI host, producing a `MODULE_NOT_FOUND` crash at build time even though the package exists on the registry.
- **Mixed lockfiles confuse CI tooling**: when both `pnpm-lock.yaml` and `package-lock.json` coexist, different toolchains may pick different files, leading to non-deterministic dependency resolution across local / CI environments.

## Real Incident (2026-06-22)

Commit `687efe6` added `@playwright/test` to `frontend/package.json` but only generated `package-lock.json` (npm format), without updating `pnpm-lock.yaml`. The project had been pnpm-only since master. The o2 CI build failed with:

```
Error: Cannot find module '@ice/css-modules-hash-linux-x64-gnu'
```

Root cause chain: `package.json` and `pnpm-lock.yaml` out of sync → pnpm re-resolved deps → linux-x64-gnu optional dep not installed → `ice build` crashed on `require()`.

Fix (commit `25ffafe`): deleted the spurious `package-lock.json` (-9164 lines), regenerated `pnpm-lock.yaml` (+42 lines reflecting `@playwright/test`).

## Verify

```bash
# Local: ensure lockfile reflects package.json
cd frontend && pnpm install --frozen-lockfile
# ^ fails if lockfile is stale — that's the point

# Sanity: no foreign lockfiles in tree
git ls-files frontend/ | grep -E 'package-lock.json|yarn.lock|bun.lockb' && echo "BAD: foreign lockfile" || echo "OK"
```

## Delivery Note

If `frontend/package.json` changed, the final answer must explicitly say:

1. Whether `pnpm install` was run to refresh `pnpm-lock.yaml`
2. Whether `pnpm install --frozen-lockfile` passes
3. Whether any foreign lockfile (`package-lock.json` / `yarn.lock`) was introduced (it should not be)

## Prevention

- Add `package-lock.json` and `yarn.lock` to `frontend/.gitignore` (defense in depth)
- Configure IDE (VSCode / JetBrains) to use pnpm as the project's package manager, so "install dependency" UI actions don't silently spawn `npm install`
- Add a pre-commit hook that rejects foreign lockfiles:
  ```bash
  if git diff --cached --name-only | grep -qE '^frontend/(package-lock.json|yarn.lock)$'; then
    echo "ERROR: frontend/ uses pnpm; do not commit package-lock.json or yarn.lock"
    exit 1
  fi
  ```
