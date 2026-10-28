"""Consumer upstream settings cannot replace the isolated database or file store."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("key", ["DATABASE_URL", "REPORT_FILES_DIR", "HEALTHFLOW_E2E_RUN_DIR", "CORS_ORIGINS"])
def test_upstream_override_rejects_isolation_settings(key):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", "await import('./frontend/playwright.config.js')"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HEALTHFLOW_E2E_UPSTREAM_ENV": json.dumps({key: "unsafe"})},
    )
    assert result.returncode != 0
    assert "accepts only string-valued upstream settings" in result.stderr


def test_upstream_override_keeps_the_sandbox_database():
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "const {default:c}=await import('./frontend/playwright.config.js');"
            "console.log(JSON.stringify(c.webServer.env));",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "HEALTHFLOW_E2E_UPSTREAM_ENV": json.dumps({"GENESIS_EVIDENCE_API_URL": "http://127.0.0.1:1"}),
        },
    )
    env = json.loads(result.stdout)
    assert env["DATABASE_URL"] == env["HEALTHFLOW_E2E_DATABASE_URL"]
    assert env["REPORT_FILES_DIR"] == env["HEALTHFLOW_E2E_REPORT_FILES_DIR"]
    assert env["GENESIS_EVIDENCE_API_URL"] == "http://127.0.0.1:1"
