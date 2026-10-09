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

**Why `data/` exists as well as `var/`.** The application creates a *relative*
`data/` directory at startup whenever the database URL is SQLite, regardless of
where the absolute `DATABASE_URL` points. Under `ProtectSystem=strict` the
working directory is read-only, so the unit crash-loops unless `data/` exists
and is listed in `ReadWritePaths`. It is a separate path from `var/` because
`var/` is this project's convention while `data/` is what the code demands.

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

7. Install the units and `systemctl enable --now health-flow`. Confirm
   `/ready` reports `report_provider: configured` and `account_auth: required`.

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
That runs on every merge, unattended.

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
