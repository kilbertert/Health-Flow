"""报告原始材料的类型判定（GLOSSARY.md 的「报告原始材料」）。

「这份原始材料是什么类型、该走哪条抽取路径」此前由**四处平行实现**回答，互不
引用：上传闸门一份后缀白名单、`app/api/report.py` 一张后缀 → MIME 表、
`vision_encoder.parse` 一套按文件名的后缀路由加**第二张** MIME 表（未知后缀默认
落到 ``image/png``），以及把渲染页一律写成字面量 ``image/png`` 的那处。

本模块是**唯一**权威。它只做判定，不落盘、不解析内容：落盘与解析分别留在
`app/api/report.py` 与 `app/service/vision_encoder.py`，两者都调用这里。

**判定优先级**：内容嗅探（magic bytes）优先于后缀。嗅不出类型时回落到后缀 ——
「嗅不出」不等于「不是」，一个截断的 PDF 仍然是 PDF；这时以患者提交的后缀为准，
**不猜**一个别的类型出来。

**后缀与内容不一致**：判定结果按内容，且 ``mismatch`` 为真。上传闸门据此**拒绝**
（患者可见的「格式不对」），而不是放行后在查看原文时才失败。策略是拒绝而非静默
纠正：把一份 PNG 改名成 ``.pdf`` 是患者的一次真实操作，值得一个明确的答复。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: **唯一**后缀 → MIME 表。受理集合与它同源，避免「白名单」和「MIME 表」漂移。
_EXTENSION_KIND: dict[str, str] = {
    ".pdf": "pdf",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".gif": "gif",
    ".bmp": "bmp",
}

#: 上传受理的后缀集合（`app/api/report.py` 的闸门用它，前端由上传策略端点下发）。
ACCEPTED_EXTENSIONS: frozenset[str] = frozenset(_EXTENSION_KIND)

#: 类型 → MIME。同一个类型只在这里写一次。
KIND_MEDIA_TYPE: dict[str, str] = {
    "pdf": "application/pdf",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
}

#: 类型 → 规范后缀（粘贴路径给无后缀的剪贴板 blob 命名时用）。
KIND_EXTENSION: dict[str, str] = {
    "pdf": ".pdf",
    "jpeg": ".jpg",
    "png": ".png",
    "gif": ".gif",
    "bmp": ".bmp",
}

#: 类型 → 给人看的名字（错误信息用；不暴露内部路径）。
KIND_LABEL: dict[str, str] = {
    "pdf": "PDF",
    "jpeg": "JPEG 图片",
    "png": "PNG 图片",
    "gif": "GIF 图片",
    "bmp": "BMP 图片",
}

UNKNOWN_KIND = "unknown"
UNKNOWN_MEDIA_TYPE = "application/octet-stream"

#: 服务端把 PDF 页渲染成图片时用的类型（`PyMuPDF` 的 `tobytes("png")`）。它是
#: **服务端自己的产物**，不是患者提交的后缀 —— 抽取侧原先把这个字面量写死在告诉
#: VLM 的那一行里，同样是「一处类型知识」。渲染器与这个常量必须一致。
RENDERED_PAGE_KIND = "png"
RENDERED_PAGE_MEDIA_TYPE = KIND_MEDIA_TYPE[RENDERED_PAGE_KIND]

#: magic bytes → 类型。顺序有意义：先长后短，避免前缀重叠时误判。
#: `BM` 只有两个字节，是一张 BMP 的必要条件而非充分条件 —— 已知的天花板：
#: 一个以 `BM` 开头的非图片文件会被判成 BMP。代价是它的解析会失败（可恢复），
#: 换成更宽松的判据则要读文件头偏移，收益不抵复杂度。
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)

#: 嗅探只需要文件开头的这么多字节。
SNIFF_PREFIX_BYTES = 16

Source = str  # "content" | "suffix" | "none"


@dataclass(frozen=True)
class ReportMaterial:
    """一份原始材料的类型判定结果。`kind` 是权威取值；判定依据记在 `source`。"""

    kind: str
    media_type: str
    extension: str
    #: 患者提交的后缀所声明的类型，供闸门判断「声明与实际是否一致」。
    declared_kind: str
    source: Source

    @property
    def mismatch(self) -> bool:
        """声明与内容是否矛盾。两端都认得出来、且不相等，才算矛盾。"""
        return (
            self.source == "content"
            and self.declared_kind != UNKNOWN_KIND
            and self.kind != self.declared_kind
        )

    @property
    def is_pdf(self) -> bool:
        return self.kind == "pdf"


def extension_of(filename: str) -> str:
    """按后缀取受理键。`Path.suffix` 已处理「没有后缀」与「以点开头」两种形状。"""
    return Path(filename or "").suffix.casefold()


def declared_kind(filename: str) -> str:
    """患者提交的后缀所声明的类型；不受理的后缀是 ``unknown``。"""
    return _EXTENSION_KIND.get(extension_of(filename), UNKNOWN_KIND)


def sniff_kind(content: bytes) -> str | None:
    """按 magic bytes 判定真实类型。嗅不出来是 ``None``（不等于「不是图片」）。"""
    head = bytes(content[:SNIFF_PREFIX_BYTES])
    for signature, kind in _SIGNATURES:
        if head.startswith(signature):
            return kind
    return None


def resolve(filename: str, content: bytes) -> ReportMaterial:
    """唯一的类型判定入口：内容优先，嗅不出时回落到后缀，两端都没有就是 ``unknown``。"""
    declared = declared_kind(filename)
    sniffed = sniff_kind(content)
    kind = sniffed or declared
    if kind == UNKNOWN_KIND:
        return ReportMaterial(UNKNOWN_KIND, UNKNOWN_MEDIA_TYPE, "", declared, "none")
    source: Source = "content" if sniffed else "suffix"
    return ReportMaterial(
        kind=kind,
        media_type=KIND_MEDIA_TYPE[kind],
        extension=KIND_EXTENSION[kind],
        declared_kind=declared,
        source=source,
    )


def media_type_for_extension(filename: str) -> str:
    """只按后缀取 MIME —— 仅供**没有内容可比**的调用点（如落盘路径）。"""
    kind = declared_kind(filename)
    return KIND_MEDIA_TYPE.get(kind, UNKNOWN_MEDIA_TYPE)


def mismatch_reason(material: ReportMaterial) -> str:
    """声明与内容不符时给患者看的可读原因（用文件名与类型名，不暴露内部路径）。"""
    declared = KIND_LABEL.get(material.declared_kind, material.declared_kind)
    actual = KIND_LABEL.get(material.kind, material.kind)
    return f"文件内容与扩展名不符：扩展名说的是 {declared}，实际是 {actual}"


def page_count(filename: str, content: bytes) -> int | None:
    """这份材料的页数 —— **唯一**计算入口；读不出页数是 ``None``，不是 1。

    「未知」与「共 1 页」是两件事，此前用 1 冒充后者：非 PDF 一律返回 1，
    `fitz` 打不开的 PDF 也在 `except` 里返回 1 —— 于是一个损坏的 PDF 在报告详情
    里显示成「共 1 页」，而同一个 1 又被当作 `page_number` 越界的边界。患者看到的
    不是「读不出页数」，而是一个**看起来很确定**的错值。

    图片（一份材料一页）是**确定**的 1，不是未知；只有 PDF 读不出来才是 ``None``。
    """
    material = resolve(filename, content)
    if material.is_pdf:
        try:
            import fitz

            with fitz.open(stream=content, filetype="pdf") as document:
                return max(1, document.page_count)
        except Exception:
            return None
    if extraction_route(material) == "image":
        return 1
    # 判定不出类型：**不猜**页数。走到这里说明上游闸门没拦住，仍然如实返回未知。
    return None


def extraction_route(material: ReportMaterial) -> str:
    """家族级抽取路由。PDF 的 text/scanned 细分是抽取侧的探针，不在这里。

    这里回答的是「这份材料是 PDF、还是一张图片、还是根本不认识」—— 后者不再由
    `vision_encoder.parse` 按文件名自己猜。
    """
    if material.kind == "pdf":
        return "pdf"
    if material.kind in {"jpeg", "png", "gif", "bmp"}:
        return "image"
    return UNKNOWN_KIND
