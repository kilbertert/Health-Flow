"""Bound how many full test runs exist at once, host-wide.

Copied from `AI-Ops/tests/conftest.py` (2026-10-23). The incident it answers was
not specific to that repository: several agent sessions ran the suite at the same
time on one machine, load average went 5.28 -> 79, and `sshd`/`tailscaled` could
not get CPU — the host stopped being reachable, including the tailnet used to
manage it. Seven runs were live. Whichever repository they belong to, they draw
from the same cores.

WHY THIS LIVES PER-REPOSITORY rather than in one shared file. There is no shared
place for a pytest hook that all three repositories load — no common package, no
installed plugin. `conftest.py` is the only thing pytest always loads, and it has
to be in the repository. So the rule is duplicated, and the duplication is
deliberate: two files that must agree, versus a hook nobody loads.

⚠️ THE LOCK FILE NAMES ARE SHARED WITH THE OTHER REPOSITORIES, and that is the
whole point. `host-test-slot-N.lock` carries no repository name on purpose: two
repositories running at once exhaust the same machine. A per-repository name would
give each its own pair of slots, which is exactly the bug the AI-Ops first version
shipped (it keyed on the checkout, so seven worktrees took seven locks and none
queued).

⚠️ Not the same file name as AI-Ops used, for a reason that no longer applies —
AI-Ops has since adopted these exact names. See that file's header; the two must
be read together, because a divergence between them is a silent loss of the cap.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import tempfile

import pytest

#: How many full runs the HOST will hold at once, across every repository using
#: this gate. Two, because the machine is 8 cores shared with sshd, tailscaled,
#: self-hosted runners and long-lived agent sessions; the AI-Ops incident was
#: seven. Rationale and the measurement are in AI-Ops' copy of this file.
MAX_CONCURRENT_RUNS = 2

#: Give up rather than wait forever. A run queued this long will not produce a
#: result anyone is still waiting for, and failing loudly beats a silent hang.
WAIT_SECONDS = 1800

#: Overridable so a CI box with nothing else on it can widen the gate.
SLOT_ENV = "PYTEST_HOST_SLOTS"


#: Where the slot files live. ⚠️ NOT `tempfile.gettempdir()`.
#:
#: The AFK agent runs inside a Docker sandbox (`.sandcastle/profile.ts`), and that
#: container has its OWN `/tmp` — the mounts are settings and MCP only, because
#: nothing needed a shared filesystem until now. Two sandboxed runs therefore take
#: slot 0 of two different filesystems and neither blocks, which is precisely the
#: scenario this gate exists for: the incident was several agent sessions at once.
#: Measured by review on the first version of this file, which used `/tmp` and was
#: correct on the host and inert in every container.
#:
#: So the directory is fixed and host-backed, and `.sandcastle/profile.ts` mounts
#: it into every sandbox at the same path. A fixed path (rather than TMPDIR) is
#: what lets the mount be unconditional: a per-run TMPDIR would need the sandbox to
#: know the run's name before it starts.
#:
#: Falls back to TMPDIR only when the shared directory cannot be created, so a
#: machine without it degrades to host-only gating instead of failing the suite.
SHARED_LOCK_DIR = pathlib.Path("/tmp/aiops-host-test-slots")


def _lock_dir() -> pathlib.Path:
    try:
        SHARED_LOCK_DIR.mkdir(mode=0o777, parents=True, exist_ok=True)
        # ⚠️ `mkdir(mode=...)` is masked by the process umask (027 on this host),
        # so the directory ends up 750 — owned by the host user, unwritable by the
        # sandbox's `agent` user, whose UID differs. The gate would then fail
        # inside every container with a permission error, which reads like a test
        # bug rather than a mount problem. Set the mode explicitly, after.
        SHARED_LOCK_DIR.chmod(0o777)
        return SHARED_LOCK_DIR
    except OSError:
        return pathlib.Path(tempfile.gettempdir())


def _host_lock_path(index: int) -> pathlib.Path:
    # ⚠️ Deliberately NOT keyed on the repository or the checkout: the resource
    # being exhausted is the host's CPU, which every checkout and every project
    # shares. The file NAME is the only contract between the three copies of this
    # file; change it in one and the cap silently stops adding up.
    return _lock_dir() / f"host-test-slot-{index}.lock"


def _open_slot(path: pathlib.Path) -> object:
    """Open a slot file so that a DIFFERENT uid can open it too.

    ⚠️ The 0o666 is load-bearing, not tidy, and it took two corrections to get
    right. This process runs as the host user; the sandbox runs as `agent`, a
    different UID. Under umask 027 the file is created 0640, so the container gets
    `PermissionError` — which surfaces as a test error inside the sandbox and looks
    like a broken test rather than a lock nobody can share. 0644 is still wrong:
    `flock` needs `O_RDWR`, and a read-only file cannot be opened for write by a
    non-owner. Measured both: 0640 raised on open, 0644 raised on open, 0666 locks.

    The lock is coordination state, not a secret — nothing in it but an empty file
    whose lock bit is the whole payload.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
    with contextlib.suppress(OSError):  # existing files keep their old mode
        os.fchmod(fd, 0o666)
    return os.fdopen(fd, "r+")


