"""Single source of truth for report ownership.

"Whose report is this, and who may see it" is the first boundary every patient
endpoint crosses (CONTEXT.md's 报告上传 entry says it "establishes ownership and
the access boundary").  Before this module the answer came from three parallel
channels — a ``request.state.owner_id`` written by the basic-auth middleware, a
``request.state.account_id`` written by the session resolver, and a priority
rule buried in an API helper — with no shared code between them.

This module owns *all* of it: the two writers hand their raw material to
:func:`resolve_owner`, and every consumer reads the typed result.  The priority
is unchanged from the rule it replaces (account > operator > unowned) so no
existing behaviour moves; what changes is that the rule is now named, typed, and
testable in one place instead of being re-derived per caller.

The typed subject is deliberately *wider* than the stored column: ``owner_id``
keeps holding a bare string for backward compatibility (no migration), while the
subject carries which kind of identity it is, so audit rows and future callers
stop guessing from the string's shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fastapi import Request

#: The literal stored in ``owner_id`` for a report nobody owns.  Kept verbatim:
#: existing rows carry it, and changing it would strand them.
UNOWNED_SENTINEL = "anonymous"


class OwnerKind(StrEnum):
    """Which identity channel answered the ownership question."""

    ACCOUNT = "account"
    OPERATOR = "operator"
    UNOWNED = "unowned"


@dataclass(frozen=True, slots=True)
class ReportOwner:
    """The typed winner of the ownership question for one request.

    ``storage_id`` is what goes into the column or is compared against it.
    ``subject`` is the same identity with its kind named, for audit and for
    callers that must branch on the kind.
    """

    kind: OwnerKind
    storage_id: str

    @property
    def subject(self) -> str:
        """A labelled form, so an audit reader can tell the three kinds apart.

        The unowned case is *not* labelled: ``unowned`` is the name of the
        concept, but the stored value is the legacy sentinel, and a report's
        owner cell should not sprout a prefix that no existing row has.
        """

        if self.kind is OwnerKind.UNOWNED:
            return self.storage_id
        return f"{self.kind.value}:{self.storage_id}"

    @property
    def is_unowned(self) -> bool:
        return self.kind is OwnerKind.UNOWNED

    @property
    def is_account(self) -> bool:
        return self.kind is OwnerKind.ACCOUNT


def resolve_owner(request: Request) -> ReportOwner:
    """Resolve the request's report owner from the two identity channels.

    Precedence is preserved from the rule this replaces: an authenticated
    account wins over a configured operator identity, which wins over unowned.
    """

    account_id = getattr(request.state, "account_id", None)
    if account_id:
        return ReportOwner(OwnerKind.ACCOUNT, str(account_id))
    operator = str(getattr(request.state, "owner_id", "") or "").strip()
    if operator and operator != UNOWNED_SENTINEL:
        return ReportOwner(OwnerKind.OPERATOR, operator)
    return ReportOwner(OwnerKind.UNOWNED, UNOWNED_SENTINEL)


def owner_id_for(request: Request) -> str:
    """The value to store or compare — the only string callers should need."""

    return resolve_owner(request).storage_id


#: ``/ready`` has reported these three words since before the ownership model
#: was named.  They describe the *same* three cases as :class:`OwnerKind`, but
#: they are an operator-facing vocabulary already baked into monitoring, so the
#: mapping is explicit rather than "reuse the enum values and see who notices".
_READY_WORDS = {
    OwnerKind.ACCOUNT: "account",
    OwnerKind.OPERATOR: "configured",
    OwnerKind.UNOWNED: "unconfigured",
}


def report_owner_kind(*, account_required: bool, operator_configured: bool) -> str:
    """The ``/ready`` projection, derived from the same model as the resolver.

    Kept here rather than in the route so the three reported values and the
    runtime precedence cannot drift apart.
    """

    if account_required:
        kind = OwnerKind.ACCOUNT
    elif operator_configured:
        kind = OwnerKind.OPERATOR
    else:
        kind = OwnerKind.UNOWNED
    return _READY_WORDS[kind]
