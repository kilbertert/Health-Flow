# Long-running readers and configuration freshness

A `systemd` `EnvironmentFile=` is read **once, at start**. Every process on the
service host that reads `var/health-flow.env` therefore holds a snapshot from its
own start time, and nothing notices when the file moves on. The application
cannot fix this from the inside — it never learns that the file changed — so the
invariant is enforced where the processes are managed: `PartOf=` makes a sibling
restart when its parent does, and a `.path` unit makes a restart happen when the
file itself changes. `/ready` reports the resulting freshness (`current` /
`stale` / `unknown`) so that the mechanism failing is visible rather than silent.

## What was measured

On 2026-10-10 every uploaded report failed with the patient-visible
「报告智能解读失败，请重新上传或稍后重试。」 while `/ready` reported
`report_provider: configured` (#201). The evidence chain:

- `journalctl -u health-flow-report-worker` carried the real error:
  `<file>.jpg: Error code: 401 - Invalid 'Authorization' header or token.`
- **Controlled experiment** — same host, same instant, same request body, only
  the credential differing: the key in the process's `/proc/<pid>/environ`
  returned **401**; the key in `var/health-flow.env` on disk returned **200**.
- `systemctl show … -p ExecMainStartTimestamp` on the worker read
  `2026-09-30 16:49:46`; the env file's mtime read `2026-10-10 14:08:56`.

The same shape held in three places at once: the worker held a superseded
credential, the portal held a superseded credential, and the worker — started
09-30 16:49 — had also been running ~10-day-old **code**, because
`deploy-36.sh` restarts only `health-flow` and the worker is not in that set.

## What was decided

Three mechanisms, each closing a different trigger. None of them is the
application's business to implement, and none of them depends on an operator
remembering a command.

1. **`PartOf=health-flow.service` on the worker unit.** The deploy already
   restarts the portal; the sibling must come with it rather than keep running
   old config *and* old code. Measured on real transient units (systemd 249):
   restarting only the parent restarts an active child; a child that is
   **disabled but active** (the worker's exact state — the README keeps it
   disabled on purpose) still propagates; a child the operator deliberately
   **stopped** stays stopped, so the documented paused state is not undone.

2. **`health-flow-env.path` → `health-flow-env-restart.service`.** `PartOf=`
   only covers "a restart happened". A hand-edit of the env file — the exact
   shape of this incident — restarts nothing, so the only mechanism that
   guarantees a re-read is "restart the readers when the file changes". A
   `.path` unit is the platform's native construct for that; the alternative (the
   application re-reading the file, or an `ExecStartPre=` diff) is more code for
   the same guarantee. Measured: `PathChanged=` fires on redirect-write, append,
   and `sed -i` rename-replace, covering the common hand-edit forms. The
   triggered unit restarts **both** readers — `.path` accepts one `Unit=`, and
   restarting half the pair is this defect's shape.

   It uses **`try-restart`, not `restart`.** `restart` starts what is not
   running, so an env edit would silently un-pause a worker the operator had
   deliberately stopped — undoing the documented "extraction stays paused"
   acceptance state by side effect. `try-restart` restarts exactly the readers
   that are running and leaves a stopped one alone, which is property 4 stated as
   the command rather than as a coincidence of file permissions. Measured: with
   both running each restarts once; with the worker stopped, only the portal
   restarts. (`start` is rejected outright: it is a no-op on an active unit, so
   the env change would silently do nothing — this defect's own shape.)

   `PartOf=` and this oneshot cannot double-restart: measured, a unit named both
   explicitly and reachable through `PartOf=` from another named unit still
   starts exactly once.

3. **`/ready` reports `config_freshness`.** The mechanisms above prevent the
   defect; this makes *their failure* visible. `stale` means the env file's
   mtime is later than the process's start time (read from `/proc/<pid>`, which
   agrees with `/proc/<pid>/stat` to within 6 ms — no `psutil` needed), and it
   degrades `status`, which `deploy-36.sh`'s self-check already asserts against
   `"ready"`. So the existing gate catches it; no new gate was added.

## Consequences accepted

- **A restart can interrupt an in-flight parse.** `claim_next_job` marks a job
  `running`, and `recover_stale_jobs` only re-queues it once `updated_at` is
  older than `REPORT_JOB_STALE_SECONDS` (1200 s). An interrupted report can
  therefore sit at "parsing" for up to 20 minutes before being recovered and
  succeeded. This is delay, not data loss — `recover_stale_jobs` already handles
  the "parse committed but the job was never acked" case. Shortening the window
  needs the worker to handle `SIGTERM` and re-queue its job; that is its own
  ticket.
