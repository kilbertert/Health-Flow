"""前端的受理规则消费（#171）：策略归一、粘贴分类、粘贴命名。

驱动的是**真实前端模块**（`frontend/src/uploadPolicy.js` 与
`frontend/src/pasteImage.js`），经 esbuild 转译后由 node 导入 —— 与浏览器里渲染
用的是同一份代码（先例：`tests/test_frontend_value_unusable.py`）。

三条回归：

1. **webp/heic 的结论只有一处。** 此前 `pasteMimeFromFileName` 认得它们、
   `UNSUPPORTED_PASTE_IMAGE_TYPES` 又在入口拒掉，同一份剪贴内容在两个函数里得到
   相反态度。现在受理判据是扩展名是否在服务端下发的集合里，一个地方回答。
2. **粘贴保留源文件名。** 词条「报告原始材料」承诺保留文件名，此前粘贴路径把它
   换成 `粘贴-<时间戳>`。有源名就用源名；没有（items / data:image 两条路径拿不到
   名字）才生成一个带序号的。
3. **策略归一不猜测。** 服务端多下发一个键、少下发一个键、下发垃圾值，前端都只
   取它认识的四个键并补齐默认 —— 一次请求失败不该挡住上传。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "frontend"
NODE = shutil.which("node")

_DRIVER = """
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const outDir = process.env.HEALTHFLOW_TEST_OUTDIR;
const entry = path.join(outDir, 'entry.mjs');
fs.writeFileSync(
  entry,
  [
    "export * from 'healthflow-upload-policy';",
    "export * from 'healthflow-paste-image';",
  ].join('\\n'),
);
await build({
  entryPoints: [entry],
  outfile: path.join(outDir, 'bundle.mjs'),
  bundle: true,
  // 前端依赖从 frontend/node_modules 解析；本地相对导入打进同一份产物。
  packages: 'external',
  absWorkingDir: process.env.HEALTHFLOW_TEST_FRONTEND,
  format: 'esm',
  jsx: 'automatic',
  logLevel: 'silent',
  alias: {
    'healthflow-upload-policy': process.env.HEALTHFLOW_POLICY_MODULE,
    'healthflow-paste-image': process.env.HEALTHFLOW_PASTE_MODULE,
  },
});
const mod = await import(pathToFileURL(path.join(outDir, 'bundle.mjs')).href);

