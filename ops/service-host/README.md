# health-flow: service host

Deployment assets for running HealthFlow on a **service host** — a machine that
serves real users, as opposed to the development host.

Separate from `ops/systemd/`, which holds the development host's
`systemctl --user` units. A service host has no interactive account to own a
user manager, so its units are system units under a project-scoped identity.

## Service identity

HealthFlow has **its own** system identity, distinct from the evidence
service's. The two share no group, so neither can read the other's credential
files. That is the point of one identity per project: a defect in either
service cannot reach the other's secrets, and reaching them would require
privilege escalation rather than mere file permissions.

## Layout

| Path | Holds |
| --- | --- |
| `/opt/health-flow` | project root, owned by the service identity |
| `/opt/health-flow/artifacts` | the wheel and frontend bundle that were deployed |
| `/opt/health-flow/var` | database and uploaded report files — **runtime data** |
| `/opt/health-flow/data` | required by the application; see the note below |
| `/opt/health-flow/frontend` | the built frontend served when `SERVE_FRONTEND=true` |
| `/opt/health-flow/ops` | operational scripts from this directory |
| `/opt/health-flow/deployed-revision` | the **部署修订** — the full 40-character sha of the commit that is live |
| `/var/log/health-flow` | service logs, owned by the identity |
| `/var/backups/health-flow` | host-side backups, one frontend snapshot per deployment |

## Deploying a revision

**Use `deploy/deploy-36.sh`.** It is the only deployment path: `.github/workflows/cd.yml`
calls it on every merge to `main`, and a human emergency rollback calls the same
script with `--rollback-to <commit>`. Two separate entry points would drift apart
— that is exactly how the tar/rsync pairing bug documented in AI-Ops happened.

```bash
# from the repository checkout, on the development host
deploy/deploy-36.sh --commit <sha>            # what cd.yml runs
deploy/deploy-36.sh --commit <sha> --dry-run  # build + print the remote command; touches nothing
deploy/deploy-36.sh --rollback-to <sha>       # re-deploy a known-good revision
```

What it does, and the reasoning that is easy to lose:

- **The artifact is built here, not there.** The host has no Node toolchain and
  no `uv`, and `/opt/health-flow` is not a git checkout. This matches the section
  below: the frontend is built on a development host and transferred.
- **Everything reaches the host under an artifact identity.** The transfer goes
  through `dev-host cp --artifact-sha256`, so the write gate enforces "a change
  arrives as an identified artifact" rather than merely requesting it.
- **The frontend directory is replaced, not extracted over.** Extracting over the
  live tree leaves files the new `index.html` no longer references, so `assets/`
  ends up holding more than one build. This was observed for real on 2026-10-06.
- **The marker and every comparison use the full 40-character sha.** A short sha
  compared against a long one makes every idempotent re-run look stale.
- **It reports binding drift and does not fix it.** The unit on the host runs
  `--host 0.0.0.0`; `ops/service-host/systemd/health-flow.service` declares
  `127.0.0.1`. Closing that gap is a change of exposure, and the policy is that an
  exposure is changed from *observed traffic*, not from which ports are listening.
  The deploy script reports the divergence so it is visible, and leaves the
  decision to a separate, deliberate change.
- **After deploying it self-checks two machine-answerable things**: the shape of
  `/ready`, and that the entry point is serving the bundle this build produced
  (byte-for-byte, by sha256). A failed self-check restores the frontend snapshot
  taken *before* any write and rolls the marker back.

**The report worker stays disabled.** Deployment does not enable it; report
extraction remains paused under the low-speed single-topic acceptance recorded
below. Uploading still works and jobs queue durably.

## Installation

1. Create the identity, the directories above, and grant `data/` to it.
2. Build the Python wheel and the frontend bundle from a merged revision, and
   record their sha256 values.
3. Transfer both into `artifacts/`. The write gate refuses an unidentified
   write, so the sha256 is enforced rather than merely requested. Transferred
   files arrive owned by root: re-own them to the service identity before use.
