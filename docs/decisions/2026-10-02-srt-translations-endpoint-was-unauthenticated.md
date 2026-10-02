# Decision: POST /api/srt-translations requires the API key

Date: 2026-10-02

Status: Accepted

## Context

`SUBTITLE_AI_API_KEY`, when set, is meant to protect every API endpoint except
`/api/health` (README, "Tuning knobs"; CLAUDE.md, "API-key coverage for job
creation and audio analysis").

## Problem

`POST /api/srt-translations` (Workflow B job creation) was declared without
the `require_api_key` dependency. With a key configured, an unauthenticated
caller got past authentication: the request reached path validation and, with
a valid path, would have queued a translation job and written a `.en.srt` into
the media library.

## Evidence

Found during the architecture audit by listing the app's routes against their
dependencies. Reproduced with `SUBTITLE_AI_API_KEY=k` and no header:
`POST /api/jobs` and `GET /api/jobs` returned 401, while
`POST /api/srt-translations` returned 400 ("path does not exist"), i.e. it was
processed. No evidence yet of exploitation; deployments that never set a key
were unaffected (the endpoint was as open as every other one).

## Options considered

1. Add the dependency to this route and add a test that enumerates every
   route (chosen).
2. A global middleware that guards `/api/*` and whitelists health. Rejected for
   now: it changes how the Origin check and the key interact for every route
   and hides which routes are protected.

## Decision

Add `dependencies=[Depends(require_api_key)]` to the route, and
`ApiKeyGuardTests.test_every_api_route_requires_the_key_except_health`, which
walks `app.routes` and fails for any `/api/*` route that answers without the
key other than the allow-listed health check.

## Consequences

A client that submitted subtitle translations without a key to a keyed
deployment now receives 401. The bundled UI sends the key on every request
(`frontend/src/api/client.ts`), so it is unaffected.

## Rejected alternatives

See options.

## Validation

The new test fails on the old code (`422 != 401`) and passes on the fix.
