"""Report ownership resolves in one place, with one precedence.

These pin the *behaviour* the three parallel channels used to encode, so the
unification cannot quietly change who may see a report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.service.report_ownership import (  # noqa: E402
    UNOWNED_SENTINEL,
    OwnerKind,
    report_owner_kind,
    resolve_owner,
)


class _State:
    def __init__(self, **values):
        self.__dict__.update(values)


class _Request:
    def __init__(self, **values):
        self.state = _State(**values)


def test_account_wins_over_operator() -> None:
    """Precedence preserved: an authenticated account beats a Basic Auth operator."""

    owner = resolve_owner(_Request(account_id="acct-1", owner_id="operator-name"))

    assert owner.kind is OwnerKind.ACCOUNT
    assert owner.storage_id == "acct-1"
    assert owner.subject == "account:acct-1"
    assert owner.is_account
    assert not owner.is_unowned


def test_operator_used_when_no_account() -> None:
    owner = resolve_owner(_Request(owner_id="operator-name"))

    assert owner.kind is OwnerKind.OPERATOR
    assert owner.storage_id == "operator-name"
    assert owner.subject == "operator:operator-name"


def test_the_sentinel_is_not_an_operator_identity() -> None:
    """`anonymous` is the absence of an owner, not somebody's name."""

    owner = resolve_owner(_Request(owner_id=UNOWNED_SENTINEL))

    assert owner.kind is OwnerKind.UNOWNED
    assert owner.storage_id == UNOWNED_SENTINEL
    assert owner.is_unowned


def test_blank_operator_falls_back_to_unowned() -> None:
    owner = resolve_owner(_Request(owner_id="   "))

    assert owner.kind is OwnerKind.UNOWNED


def test_absent_state_is_unowned() -> None:
    """A request that never passed through either writer is still answerable."""

    owner = resolve_owner(_Request())

    assert owner.kind is OwnerKind.UNOWNED
    assert owner.storage_id == UNOWNED_SENTINEL


def test_unowned_subject_is_not_labelled() -> None:
    """The stored value for an unowned report must not gain a prefix.

    Existing rows carry the bare sentinel; labelling it would describe a
    different string than the one on disk.
    """

    assert resolve_owner(_Request()).subject == UNOWNED_SENTINEL


@pytest.mark.parametrize(
    ("account_required", "operator_configured", "expected"),
    [
        (True, True, "account"),
        (True, False, "account"),
        (False, True, "configured"),
        (False, False, "unconfigured"),
    ],
)
def test_ready_projection_keeps_its_published_vocabulary(
    account_required: bool, operator_configured: bool, expected: str
) -> None:
    """/ready's three words predate this module; monitoring reads them."""

    assert (
        report_owner_kind(
            account_required=account_required, operator_configured=operator_configured
        )
        == expected
    )
