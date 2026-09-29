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

**Audit rows written before this module keep their original vocabulary.** They
are evidence of what happened, and rewriting an audit trail into a newer naming
convention would edit the record rather than describe it; that is a separate,
deliberate decision and not one this refactor should make in passing. So the
column holds two conventions, separated in time: bare identifiers before, typed
subjects after. A reader that needs to be certain which it has should use the
row's timestamp, not the string's shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fastapi import Request

#: The literal stored in ``owner_id`` for a report nobody owns.  Kept verbatim:
#: existing rows carry it, and changing it would strand them.
UNOWNED_SENTINEL = "anonymous"


class OwnerKind(StrEnum):
    """Which identity channel answered the ownership question.

    **`ACCOUNT` 现在的含义是「本服务的会话身份」，不是「账号表里的一行」。**
    它涵盖两种来源：商城票据兑换出的主体（`account:<tenant>:<sub>`，本票之后唯一
    会新产生的来源），以及退役期内的账户会话（`account:<uuid>`，历史行仍是这个形状）。

    **为什么没有为票据主体新增 `subject` kind**：`kind` 描述的是「这是本服务的哪一类
    身份」——票据主体接替的正是账户会话的那个角色，它们是同一件事在不同时代的两副
    面孔。新增一个 kind 会让两种「来自会话的身份」并存，而它们本该是同一种。代价是
    名字看起来还留着旧时代；那是文档要解释的事，不是新增枚举值的理由。
    """

    ACCOUNT = "account"
    OPERATOR = "operator"
    #: 不再被产生：新上传必然带主体（无主报告这一类已随令牌机制一并退役）。
    #: **枚举值保留**——历史行里仍有它，读到时必须能识别并明确拒绝，
    #: 而不是遇到未定义状态就抛错。
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

        两条规则，都来自「标签只能加一次」：

        - **无主不加标签**（沿用原样）：`unowned` 是概念名，而存储值是那个历史哨兵，
          报告的所有者单元格不该长出任何既有行没有的前缀。
        - **已经带标签的不重复加**：主体标识 `account:<tenant>:<sub>` 本身就以上一级
          的 kind 词起头（见 `subject_storage_id`），再拼一次会得到
          `account:account:<tenant>:<sub>` —— 那是同一个词出现两次，读的人会以为
          有两层身份。
        """

        if self.kind is OwnerKind.UNOWNED:
            return self.storage_id
        prefix = f"{self.kind.value}:"
        if self.storage_id.startswith(prefix):
            return self.storage_id
        return f"{prefix}{self.storage_id}"

    @property
    def is_unowned(self) -> bool:
        return self.kind is OwnerKind.UNOWNED

    @property
    def is_account(self) -> bool:
        return self.kind is OwnerKind.ACCOUNT


def resolve_owner(request: Request) -> ReportOwner:
    """Resolve the request's report owner from the two remaining channels.

    **优先级不变：会话身份 > 运维身份 > 无主。** 本票只改了「会话身份从哪来」
    （票据主体取代账户会话），顺序一字未动。

    两点必须知道：

    - **运维身份的既有报告仍然可见**（它走 `OPERATOR` 这一支，不受账号退役影响）。
      「既有数据保留只读」这句话里的三种归属命运不同：账户归属在新体系下打不开
      （没有 uuid → (tenant, sub) 的可信映射）；运维归属仍可见；无主归属打不开。
    - **无主这一支不再被产生**（令牌机制随本票移除，上传必然带主体），但它仍在
      判定链里——历史行要能被正确归到 `UNOWNED`，而不是变成未定义状态。
    """

    account_id = getattr(request.state, "account_id", None)
    if account_id:
        return ReportOwner(OwnerKind.ACCOUNT, str(account_id))
    # The *value* is never normalised: it is compared against `owner_id` in
    # stored rows with `hmac.compare_digest`, so stripping it would stop matching
    # every report that operator already owns.  Only the emptiness *test* ignores
    # surrounding whitespace, matching how the middleware already decides whether
    # an operator is configured at all (`HEALTHFLOW_BASIC_USER.strip()`), so a
    # whitespace-only name cannot become an owner literally named " ".
    operator = str(getattr(request.state, "owner_id", "") or "")
    if operator.strip() and operator != UNOWNED_SENTINEL:
        return ReportOwner(OwnerKind.OPERATOR, operator)
    return ReportOwner(OwnerKind.UNOWNED, UNOWNED_SENTINEL)


#: `owner_id` 列宽。主体标识必须放得进去——超长会让写入在运行时才失败，
#: 而那时数据已经在上传路径上了。这里定义一个显式上界并断言。
OWNER_ID_MAX_LENGTH = 128


def subject_storage_id(tenant_id: str, external_subject: str) -> str:
    """票据主体在 `owner_id` 里的持久化形状：`account:<tenant>:<sub>`。

    沿用 `account:` 前缀，因为 `kind` 描述的是身份**种类**（来自会话的身份），
    而不是它背后的表——见 `OwnerKind.ACCOUNT` 的说明。
    """
    tenant = tenant_id.strip()
    subject = external_subject.strip()
    if not tenant or not subject:
        raise ValueError("主体标识的租户与用户标识都不能为空")
    value = f"{OwnerKind.ACCOUNT.value}:{tenant}:{subject}"
    if len(value) > OWNER_ID_MAX_LENGTH:
        raise ValueError(f"主体标识超出 {OWNER_ID_MAX_LENGTH} 字符：{len(value)}")
    return value


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
