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
| `/var/log/health-flow` | service logs, owned by the identity |
| `/var/backups/health-flow` | host-side backups |

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
   identity. The evidence API key must **match** the evidence service's; read it
   from that service's own file as root and pass it in, because the identity
   cannot read the other project's secrets — by design.
7. Install the units and `systemctl enable --now health-flow`.

The report worker is installed but **left disabled**: report extraction stays
paused under the same single-topic low-speed acceptance that the development
host is under. Uploading still works; jobs queue durably until the worker runs.

## The frontend is built elsewhere

This repository does not track build output and the host has no Node toolchain,
so the frontend is built on a development host and transferred as an artifact.
It is not built at deploy time on the target.

## Verification

Two probes, both run as the service identity:

- `upload-probe.sh` — asserts the account boundary and the upload contract:
  registration yields a session, a protected route is reachable with it, and an
  upload returns `202 processing`. It exits nonzero on mismatch.
- `bridge-probe.py` — calls the same function the report API calls to reach the
  evidence service, so it proves the real cross-service path (configuration,
  key, loopback edge, response contract) rather than a synthetic request.

To confirm the queue is durable rather than in-process, check that a job row is
written while the worker is stopped: the report should sit at `processing` with
its job `queued`.
