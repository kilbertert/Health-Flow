// 解析 E2E 使用的项目 Python 解释器:
//   1. HEALTHFLOW_E2E_PYTHON 显式指定(优先,不检查内容——指定它的人负责);
//   2. 仓库根目录 uv 管理的 .venv(`uv sync --extra dev` 的产物),且**能导入 uvicorn**;
//   3. 都没有:报错退出。
//
// 第 3 条曾经是 `uv run --directory <repoRoot> --no-sync python`。那是个陷阱:
// uv 的项目发现与 --directory 不是一回事,它认定「这个项目还没有环境」,于是**在
// checkout 里新建了一个空的 .venv**(只有 pip/setuptools),`--no-sync` 又不让它同步
// 依赖,于是 uvicorn 起不来。更糟的是**建目录这件事本身**:下一次运行时第 2 条会选中
// 那个空环境,失败从「uv 可自愈」变成「稳定复现」。这是 #114;同一条缝隙的另一端是 #67。
//
// 所以现在的规矩是:**解析器永不创建目录、永不同步依赖**。它要么在一个已经可用的
// 解释器上落地,要么把「用哪个解释器」变成人的显式输入。发现 canonical checkout 的环境
// 不在射程内——那需要猜测,而猜错又是一个假环境。
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const UVICORN_PROBE = 'import importlib.util; raise SystemExit(0 if importlib.util.find_spec("uvicorn") else 1)';

// 真正跑 e2e 的是 uvicorn,所以判据就是「这个解释器能不能导入 uvicorn」。
// 只看 .venv/bin/python 是否存在是不够的:空的 .venv 也有这个文件。
// 探针继承当前环境(server.mjs 启动服务时同样继承),所以它问的正是
// 「照现在这么做,服务起得来吗」。
function hasUvicorn(python) {
  const probe = spawnSync(python, ['-c', UVICORN_PROBE], { stdio: 'ignore' });
  return probe.status === 0;
}

export function resolvePython(repoRoot) {
  if (process.env.HEALTHFLOW_E2E_PYTHON) {
    return { command: process.env.HEALTHFLOW_E2E_PYTHON, prefixArgs: [] };
  }
  const venvPython = path.join(
    repoRoot,
    '.venv',
    process.platform === 'win32' ? 'Scripts\\python.exe' : 'bin/python',
  );
  if (fs.existsSync(venvPython) && hasUvicorn(venvPython)) {
    return { command: venvPython, prefixArgs: [] };
  }
  throw new Error(buildMissingEnvironmentMessage(repoRoot, fs.existsSync(venvPython)));
}

function buildMissingEnvironmentMessage(repoRoot, venvPythonExists) {
  const lines = [
    `e2e/python.mjs: 没有可用的项目 Python 环境(没有找到能导入 uvicorn 的解释器)。`,
    '',
  ];
  if (venvPythonExists) {
    lines.push(
      `  ${path.join(repoRoot, '.venv')} 存在,但里面的 python 导入不了 uvicorn——`,
      '  这通常是上一次运行留下的空环境,不是项目环境。',
      '',
    );
  }
  lines.push(
    '  在仓库根目录同步依赖(会创建 .venv 并装上 uvicorn):',
    `    uv sync --extra dev            # 在 ${repoRoot} 执行`,
    '',
    '  共享环境也可以:用 HEALTHFLOW_E2E_PYTHON 指定那个环境里的 python,例如:',
    '    HEALTHFLOW_E2E_PYTHON=<canonical checkout>/.venv/bin/python npm run test:e2e',
    '',
    '  worktree 里跑 e2e 时,第二种是通常的做法(canonical checkout 的 .venv 已就绪,',
    '  worktree 里没有也不必有)。',
  );
  return lines.join('\n');
}