def _acquire(path: pathlib.Path, fcntl, time, what: str) -> object:
    """Take an exclusive flock, polling so the wait can be bounded."""
    handle = _open_slot(path)
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                raise pytest.UsageError(
                    f"waited over {WAIT_SECONDS}s for {what}; refusing to queue forever. "
                    f"Check for a stuck pytest, or raise {SLOT_ENV}/WAIT_SECONDS."
                ) from None
            time.sleep(0.5)


def _acquire_host_slot(fcntl, time, slots: int) -> object:
    """Lowest free host-wide slot, or a bounded wait.

    `flock` has no queue, so "wait for slot 0" would leave a run blocked behind
    slot 0 while slot 1 sits idle; probing every slot is what makes N the actual
    concurrency.
    """
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        for index in range(slots):
            handle = _open_slot(_host_lock_path(index))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            return handle
        if time.monotonic() >= deadline:
            raise pytest.UsageError(
                f"all {slots} host test slots have been busy for {WAIT_SECONDS}s; "
                f"refusing to queue forever. Raise {SLOT_ENV} or WAIT_SECONDS."
            )
        time.sleep(0.5)


def pytest_configure(config: pytest.Config) -> None:
    """Take a host-wide slot for the duration of the run."""
    import fcntl
    import os
    import time

    # ⚠️ Only the CONTROLLER process takes the lock. Under xdist, every worker runs
    # its own `pytest_configure` and would each try to acquire the same file — the
    # AI-Ops first version deadlocked exactly there (two concurrent runs hung
    # forever; a single run exited 0 having executed nothing). The worker marker is
    # set by xdist before the worker's config is built.
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return

    # ⚠️ NOT keyed on `numprocesses`, which is where this file first differed from
    # AI-Ops' copy and was wrong to. AI-Ops only takes the slot when `-n` asks for
    # more than zero workers, because that repository runs `-n 4` by default and a
    # serial `-n 0` run is its documented iterate-on-one-file path.
    #
    # This repository is the opposite: it has no scheduler by default, so EVERY run
    # is serial, and keying on `numprocesses` made the gate inert — verified, two
    # runs took slots nobody was holding and the positive control (occupy both
    # slots externally, then run pytest) finished in 4 s instead of blocking.
    #
    # The resource the incident exhausted is CPU-seconds, and a serial run burns a
    # core just as surely as a parallel one: seven serial sessions is seven cores.
    # So the slot is taken for every run that actually executes tests — independent
    # of how many workers it uses.
    #
    # Cheap enough to hold for a single-file run: a slot is a file lock, and the
    # cost of queueing a 3 s run behind a 255 s one is a few seconds, not a hung
    # terminal.
    if config.option.collectonly:
        return

    slots = int(os.environ.get(SLOT_ENV) or MAX_CONCURRENT_RUNS)
    handle = _acquire_host_slot(fcntl, time, slots)

    # Keep the handle alive on the config so it is not garbage-collected (which
    # would release the lock mid-run). pytest tears the config down at exit, and
    # the OS releases it when the process dies even if it does not.
    config._host_slot_handle = handle


def pytest_unconfigure(config: pytest.Config) -> None:
    """Release the slot explicitly, so a long-lived process does not hoard it."""
    handle = getattr(config, "_host_slot_handle", None)
    if handle is not None:
        handle.close()
        config._host_slot_handle = None
