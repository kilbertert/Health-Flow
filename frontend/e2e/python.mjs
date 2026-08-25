// 解析 E2E 使用的项目 Python 解释器:
//   1. HEALTHFLOW_E2E_PYTHON 显式指定;
//   2. 仓库根目录 uv 管理的 .venv(`uv sync --extra dev` 的产物);
//   3. 回退到 `uv run --no-sync`,沿用当前环境不做任何同步。
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

export function resolvePython(repoRoot) {
  if (process.env.HEALTHFLOW_E2E_PYTHON) {
    return { command: process.env.HEALTHFLOW_E2E_PYTHON, prefixArgs: [] };
  }
  const venvPython = path.join(
    repoRoot,
    '.venv',
    process.platform === 'win32' ? 'Scripts\\python.exe' : 'bin/python',
  );
  if (fs.existsSync(venvPython)) {
    return { command: venvPython, prefixArgs: [] };
  }
  return {
    command: 'uv',
    prefixArgs: ['run', '--directory', repoRoot, '--no-sync', 'python'],
  };
}