4. Extract the frontend bundle into `frontend/`, then re-own the tree.
5. Install the wheel into the project virtualenv.
6. Write `var/health-flow.env` from `examples/`, mode `640`, owned by the
   identity. Two settings need care:

   - The evidence API key must **match** the evidence service's; read it from
     that service's own file as root and pass it in, because this identity
     cannot read the other project's secrets — by design.
   - `APP_ENV` stays `development` because `production` would make the
     application select MySQL instead of the SQLite tenant. That means the two
     settings normally inferred from `APP_ENV` must be set explicitly:
     `REPORT_ACCOUNT_REQUIRED=true` and `AUTH_COOKIE_SECURE=true`. Omitting the
     latter leaves session cookies without `Secure`, so a user's session token
     could cross plaintext HTTP at any route in front of the app.

   `OPENAI_API_KEY` (or `VLLM_API_KEY`) must be present for the report worker to
   do anything. Without it the service still starts and accepts uploads, and
   `/ready` reports `report_provider: unconfigured` — a configuration gap that
   is invisible until the worker is enabled.

7. Install the units and enable the two that must start at boot. Confirm
   `/ready` reports `report_provider: configured`, `account_auth: required`, and
   `config_freshness: current`.

   ```bash
   # four files: the two readers, the watcher, and the oneshot it triggers
   install -m 0644 ops/service-host/systemd/health-flow.service \
                    ops/service-host/systemd/health-flow-report-worker.service \
                    ops/service-host/systemd/health-flow-env.path \
                    ops/service-host/systemd/health-flow-env-restart.service \
                    /etc/systemd/system/
   systemctl daemon-reload
   # The worker is deliberately NOT enabled — see the note above. It still needs
   # to be *started* once if extraction is in service; enabling it is what stays
   # undone.
   systemctl enable --now health-flow health-flow-env.path
   ```

   `health-flow-env-restart.service` has no `[Install]` on purpose: it is not
   started at boot, only triggered by the `.path` unit. Enabling it would do
   nothing and would suggest it is a service in its own right.

   **Installing the units is an operator step, not a deploy step.** `deploy-36.sh`
   deliberately does not touch them, and `cd.yml` deliberately does not fire on
   `ops/service-host/systemd/**` — an exposure or a unit change is its own
   decision. So after a merge that changes a unit, nothing has happened until
   you run the block above.

   **Why `health-flow-env.path` exists.** `EnvironmentFile=` is read **once, at
   start**. Editing `var/health-flow.env` by hand — no code change, no deploy —
   leaves the running processes holding the previous values. That is exactly
   what failed on 2026-10-10: the worker started 09-30 16:49, the env file was
   replaced 10-10 14:08:56, and every report failed with a provider 401 while
   `/ready` cheerfully reported `report_provider: configured`. The `.path` unit
   watches the file and restarts **both** readers on any change; `PartOf=` on the
   worker covers the other half (a deploy restarts the parent, and the sibling
   must come along rather than keep running old config *and* old code).

   `/ready` now also reports `config_freshness` (`current` / `stale` /
   `unknown`), and `stale` degrades `status`. So if the mechanism above is ever
   missing or broken, `deploy-36.sh`'s own self-check — which asserts
   `status == "ready"` — catches it instead of reporting a green deploy. The
   plain consequence to remember: **a deploy whose reader did not restart now
   fails its own self-check and rolls itself back**, which is the intended
   behaviour and not a bug in the deploy script.

   `unknown` (the process was never told which file feeds it) does **not**
   degrade `status`. That asymmetry is deliberate and it is what makes the
   rollout order safe: this code merges and deploys first, the units are
   installed after, and the first deploy runs against a unit that does not yet
   set `HEALTHFLOW_ENV_FILE`. A two-valued field would have that deploy roll
   itself back over a condition that is not a defect.

   **After editing the env file you no longer need to restart anything by hand.**
   That is the point of the `.path` unit. Verify it rather than trusting it:

   ```bash
   systemctl show health-flow health-flow-report-worker -p ExecMainStartTimestamp
   ```

   The watcher uses `try-restart`, so an env edit does **not** un-pause a worker
   you deliberately stopped — the "extraction stays paused" state above survives
   an env change, a deploy, and anything else that restarts the portal.

