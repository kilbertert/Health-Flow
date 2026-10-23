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


def _host_lock_path(index: int) -> pathlib.Path:
    # ⚠️ Deliberately NOT keyed on the repository or the checkout: the resource
    # being exhausted is the host's CPU, which every checkout shares.
    return pathlib.Path(tempfile.gettempdir()) / f"host-test-slot-{index}.lock"


def _acquire(path: pathlib.Path, fcntl, time, what: str) -> object:
    """Take an exclusive flock, polling so the wait can be bounded."""
    handle = open(path, "w")  # noqa: SIM115 - held for the whole session
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
            handle = open(_host_lock_path(index), "w")  # noqa: SIM115
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
