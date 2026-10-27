"""报告的删除：先把数据库删掉，再把磁盘文件删掉（单份与按主体两处共用）。

删除有**两种规模**，但只有一条路径：

- 一份报告（`DELETE /report/{id}`，患者清理单个文件）；
- 一个主体的全部报告（撤回「报告解读」同意，GLOSSARY.md 的「健康数据同意」）。

两处都走 `delete_reports`，因为顺序这件事有两份实现就一定有一份会漂。顺序是
**先删库、后删文件**：

- 数据库那一半是事务的，失败就整体回滚 —— 患者重试即可，什么都没发生。
- 文件删不掉（`unlink` 失败）只记一条日志、不把整个删除判为失败：数据库已经提交，报告
  已经不在了，把它翻回失败只会让患者反复重试一件已经完成的事。**残留的字节**由运维侧的
  保留策略处理；这不是「报告还在」，因为没有任何一行指向它。
- 反过来（先删文件、后删库）时，一次失败的提交会留下**指向不存在文件的报告行**：患者看到
  一份打不开、也删不掉的报告。这条是单份删除此前就选定的方向，本模块只是把它变成唯一
  实现。

级联由 `MedicalReport` 的关系声明：指标、页图行、审计事件、抽取任务都是
`cascade="all, delete-orphan"`，删掉报告行即可，不需要在这里再列一遍表名。

**审计事件随报告删除**（#272 对 PRD 的一处订正）：`ReportAuditEvent.report_id` 是对报告
的非空外键，审计行能追溯到主体、属于个人信息。撤回的意义是「把我关于健康数据的东西删掉」，
保留一张能追到我的审计表要与这条请求相反，也要改表结构。留证由撤回那条同意记录承担 ——
它自己写着删掉了多少份报告。见 ADR 0029。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy.orm import Session

from app.data.models import MedicalReport

logger = logging.getLogger(__name__)


def stored_paths(reports: Iterable[MedicalReport]) -> list[Path]:
    """这些报告在磁盘上的原始文件路径。**在删库之前**取，因为删完就查不到了。"""
    return [Path(item.stored_path) for report in reports for item in report.files]


def delete_reports(db: Session, reports: Iterable[MedicalReport]) -> Deletion:
    """发出这些报告的删除（数据库），返回一个**提交之后才删文件**的句柄。

    **不 commit** —— 由调用方在同一事务里提交（撤回同意要把撤回记录和这次删除写进同一
    次提交）。文件要等提交成功之后才删，所以这里只返回句柄，不在事务里面就去动磁盘。
    """
    reports = list(reports)
    paths = stored_paths(reports)
    for report in reports:
        db.delete(report)
    db.flush()
    return Deletion(paths=paths, count=len(reports))


class Deletion:
    """一次已发出、尚未提交的删除。

    `commit(db)` 才是那个分界：事务提交成功之后文件才消失。把它做成一个对象而不是一个
    「提交后再调一次函数」的约定，是因为后者要求每个调用方都记得第二步，而漏掉它的表现
    （数据库说没有、磁盘上还在）恰恰是本模块要保证不会发生的事。
    """

    def __init__(self, *, paths: list[Path], count: int) -> None:
        self._paths = paths
        self.count = count

    @property
    def paths(self) -> list[Path]:
        return list(self._paths)

    def commit(self, db: Session) -> int:
        db.commit()
        self._unlink()
        return self.count

    def _unlink(self) -> None:
        directories = set()
        for path in self._paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                # 数据库已经提交，报告不属于患者了。残留字节没有行指向它，由运维侧的
                # 保留策略清扫；把删除判成失败会让患者反复重试一件已完成的事。
                logger.warning("报告原始文件删除失败（数据库已提交）：%s", path, exc_info=True)
                continue
            directories.add(path.parent)
        # 每份报告一个目录（`REPORT_FILES_DIR/<report_id>/`）。空了就收掉，不留下
        # 一堆空目录给下一次删除去猜。
        for directory in directories:
            with contextlib.suppress(OSError):
                directory.rmdir()
