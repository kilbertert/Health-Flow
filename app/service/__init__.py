"""Service layer - Business logic services.

**刻意留空**（不再是 `MedicalRAGService` / `VisionEncoderService` 等的再导出）。

那两个类不 import 任何东西就能拿到，但代价是 `import app.service.<任何子模块>` 都会
先把 `medical_rag` 与 `vision_encoder` 拉起来；而 `vision_encoder` 反过来 import
`app.schema.report`，`app.schema.report` 又 import `app.schema.evidence`。于是
**契约层一旦要 import 服务层的任何模块就成环** —— 实测报
`ImportError: cannot import name 'MetricRecord' from partially initialized module`。

这不是理论风险：它挡住的是「出域契约的词表从唯一的词表之家派生」这件事（#183）。
契约在最底层，它要读词表，而词表所在的包不能有 eager 的副作用。

全仓库没有任何一处 `from app.service import <名字>`（grep 为 0），所以删掉再导出不
影响任何调用点：`from app.service.medical_rag import MedicalRAGService` 一直是可行的，
现在仍然可行。真正需要在 `__init__` 里再导出的场合出现时，加回来之前先确认它不会把
`vision_encoder` 拖进契约层的导入路径。
"""

__all__: list[str] = []