- **`unknown` does not degrade `status`, but `stale` does.** `stale` is a
  positively observed fact; `unknown` only means "this process was not told which
  file feeds it". Degrading on `unknown` would create a deployment-order trap:
  installing the units is an operator step that happens *after* the merge that
  triggers a deploy, so that deploy would roll itself back over a condition that
  is not a defect. `stale` detection still works before the `.path` unit is
  installed — the file only has to be newer than the process — so `unknown` does
  not need to be an alarm.
- **Installing units stays manual.** `deploy-36.sh` deliberately does not touch
  them and `cd.yml` deliberately does not fire on `ops/service-host/systemd/**`;
  an exposure or unit change is its own decision. That decision is unchanged
  here, which means a merge that changes a unit does nothing until an operator
  installs it. The README's install step is the only path.

  The ordering that follows: the code lands first, the units are installed after.
  The first deploy is therefore green with `config_freshness: unknown` (the
  installed unit does not set `HEALTHFLOW_ENV_FILE` yet) — which is exactly why
  `unknown` must not degrade `status`. A two-valued field would have that deploy
  roll itself back over a condition that is not a defect.

- **The `/ready` probe does not check the credential's validity.** It reports
  whether the loaded configuration is *current*, not whether the provider accepts
  it. The distinction matters here only because both were involved in the
  incident: the credential was superseded (freshness) and therefore rejected
  (validity). Probing the provider on every readiness call would let one upstream
  hiccup pull this service out of rotation for a fault that does not affect
  report reading — the same reasoning already written down in `app/main.py` for
  the mall connection.

- **A `.path` failure has no channel other than `/ready`.** If the oneshot's
  `try-restart` fails, the env change happened and nothing restarted; `/ready`
  reports `stale` the next time anyone asks, and the next deploy's self-check
  fails and rolls back. A sentinel file would give a louder signal, but the unit's
  `ReadWritePaths` excludes `/run`, and any file it *can* write needs a lifecycle
  nobody currently owns. The decision is to let `/ready` be the channel and to
  write that down, rather than to add a sentinel with an unowned cleanup story.

- **A stopped worker leaves an in-flight job `running` indefinitely.**
  `recover_stale_jobs` runs at the top of the worker's own loop, so while the
  worker is stopped a job claimed just before the stop is never recovered — there
  is no 20-minute ceiling in that case. The window is bounded by "until someone
  starts the worker", not by `REPORT_JOB_STALE_SECONDS`. Recorded so it is not
  later discovered as a mystery; the SIGTERM ticket above is where it would be
  fixed.

## Alternatives rejected

- **Re-read the env file inside the application.** It would have to watch a file
  the process was never told the path of, and it would fix only the Python
  readers — not the code-freshness half (the worker ran 10-day-old code), which
  is a restart, not a re-read.
- **Make `deploy-36.sh` restart a list of units, or install units.** The former
  still misses the hand-edit case, and the latter reverses an explicit decision
  the repository already made and tests (`tests/test_cd_workflow.py`). Note that
  the restart step's *log* now understates what happened — it names one unit and
  the worker comes along via `PartOf=` — so a comment there says so, and a static
  test pins `SERVICE`'s default against the units' `PartOf=` target.

- **Poll the env file on a timer instead of watching it.** A timer works, but it
  adds an always-waking unit plus a class of "what if it stopped" questions for no
  benefit over the kernel's own inotify watch.

- **Re-queue the reports this incident failed.** Five rows; an operator statement
  against the live database, not a code path. `/ready` was green throughout it,
  which is the part that merited code.
- **`BindsTo=` / `ReloadPropagatedFrom=` / `PropagateReloadTo=`.** Both units
  report `CanReload=no` and define no `ExecReload=`, and the app has no reload
  handler — there is nothing to propagate a reload *to*. `BindsTo=` is stronger
  than needed: it would take the worker down whenever the portal is restarted,
  including a failed one, where `PartOf=` only forwards the restart.
- **Add 401/403 to `_TRANSIENT_ERROR_MARKERS`.** A 401 is not transient, and
  retrying with the same bad credential is futile; the fix is to not produce a
  401. The patient-facing 「请重新上传」 is genuinely misleading for this class of
  failure, but that is a separate UX decision with its own ticket.
