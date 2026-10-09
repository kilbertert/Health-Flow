# AFK development workflow

The project uses the official Matt Pocock planning skills and this repository's
Sandcastle execution adapters:

```
idea -> /grill-with-docs -> /to-spec -> /to-tickets -> implement -> review -> merge
```

## Phase boundaries

1. `/grill-with-docs` resolves terminology and decisions. When its frontier is
   empty, it reports `GRILLING_COMPLETE`, asks for confirmation, and ends the
   turn. Confirmation does not authorize a later phase.
2. `/to-spec` publishes the confirmed requirements as a GitHub spec/PRD issue.
3. `/to-tickets` creates native sub-issues and native blocking edges in the
   order approved by the user.
4. Implementation starts only after an explicit invocation or authorization:
   `agent:implement` for the matching GitHub workflow, `pnpm ralph` for the
   host planner, or `pnpm afk -- <issue>` for one controlled issue.
5. Review runs the harness-neutral Sandcastle two-axis orchestration, then a
   fixer handles confirmed findings and PR conversation.

## Labels

| Label | Meaning | Engine |
| --- | --- | --- |
| `ready-for-agent` | Complete spec, eligible leaf issue | `pnpm ralph` |
| `agent:implement` | Explicit execution authorization | issue or PR workflow |
| `agent:queued` | Ready but blocked by an open native dependency | promotion workflow |
| `agent:in-progress` | AFK run is active | workflow state |
| `agent:blocked` | Failed run or invalid shape | human triage |
| `agent:review` | Explicit PR review authorization | PR review workflow |

`agent:implement` is not a planning label. The planner never selects PRDs,
parents with sub-issues, nested sub-issues, open native blockers, or issues
already targeted by an open PR. It has no forced fallback when every candidate
is blocked.

## Truth sources

- `GLOSSARY.md` is the glossary only.
- `docs/adr/` records durable implementation decisions and trade-offs.
- The spec/PRD issue records requirements.
- Native sub-issues and dependency edges record execution slices.
- `docs/agents/` tells the official skills how to read the tracker, labels,
  and domain docs.

## Delivery

Agents commit on task branches and run deterministic checks. The host runner
pushes branches, opens draft PRs, and merges only after CI and human review.
No agent pushes the default branch directly.

Pull request mutation jobs accept only repository-owner-authored branches from
the same repository. The current `main` checkout supplies the trusted
controller, candidate commands run in Docker with the read token, and verified
Git bundles enter a clean delivery checkout before the host write token is used.
Missing delivery credentials produce `agent:blocked`; there is no non-triggering
`GITHUB_TOKEN` fallback.

## What happens after the merge

Delivery does not end at the merge. Merging to `main` deploys — no further human
action, and no separate approval step.

- **Checks gate the merge, the merge triggers the deploy.** `.github/workflows/ci.yml`
  runs `ruff check` and `pytest` on every pull request and on `main`, and is a
  required check in the branch ruleset. `.github/workflows/cd.yml` then ships what
  merged; it deliberately does **not** re-run those checks, because it runs on
  `main` and `main` is only reachable through a pull request carrying them.
- **The trigger set is a whitelist.** `cd.yml` deploys only when `app/**`, the
  frontend sources and build inputs, `pyproject.toml`, `uv.lock`, or the deploy
  assets themselves change. A documentation merge must not restart the service.
  The trap to remember: when `paths` does not match there is *no signal at all*, so
  a file a deployment really executes and that is missing from that list produces
  "changed it but it never deployed" with nothing to notice.
- **One script, two callers.** `deploy/deploy-36.sh` is the only deployment path —
  the automated one and the human emergency `--rollback-to` share it, so the two
  cannot drift apart. It drives `dev-host`, which enforces the artifact-identity
  gate (`--artifact-sha256`) for a service host.
- **What the post-deploy self-check asserts** is only what a machine can answer:
  the shape of `/ready`, and that the entry point is actually serving the bundle
  this build produced (its sha256). Everything needing a session — the real
  upload → confirm → interpret path — is still operator-run business acceptance,
  and a deployment that self-checks green does not claim it.
- **Rollback** is re-running the same script with `--rollback-to <commit>`. The
  host keeps a frontend snapshot per deployment, and the script restores it
  automatically when the self-check fails. There is deliberately no "pick a
  commit" input on the workflow: that would let whoever can trigger a deploy ship
  a revision that never passed the required checks.

## Providers

The configured Sandcastle profile is server-global: `claude` or
`claude-deepseek`. Set `AFK_PROFILE` for local runs or the repository variable
for Actions; no project-side credential is needed.

Both are one settings file the host owns, mounted read-only into the sandbox:

- `claude` talks to the Anthropic API with whatever credential the host shell
  already exports.
- `claude-deepseek` points Claude Code at the host-local cli-proxy-api relay's Anthropic Messages
  API through `~/cliproxyapi/settings.deepseek.json`, which holds
  `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` plus the three
  `ANTHROPIC_DEFAULT_*_MODEL` entries. Override the path with
  `AFK_DEEPSEEK_SETTINGS` when the file lives elsewhere.

Because the endpoint is mounted rather than baked, rotating the token is an
edit to that host file plus a container restart — there is no image rebuild,
and no `--no-cache` to remember. The base URL is the provider root preceding
`/v1`; Claude Code appends `/v1/messages` itself, so a URL ending in `/v1`
would be requested as `/v1/v1/messages`.