**Why `data/` exists as well as `var/`.** The application creates a *relative*
`data/` directory at startup whenever the database URL is SQLite, regardless of
where the absolute `DATABASE_URL` points. Under `ProtectSystem=strict` the
working directory is read-only, so the unit crash-loops unless `data/` exists
and is listed in `ReadWritePaths`. It is a separate path from `var/` because
`var/` is this project's convention while `data/` is what the code demands.

### Before deploying a revision that changes the evidence contract

The bundled migration ships in the wheel as `health-flow-migrate-evidence`. Run it
**before** switching the service to the new revision, because the new revision's
response model is strict (`extra="forbid"`) and rejects the retired fields still
stored in `medical_reports.evidence_result` — until it runs, those reports return
an error instead of opening.

```bash
# Run as the service identity. A login shell does NOT inherit the unit's
# EnvironmentFile, so load it explicitly — without DATABASE_URL the command would
# silently target the default `./data/healthflow.db` instead of the live tenant.
set -a; . /opt/health-flow/var/health-flow.env; set +a
cd /opt/health-flow

/opt/health-flow/.venv/bin/health-flow-migrate-evidence --dry-run
# SQLite tenant: back up first
cp /opt/health-flow/var/healthflow.db /opt/health-flow/var/healthflow.db.bak-$(date +%Y%m%d%H%M%S)
/opt/health-flow/.venv/bin/health-flow-migrate-evidence
```

The executable lives in the project virtualenv (`/opt/health-flow/.venv/bin`), the
same one the units use — the printed line starts with the resolved `database=` so a
wrong target is visible before any write.

It is idempotent — a second run reports `changed=0`. It never deletes a report and
leaves payloads it cannot parse untouched, reporting them instead.

The report worker is installed but **left disabled**: report extraction stays
paused under the same single-topic low-speed acceptance that the development
host is under. Uploading still works; jobs queue durably until the worker runs.

## The frontend is built elsewhere

This repository does not track build output and the host has no Node toolchain,
so the frontend is built on a development host and transferred as an artifact.
It is not built at deploy time on the target.

## Verification

The **deployment's own** self-check is `deploy/deploy-36.sh` (see above): it asserts
`/ready`'s shape and that the entry point serves the bundle this build produced.
That runs on every merge, unattended. Because `/ready` degrades on
`config_freshness: stale`, this same self-check is also the guard that a reader
did not restart — there is no second check for it.

**What `/ready` answers that the other probes do not.** `report_provider:
configured` only says a key is *present*; it says nothing about whether the
provider accepts it, and nothing about whether the process is holding a
superseded copy. Three fields cover that:

```json
{"config_freshness": "current|stale|unknown",
 "config_file_changed_at": "<iso8601|null>",
 "process_started_at": "<iso8601|null>"}
```

`stale` means the env file's mtime is later than this process's start time — the
exact condition that made every report fail on 2026-10-10 while `/ready` stayed
green. `config_file_changed_at` and `process_started_at` are there so "why is it
stale" is answerable from the response alone, without logging into the host.

Note what it still does **not** check: whether the credential is *valid*. Probing
the provider on every readiness call would let one upstream hiccup pull the
service out of rotation for a fault that does not affect report reading — the
same reasoning already recorded in `app/main.py` for the mall connection.

The probes below are the operator-run acceptance checks, all run as the service
identity, all exiting nonzero on mismatch:

- `upload-probe.sh` — **stale, and it will report a false failure.** It asserts
  `POST /api/auth/register` returns 201, but that route was retired with the
  account system and the live deployment answers **405** (measured 2026-10-09).
  Sessions are now minted by exchanging a mall ticket. Fixing it needs a ticket
  the development host cannot mint, so the repair is its own ticket and this file
  does not claim the probe passes.
- `cookie-probe.sh` — asserts the session cookie's hardening attributes on their
  own, printing attribute names only and never the token value. It registers an
  account first, so it is stale in the same way as above.
- `bridge-probe.py` — calls the same function the report API calls to reach the
  evidence service, so it proves the real cross-service path (configuration,
  key, loopback edge, response contract) rather than a synthetic request.

A probe that cannot fail is not a verification. Each of these asserts against an
expected value; the negative cases exist so that a deployment which let
anonymous callers through would be caught rather than reported as healthy.

To confirm the queue is durable rather than in-process, check that a job row is
written while the worker is stopped: the report should sit at `processing` with
its job `queued`.
