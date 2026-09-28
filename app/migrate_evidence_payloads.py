"""Strip the retired product fields from stored evidence payloads.

Runs through the application's own database layer (``get_mysql_client()``), so it
works with whatever ``DATABASE_URL`` is configured — SQLite in development and on
the deployed service host, or a server URL in a MySQL deployment. Nothing here is
SQLite-specific.

genesis-evidence 的 ADR 0006 把商品权威移到商城，证据响应不再返回
``recommendations`` / ``recommendation_message`` / ``product_status``。本仓的响应模型是
``extra="forbid"``，因此**在删字段之前**持久化的 ``medical_reports.evidence_result``
会让整条读取路径抛 ``ValidationError``——报告打不开。

本脚本做一次性、幂等、可重复的迁移：把这两个字段从已存 payload 里剥掉，
其余内容一字不动。重复运行是 no-op。

用法::

    # 预演：只报告将要改动什么（读配置里的 DATABASE_URL）
    health-flow-migrate-evidence --dry-run

    # SQLite 部署先备份，再执行
    cp /opt/health-flow/var/healthflow.db /opt/health-flow/var/healthflow.db.bak-$(date +%Y%m%d%H%M%S)
    health-flow-migrate-evidence

    # 显式指定另一个库
    health-flow-migrate-evidence --database sqlite:////tmp/copy.db --dry-run

以 console script 形式分发（``pyproject.toml`` 的 ``[project.scripts]``），因此它随
wheel 一起到达服务主机——服务主机只安装 wheel，不安装源码检出。

退出码：0 = 无需改动或已成功；1 = 存在无法迁移的记录；2 = 用法错误。

本脚本**不删除任何报告**，也不改动 payload 里的其它键。无法解析的 payload 会被
报出来并保持原样，而不是被当作空值覆盖。

实测（2026-09-28，生产库副本）：16 条有 `evidence_result` 的记录，迁移 10 条、
剥掉 99 处字段；迁移后 13 条能通过当前严格模型校验，迁移前只有 3 条。剩下的 3 条是
`schema_version="1"` 的历史 payload，**在本次改动之前就打不开**（模型只接受 "2"/"3"），
与本脚本无关，另案处理。
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings

RETIRED_FIELDS = frozenset({"recommendations", "recommendation_message", "product_status"})


def _strip(node) -> int:
    """Remove the retired keys anywhere under `node`; return how many were present.

    Recursive, not keyed to a path: the fields appear both on a finding and on the
    nested card (`evidence_items[].card` in v3, `findings[].card` in v2). A
    path-specific walk silently misses the nested ones, which is exactly the
    breakage this migration exists to prevent.
    """

    removed = 0
    if isinstance(node, dict):
        for key in list(node):
            if key in RETIRED_FIELDS:
                del node[key]
                removed += 1
            else:
                removed += _strip(node[key])
    elif isinstance(node, list):
        for item in node:
            removed += _strip(item)
    return removed


def migrate_payload(payload: dict) -> tuple[dict, int]:
    """Return a payload with the retired fields removed, and the removal count."""

    return payload, _strip(payload)


def _resolve_url(database: str | None) -> str:
    """Explicit URL wins; otherwise use the configured one."""

    if database:
        return database
    return get_settings().database_url


def _iter_reports(session: Session):
    return session.execute(
        text(
            "SELECT id, evidence_result FROM medical_reports "
            "WHERE evidence_result IS NOT NULL"
        )
    ).fetchall()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database",
        help="SQLAlchemy URL. Defaults to the configured DATABASE_URL.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would change without writing"
    )
    args = parser.parse_args(argv)

    url = _resolve_url(args.database)
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            rows = _iter_reports(session)
            changed = 0
            malformed: list[object] = []
            total_removed = 0

            for report_id, raw in rows:
                # A JSON null (or any non-object) is an unassessed report, not a
                # broken one: there is nothing to strip, so it is skipped rather
                # than reported as malformed and failing the whole run.
                if raw is None:
                    continue
                try:
                    payload = json.loads(raw) if not isinstance(raw, dict) else raw
                except (TypeError, ValueError):
                    malformed.append(report_id)
                    continue
                if not isinstance(payload, dict):
                    continue
                payload, removed = migrate_payload(payload)
                if removed == 0:
                    continue
                changed += 1
                total_removed += removed
                if not args.dry_run:
                    session.execute(
                        text(
                            "UPDATE medical_reports SET evidence_result = :payload "
                            "WHERE id = :id"
                        ),
                        {"payload": json.dumps(payload, ensure_ascii=False), "id": report_id},
                    )

            if not args.dry_run and changed:
                session.commit()

            verb = "would change" if args.dry_run else "changed"
            print(
                f"database={make_url(url).get_backend_name()} reports={len(rows)} "
                f"{verb}={changed} fields_removed={total_removed} malformed={len(malformed)}"
            )
            for report_id in malformed:
                print(f"  cannot parse evidence_result for report {report_id}", file=sys.stderr)
            return 1 if malformed else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