const input = JSON.parse(process.env.HEALTHFLOW_TEST_CASES);
const result = {
  normalized: input.policies.map((policy) => mod.normalizeUploadPolicy(policy)),
  accepts: input.accepts.map((item) => mod.acceptAttribute(item)),
  classified: input.classifications.map(({ candidate, accepted }) => {
    const out = mod.classifyPaste(candidate, accepted);
    return { supported: out.supported, extension: out.extension, mime: out.mime };
  }),
  names: input.names.map(({ candidate, now, sequence }) =>
    mod.pastedFileName(candidate, { now, sequence }),
  ),
  messages: input.messages.map(({ candidate, accepted }) =>
    mod.unsupportedPasteMessage(candidate, accepted),
  ),
  // `data:image` 的提取在模块里，不在组件里 —— 这里连喂两次并**先污染一次
  // 共享正则的 lastIndex**，确认它不被模块级状态影响。
  dataImages: (() => {
    mod.DATA_IMAGE_RE.exec('<img src="data:image/png;base64,ZZZZ">');
    return input.dataImages.map((html) => mod.dataImageCandidates(html));
  })(),
};
fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify(result));
"""

SERVER_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp"]

POLICIES = [
    # 服务端下发什么就用什么。
    {
        "raw": {"accepted_extensions": [".pdf", ".png"], "max_files": 3, "max_file_bytes": 100, "max_total_bytes": 200},
        "expect": {"accepted_extensions": [".pdf", ".png"], "max_files": 3},
    },
    # 拿不到策略（请求失败 / null）：回落到内建默认，**不挡住上传**。
    {"raw": None, "expect": {"accepted_extensions": SERVER_EXTENSIONS, "max_files": 20}},
    # 服务端多下发的键不进前端对象 —— 认识的面是固定的。
    {
        "raw": {
            "accepted_extensions": [".png"],
            "max_files": 5,
            "report_files_dir": "/srv/x",
            "secret": 1,
        },
        "expect": {
            "accepted_extensions": [".png"],
            "max_files": 5,
            "keys": ["accepted_extensions", "max_files", "max_file_bytes", "max_total_bytes"],
        },
    },
    # 垃圾值：不猜，回落默认。
    {
        "raw": {"max_files": 0, "max_file_bytes": -5, "max_total_bytes": "x"},
        "expect": {"max_files": 20, "max_file_bytes": 20971520},
    },
    # 空扩展名清单同样回落（不能变成一个「什么都不受理」的策略）。
    {"raw": {"accepted_extensions": []}, "expect": {"accepted_extensions": SERVER_EXTENSIONS}},
    # 非字符串项被丢掉，留空则回落。
    {"raw": {"accepted_extensions": [".png", 7, "png"]}, "expect": {"accepted_extensions": [".png"]}},
]


def _classify(mime="", name="", accepted=SERVER_EXTENSIONS):
    return {"candidate": {"mime": mime, "name": name}, "accepted": accepted}


CLASSIFICATIONS = [
    # 受理的图片类型：入口与分类结论一致。
    (_classify(mime="image/png"), True, "png", "image/png"),
    (_classify(mime="image/jpeg"), True, "jpg", "image/jpeg"),
    (_classify(mime="image/gif"), True, "gif", "image/gif"),
    (_classify(mime="image/bmp"), True, "bmp", "image/bmp"),
    # **webp / heic：不受理，而且是在这里判的**（不再有第二个函数认得它们）。
    (_classify(mime="image/webp"), False, "webp", ""),
    (_classify(name="copied.heic"), False, "heic", ""),
    (_classify(name="copied.heif"), False, "heif", ""),
    # 只有文件名时按后缀判。
    (_classify(name="报告.PNG"), True, "png", "image/png"),
    (_classify(name="报告.pdf"), True, "pdf", "application/pdf"),
    (_classify(name="扫描件.tiff"), False, "tiff", ""),
    # MIME 比文件名可信：名字说 pdf、内容说 png，按内容。
    (_classify(mime="image/png", name="假装.pdf"), True, "png", "image/png"),
    # 两个线索都没有：不知道它是什么，不猜。
    (_classify(), False, "", ""),
    # 服务端把受理集合收紧后，前端立刻跟着收紧（同一个判据）。
    (
        {
            "candidate": {"mime": "image/gif", "name": ""},
            "accepted": [".pdf", ".png"],
        },
        False,
        "gif",
        "",
    ),
]


@pytest.mark.skipif(NODE is None, reason="需要 node 才能驱动前端模块")
@pytest.mark.skipif(
    not (FRONTEND / "node_modules" / "esbuild").is_dir(),
    reason="需要 frontend/node_modules（esbuild）才能转译；先在前端目录 npm install",
)
def test_frontend_consumes_the_upload_policy_contract():
    import os

    work = FRONTEND / "node_modules" / f".tmp-upload-policy-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        driver = work / "driver.mjs"
        driver.write_text(_DRIVER, encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(driver)],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "HEALTHFLOW_TEST_OUTDIR": str(work),
                "HEALTHFLOW_TEST_FRONTEND": str(FRONTEND),
                "HEALTHFLOW_POLICY_MODULE": str(FRONTEND / "src" / "uploadPolicy.js"),
                "HEALTHFLOW_PASTE_MODULE": str(FRONTEND / "src" / "pasteImage.js"),
                "HEALTHFLOW_TEST_CASES": json.dumps(
                    {
                        "policies": [case["raw"] for case in POLICIES],
                        "accepts": [
                            {"accepted_extensions": [".pdf", ".png"]},
                            {"accepted_extensions": []},
                            None,
                        ],
                        "classifications": [case[0] for case in CLASSIFICATIONS],
                        "names": [
                            {"candidate": {"sourceName": "报告.png"}, "now": 1000, "sequence": 1},
                            {"candidate": {"sourceName": ""}, "now": 1000, "sequence": 2},
                            {"candidate": {}, "now": 1000, "sequence": 3},
                        ],
                        "messages": [
                            {"candidate": {"extension": "webp"}, "accepted": SERVER_EXTENSIONS},
                        ],
                        "dataImages": [
                            '<div><img src="data:image/png;base64,AAAA"></div>',
                            '<div><img src="data:image/jpeg;base64,BBBB"></div>',
                            '<div>没有内嵌图片的普通 HTML</div>',
                        ],
                    }
                ),
            },
            cwd=FRONTEND,
            timeout=240,
        )
        assert proc.returncode == 0, f"驱动失败:\n{proc.stdout}\n{proc.stderr}"
        result = json.loads((work / "result.json").read_text(encoding="utf-8"))

        for case, got in zip(POLICIES, result["normalized"], strict=True):
            for key, expected in case["expect"].items():
                if key == "keys":
                    assert sorted(got) == sorted(expected), f"{case['raw']}: 键集合 {sorted(got)}"
                else:
                    assert got[key] == expected, f"{case['raw']}: {key} 期望 {expected}，得到 {got[key]}"

        assert result["accepts"][0] == ".pdf,.png"
        assert result["accepts"][1] == ".pdf,.jpg,.jpeg,.png,.gif,.bmp"
        assert result["accepts"][2] == ".pdf,.jpg,.jpeg,.png,.gif,.bmp"

        for case, got in zip(CLASSIFICATIONS, result["classified"], strict=True):
            assert got["supported"] is case[1], f"{case[0]}: supported 期望 {case[1]}"
            assert got["extension"] == case[2], f"{case[0]}: extension 期望 {case[2]}，得到 {got['extension']}"
            if case[1]:
                assert got["mime"] == case[3], f"{case[0]}: mime 期望 {case[3]}，得到 {got['mime']}"

        # 三条：两次连续解析都要找得到（共享正则的 lastIndex 不能留下状态），
        # 第三次是没有内嵌图片的 HTML —— 不许凭空造一张。
        assert result["dataImages"][0] == [{"mime": "image/png", "base64": "AAAA"}], result["dataImages"][0]
        assert result["dataImages"][1] == [{"mime": "image/jpeg", "base64": "BBBB"}], result["dataImages"][1]
        assert result["dataImages"][2] == []

        assert result["names"][0] == "报告.png", "有源文件名就必须保留它"
        assert result["names"][1] == "粘贴-1000-2.png"
        assert result["names"][2] == "粘贴-1000-3.png"
        assert "WEBP" in result["messages"][0] and "WebP" not in result["messages"][0]
        assert "PNG" in result["messages"][0], "提示要说清支持哪些格式"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_builtin_defaults_match_the_server_acceptance_set():
    """内建默认值不许与「什么算受理类型」漂移（一次请求失败时会用到它们）。

    静态守卫：默认值只在拿不到服务端策略时生效，行为测试够不到「服务端改了扩展名、
    前端默认值没跟着改」这条 —— 那时两者都自洽，只有**这一对**不一致。
    """
    from app.service.report_material import ACCEPTED_EXTENSIONS

    source = (FRONTEND / "src" / "uploadPolicy.js").read_text(encoding="utf-8")
    for extension in ACCEPTED_EXTENSIONS:
        assert f"'{extension}'" in source, f"内建默认值缺少 {extension}"
