#!/usr/bin/env bash
#
# deploy-36.sh — 把本仓某个 commit 的产物部署到 36（yidong-36，service host）。
#
# USAGE:
#   deploy/deploy-36.sh --commit <sha> [--dry-run]
#   deploy/deploy-36.sh --rollback-to <sha> [--dry-run]
#
#   --commit <sha>       部署该 commit（通常由 cd.yml 传 $GITHUB_SHA）
#   --rollback-to <sha>  用同一套流程重部署一个已知良好的 commit
#   --dry-run            打包并打印将要执行的远端命令；**不碰 36**
#
# Why this script exists: the path from "merged to main" to "36 is running it" was
# 100% manual — `ops/service-host/README.md` prose plus one `artifacts/qa/` record
# per deployment. On 2026-10-09 that had drifted to 25 commits behind main (11 of
# them touching `app/`), with no revision marker on the host and no git checkout
# there, so "which revision is live?" could only be answered by inferring from the
# shape of the API. This script and `cd.yml` make that path automatic, and this
# script is deliberately the *only* path — the automated one and the human
# emergency rollback share it so they cannot drift apart.
#
# Three things it deliberately does NOT do:
#   * business acceptance — that needs a session minted by the mall entry page,
#     which this host cannot create. This proves technical health only. See
#     `artifacts/qa/20261006-frontend-deploy.md` for that record's shape.
#   * invent its own host transport — it drives `dev-host`, which already enforces
#     the policy gate (identity assertion + `--artifact-sha256`). A hand-rolled
#     ssh+scp here would bypass "changes reach a service host only as an
#     identified artifact".
#   * rewrite the unit file — see the drift note in step 6.
#
# Exit codes: 0 ok / 1 deploy failed / 64 usage / 65 environment / 77 refused /
# 124 timeout. 65-vs-1 is deliberate: "the environment is wrong" (dev-host cannot
# parse TOML, the alias resolves elsewhere) sends the reader somewhere completely
# different from "the deployment failed", and conflating them wastes the first
# ten minutes of every incident.
set -euo pipefail

PROG=${0##*/}

HOST_TARGET=36
APP_ROOT=/opt/health-flow
SERVICE=health-flow
REMOTE_TARBALL_BASE=/tmp/deploy-36
BACKUP_DIR=/var/backups/health-flow
# The service binds 0.0.0.0:10007 on the host (measured). The repository unit file
# says 127.0.0.1, so the two disagree — see the drift note in step 6 for why this
# script reports that instead of "fixing" it.
HEALTH=http://127.0.0.1:10007/ready
REMOTE_TIMEOUT=600

# Test seams. `tests/test_cd_deploy_scripts.py` runs the *remote block* locally by
# printing it with `--dry-run` and pointing these at a scratch directory, which is
# how the idempotence and recovery branches get exercised without touching 36.
# `cd.yml` deliberately sets none of them: the production path takes the literals
# above, and an override that reached production would redirect a deploy.
HOST_TARGET=${DEPLOY_HOST_TARGET:-$HOST_TARGET}
APP_ROOT=${DEPLOY_APP_ROOT:-$APP_ROOT}
SERVICE=${DEPLOY_SERVICE:-$SERVICE}
REMOTE_TARBALL_BASE=${DEPLOY_REMOTE_TARBALL_BASE:-$REMOTE_TARBALL_BASE}
BACKUP_DIR=${DEPLOY_BACKUP_DIR:-$BACKUP_DIR}
HEALTH=${DEPLOY_HEALTH_URL:-$HEALTH}

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }
die()   { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
refuse(){ printf '%s: %s\n' "$PROG" "$*" >&2; exit 77; }
usage_err() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 64; }
step()  { printf '\n== %s ==\n' "$*"; }
info()  { printf '   %s\n' "$*"; }

COMMIT=""
ROLLBACK_TO=""
DRY_RUN=0
while [ $# -gt 0 ]; do
  case $1 in
    --commit)      [ $# -ge 2 ] || usage_err "--commit 需要参数"; COMMIT=$2; shift 2 ;;
    --rollback-to) [ $# -ge 2 ] || usage_err "--rollback-to 需要参数"; ROLLBACK_TO=$2; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) usage_err "未知参数：$1（-h 看用法）" ;;
  esac
done
if [ -n "$COMMIT" ] && [ -n "$ROLLBACK_TO" ]; then
  usage_err "--commit 与 --rollback-to 只能给一个"
fi
TARGET_COMMIT=${COMMIT:-$ROLLBACK_TO}
[ -n "$TARGET_COMMIT" ] || usage_err "必须给 --commit <sha> 或 --rollback-to <sha>"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

STAGE=$(mktemp -d)

# The frontend is built from the target commit's tree via a scratch worktree, and a
# worktree is a **registration in the repository**, not just a directory. If the run
# ends without unregistering it, the registration outlives the directory and every
# later `git worktree list` carries a dangling entry — an audit violation, and noise
# that buries a real one. Measured: the first version of this script leaked 55 of
# them across its own test runs. So registration is paired with this trap from the
# moment it can happen.
FRONTEND_WT="$STAGE/src"
cleanup_worktree() {
  if [ -e "$FRONTEND_WT/.git" ]; then
    git worktree remove --force "$FRONTEND_WT" >/dev/null 2>&1 || true
  fi
  git worktree prune >/dev/null 2>&1 || true
}

# `--dry-run` keeps its scratch tree: it prints the artifact path so the caller can
# hash the very payload whose identity it declares, and a test that consumes the
# seam cannot do that if the seam deletes its own output on exit. It still prunes the
# worktree registration — that is metadata in the repository, not the scratch tree.
if [ "${DRY_RUN:-0}" = "1" ]; then
  trap 'cleanup_worktree; printf "%s\n" "（--dry-run：暂存目录保留在 $STAGE）" >&2' EXIT
else
  trap 'cleanup_worktree; rm -rf "$STAGE"' EXIT
fi

# ── 0. 环境前置检查 ─────────────────────────────────────────────────────────
#
# `dev-host` parses the host registry with `python3 -c` + `tomllib`, i.e. it needs
# Python 3.11+. The self-hosted runner's PATH is
# `/home/claude/.local/bin:/usr/local/bin:/usr/bin:/bin`, so a bare `python3` is
# /usr/bin/python3 (3.10) and raises `ModuleNotFoundError: tomllib`. Measured. That
# is an environment fault, not a deployment failure, so it gets its own exit code
# (65) rather than 1.
step "环境前置检查"
command -v dev-host >/dev/null 2>&1 \
  || { printf '%s\n' "dev-host 不在 PATH。runner 的 PATH 应含 /home/claude/.local/bin" >&2; exit 65; }
if ! dev-host show "$HOST_TARGET" >/dev/null 2>&1; then
  cat >&2 <<'EOF'
环境前置失败：`dev-host show 36` 跑不通。

最可能的原因：python3 解析到 /usr/bin/python3 (3.10)，没有 tomllib。
修法（本机已验证）——把 miniconda 前置到 PATH 再调用本脚本：

  PATH=/home/claude/miniconda3/bin:$PATH deploy/deploy-36.sh ...
EOF
  exit 65
fi
host_role=$(dev-host show "$HOST_TARGET" | awk '$1=="role"{print $2}')
host_status=$(dev-host show "$HOST_TARGET" | awk '$1=="status"{print $2}')
[ "$host_role" = "service-host" ] || refuse "36 的角色是 '$host_role'，期望 service-host；停下"
[ "$host_status" = "active" ]     || refuse "36 的状态是 '$host_status'，不是 active；停下"
info "dev-host 可用（$(command -v dev-host)）；36 角色=$host_role 状态=$host_status"

# ── 1. 解析目标 commit ──────────────────────────────────────────────────────
step "解析目标 commit"
git cat-file -e "${TARGET_COMMIT}^{commit}" 2>/dev/null \
  || die "仓库里没有 commit $TARGET_COMMIT（自托管 runner 需要完整 main 历史）"
FULL_SHA=$(git rev-parse "$TARGET_COMMIT^{commit}")
SHORT_SHA=$(git rev-parse --short=12 "$FULL_SHA")
# FULL_SHA 会被插进远端命令与标记文件。git rev-parse 的契约是产出 hex，但这里显式
# 断言，把这条不变量变成脚本自己维护的，而不是依赖外部工具当前的行为。
case $FULL_SHA in *[!0-9a-f]*) die "commit 解析出了非 hex 的 sha：$FULL_SHA" ;; esac
info "commit=$FULL_SHA (${SHORT_SHA})"
# Byte-truncate with the shell, not with `cut -c`. `cut` counts characters by
# locale rules that are not consistently UTF-8 here — measured: a subject of 87
# bytes came back as 71 bytes ending mid-character, and this line is printed by
# `--dry-run`, which is a *test seam*. A seam that emits invalid UTF-8 cannot be
# read back by the test that consumes it, so the truncation stays ASCII-safe.
subject=$(git log -1 --format=%s "$FULL_SHA")
info "subject=${subject:0:70}"

# ── 2. 本机构建产物 ─────────────────────────────────────────────────────────
#
# The host has no node and no uv (measured), and `/opt/health-flow` is not a git
# checkout — so the artifact is built here and transferred, never built there.
# That matches what `ops/service-host/README.md` already says about the frontend.
step "构建产物"
BUILD="$STAGE/build"
mkdir -p "$BUILD"

info "wheel"
( cd "$REPO_ROOT" && uv build --wheel -o "$BUILD/pkg" >/dev/null )
WHEEL=$(ls "$BUILD/pkg"/*.whl)
[ -f "$WHEEL" ] || die "wheel 构建失败"

# The frontend build must come from the target commit's tree, so check out that
# tree into a scratch worktree rather than building whatever is on disk.
# (`FRONTEND_WT` itself is defined above, next to the trap that unregisters it.)
info "前端（从 $SHORT_SHA 的树构建）"
git worktree add --detach --quiet "$FRONTEND_WT" "$FULL_SHA"
( cd "$FRONTEND_WT/frontend" && npm ci --silent && npm run build >/dev/null )
FRONTEND_DIST="$FRONTEND_WT/frontend/dist"
[ -f "$FRONTEND_DIST/index.html" ] || die "前端构建失败：没有 index.html"

# Per-file manifest of the frontend tree. This is the long-lived version identity:
# an archive hash is only reproducible on the same untouched tree (tar records
# mtimes), so the archive hash proves *this transfer* was not altered, while this
# manifest proves *the content* is this revision. Both are used, for different
# questions. Prior art and the trap ("diff with no output is also what a wrong
# command produces"): artifacts/qa/20261006-frontend-deploy.md §2.
( cd "$FRONTEND_DIST" && find . -type f | LC_ALL=C sort | xargs sha256sum | sed 's|  \./|  |' ) > "$BUILD/MANIFEST.sha256"
MANIFEST_LINES=$(wc -l < "$BUILD/MANIFEST.sha256")
[ "$MANIFEST_LINES" -gt 0 ] || die "MANIFEST.sha256 是空的"

# The bundle index.html actually loads. This is what proves the *live* site is
# serving this build — the one assertion that cannot be satisfied by a file merely
# existing on disk.
LIVE_ASSET=$(sed -n 's|.*src="/\(assets/[^"]*\.js\)".*|\1|p' "$FRONTEND_DIST/index.html" | head -1)
[ -n "$LIVE_ASSET" ] || die "index.html 里找不到入口 bundle"
LIVE_ASSET_SHA=$(sha256sum "$FRONTEND_DIST/$LIVE_ASSET" | awk '{print $1}')
info "入口 bundle=$LIVE_ASSET sha=${LIVE_ASSET_SHA:0:16}…"
info "前端文件数=$MANIFEST_LINES"

printf '%s\n' "$FULL_SHA" > "$BUILD/REVISION"

# Stage the frontend under the same root as the wheel and the manifest, so one tar
# carries all three and the remote unpack is a single extract with no assumptions
# about the archive's internal layout.
mkdir -p "$BUILD/frontend"
cp -a "$FRONTEND_DIST/." "$BUILD/frontend/"

# The tarball's own sha256 is what `dev-host cp --artifact-sha256` asserts at
# transfer time — the policy gate for a service host. It is computed here, from
# the bytes that will actually be uploaded.
ARTIFACT="$STAGE/deploy-36-$SHORT_SHA.tar.gz"
tar czf "$ARTIFACT" -C "$BUILD" .
ARTIFACT_SHA=$(sha256sum "$ARTIFACT" | awk '{print $1}')
ARTIFACT_BYTES=$(stat -c %s "$ARTIFACT")
info "产物 $(basename "$ARTIFACT") sha256=$ARTIFACT_SHA （$ARTIFACT_BYTES 字节）"

REMOTE_TARBALL="$REMOTE_TARBALL_BASE-$SHORT_SHA.tar.gz"

# Values the remote block is generated with. Defined here, before the heredoc, so
# they are substituted in as literals: the block then runs under `set -u` without
# inheriting anything from the caller, and `--dry-run` prints the exact bytes that
# will execute rather than a template someone has to read in their head.
TARGET_SHA=$FULL_SHA
STAGE_DIR="$REMOTE_TARBALL_BASE-$SHORT_SHA"
MARKER="$APP_ROOT/deployed-revision"

# ── 3. 远端命令 ─────────────────────────────────────────────────────────────
#
# Two structural decisions, both from failures that were actually hit in AI-Ops'
# sibling script:
#
#   * **Every mutating step accumulates a status instead of exiting.** The remote
#     block runs under `set -e`; if any one step exits early, control never reaches
#     the self-check or the rollback, and production is left half-updated with the
#     local side reporting "state unknown".
#   * **`systemctl restart` returning 0 does not mean the service came up** — it
#     forks and returns. The real judgement is the self-check below.
# NOTE: this block's comments carry no backticks on purpose. Bash still runs command
# substitution inside an unquoted heredoc body, so a backtick in prose starts one —
# measured: eight prose backticks produced eight `command not found` lines and one
# aborted command substitution, and `bash -n` reported none of it.
read -r -d '' REMOTE_CMD <<EOF || true
set -u
TS=\$(date +%Y%m%d-%H%M%S)
BACKUP="$BACKUP_DIR/frontend-\$TS.tar.gz"

# ---- 幂等：已经是这一版就什么都不做 ----
#
# Compare full 40-character shas. AI-Ops hit the opposite: its health endpoint
# reported a 12-char sha while the target was 40, so string inequality made every
# idempotent re-run look stale and redeployed production. The marker is written by
# this script, so both sides are the same width by construction — and the test
# asserts that width rather than trusting it.
prev=\$(cat "$MARKER" 2>/dev/null || echo "")
if [ "\$prev" = "$TARGET_SHA" ]; then
  echo "state=no-change"
  echo "revision=$TARGET_SHA"
  echo "note=已是该修订，未做任何写入、未重启服务"
  exit 0
fi

mutate_rc=0
step_failed() { echo "mutate-failed=\$1"; mutate_rc=1; }

# ---- 回滚点先于写入 ----
mkdir -p "$BACKUP_DIR" || step_failed "backup-mkdir"
if [ -d "$APP_ROOT/frontend" ]; then
  tar czf "\$BACKUP" -C "$APP_ROOT/frontend" . || step_failed "backup-frontend"
fi
echo "backup=\$BACKUP"

rm -rf "$STAGE_DIR" && mkdir -p "$STAGE_DIR" || step_failed "prepare-stage"
tar xzf "$REMOTE_TARBALL" -C "$STAGE_DIR" || step_failed "extract"

# ---- 内容一致性：解出来的前端必须逐字节等于本机构建输出 ----
if [ "\$mutate_rc" = "0" ]; then
  # sha256sum -c on the manifest proves the shipped tree equals the built tree.
  # The manifest itself is inside the tarball, and the tarball's hash was asserted
  # at transfer time — so this is not circular.
  if ( cd "$STAGE_DIR/frontend" && sha256sum -c --quiet "$STAGE_DIR/MANIFEST.sha256" ) 2>/dev/null; then
    echo "manifest=ok"
  else
    step_failed "manifest-mismatch"
  fi
fi

# ---- 换前端：整目录原子替换，不是解包覆盖 ----
#
# The deployment record caught the real version of this bug once already: extracting
# over the live directory preserves files the new index.html no longer references,
# so 'assets/' ends up holding more than one build. Swapping directories makes the
# tree exactly one build, always.
if [ "\$mutate_rc" = "0" ]; then
  rm -rf "$APP_ROOT/frontend.new" "$APP_ROOT/frontend.old" || step_failed "prepare-swap"
  cp -a "$STAGE_DIR/frontend" "$APP_ROOT/frontend.new" || step_failed "stage-frontend"
  chown -R health-flow:health-flow "$APP_ROOT/frontend.new" || step_failed "chown-frontend"
  mv "$APP_ROOT/frontend" "$APP_ROOT/frontend.old" || step_failed "swap-out"
  mv "$APP_ROOT/frontend.new" "$APP_ROOT/frontend" || step_failed "swap-in"
fi

# ---- 装后端 ----
#
# The wheel is 'py3-none-any' and the host venv is python 3.12 (measured), so the
# interpreter used to build it here does not matter for compatibility.
# '--no-deps' is deliberate: dependencies change only with pyproject/uv.lock, and
# resolving them silently during a code deploy would turn a small change into an
# unbounded one. 'pip check' afterwards catches the case where that assumption
# stopped holding.
if [ "\$mutate_rc" = "0" ]; then
  runuser -u health-flow -- "$APP_ROOT/.venv/bin/pip" install \
      --quiet --no-deps --force-reinstall "$STAGE_DIR/$(basename "$WHEEL")" \
    || step_failed "pip-install"
  chown -R health-flow:health-flow "$STAGE_DIR" || step_failed "chown-stage"
fi

# ---- 写入修订标记 ----
#
# This is the machine-readable answer to "which revision is live". Before it
# existed the question could only be answered by probing the API for a capability
# and reasoning about which commit introduced it — which is how the 25-commit drift
# was found, and is not a thing to rely on.
if [ "\$mutate_rc" = "0" ]; then
  printf '%s\n' "$TARGET_SHA" > "$MARKER.tmp" && mv "$MARKER.tmp" "$MARKER" \
    || step_failed "write-marker"
  chown health-flow:health-flow "$MARKER" || step_failed "chown-marker"
fi

# ---- 漂移报告（只报告，不修复）----
#
# The repository unit file says '--host 127.0.0.1'; the deployed one says
# '--host 0.0.0.0' and binds the host's public interface. The policy is explicit
# that changing an exposure is decided from *observed traffic*, not from which
# ports are listening — and this entry point is a documented, actually-used one.
# Silently rewriting the unit would cut the entry point, which is not a decision a
# deploy script gets to make. So: report the divergence and stop there.
unit_host=\$(systemctl cat "$SERVICE" 2>/dev/null | sed -n 's/.*--host \([0-9.]*\) .*/\1/p' | head -1)
case "\$unit_host" in
  127.0.0.1) echo "drift=unit-host-matches-repo" ;;
  0.0.0.0)   echo "drift=unit-binds-0.0.0.0-repo-declares-127.0.0.1（保持线上现状，收敛另开票）" ;;
  "")        echo "drift=unit-host-unknown" ;;
  *)         echo "drift=unit-host-\$unit_host" ;;
esac

# ---- 重启 ----
if systemctl restart "$SERVICE"; then :; else echo "restart-rc=nonzero"; fi
sleep 5

# ---- 自检 ----
#
# Two judgements the local side can independently confirm:
#   * the service is active and /ready reports the expected shape;
#   * the entry point is actually serving the bundle this build produced.
# Everything here is machine-answerable; nothing needs a session.
ready_field() {
  curl -s --max-time 8 "$HEALTH" 2>/dev/null \
    | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get(sys.argv[1],""))
except Exception:
    print("")' "\$1" 2>/dev/null || true
}
# '|| true' on the command substitution above is load-bearing: under 'set -e' a
# timed-out curl returns nonzero and would abort *every* normal deployment. AI-Ops
# hit exactly this. Let the judgement below speak, not the timeout.
live_asset_sha() {
  # Derived from $HEALTH rather than hard-coded, so the test seam that redirects
  # the health endpoint also redirects this — otherwise the dry-run test would
  # read the live site and the assertion would be about the wrong thing.
  curl -s --max-time 20 "${HEALTH%/ready}/$LIVE_ASSET" 2>/dev/null \
    | sha256sum | awk '{print \$1}' || true
}

check_ok() {
  [ "\$mutate_rc" = "0" ] || return 1
  [ "\$(systemctl is-active $SERVICE)" = "active" ] || return 1
  [ "\$(ready_field status)" = "ready" ] || return 1
  [ "\$(ready_field report_provider)" = "configured" ] || return 1
  [ "\$(ready_field account_auth)" = "required" ] || return 1
  [ "\$(live_asset_sha)" = "$LIVE_ASSET_SHA" ] || return 1
  return 0
}

if check_ok; then
  echo "selfcheck=passed"
  echo "live-asset-sha=$LIVE_ASSET_SHA"
  echo "ready-status=\$(ready_field status)"
  rm -rf "$APP_ROOT/frontend.old" "$STAGE_DIR" 2>/dev/null || true
  exit 0
fi

# ---- 恢复 ----
echo "selfcheck=failed"
echo "ready-status=\$(ready_field status)"
echo "report_provider=\$(ready_field report_provider)"
echo "account_auth=\$(ready_field account_auth)"
echo "live-asset-sha=\$(live_asset_sha)"
if [ -f "\$BACKUP" ]; then
  rm -rf "$APP_ROOT/frontend" && mkdir -p "$APP_ROOT/frontend" \
    && tar xzf "\$BACKUP" -C "$APP_ROOT/frontend" \
    && chown -R health-flow:health-flow "$APP_ROOT/frontend" \
    && echo "restored=frontend-from-\$BACKUP" || echo "restore=failed"
else
  echo "restore=no-backup-available"
fi
if [ -n "\$prev" ]; then printf '%s\n' "\$prev" > "$MARKER"; chown health-flow:health-flow "$MARKER"; fi
if systemctl restart "$SERVICE"; then :; fi
exit 1
EOF

if [ "$DRY_RUN" = "1" ]; then
  step "DRY RUN — 不会碰 36"
  info "本地产物：$ARTIFACT"
  info "上传目标：$HOST_TARGET:$REMOTE_TARBALL"
  info "产物身份：--artifact-sha256 $ARTIFACT_SHA"
  printf '\n---- 远端命令（原样）----\n%s\n---- 结束 ----\n' "$REMOTE_CMD"
  exit 0
fi

# ── 4. 传输 ─────────────────────────────────────────────────────────────────
#
# `--artifact-sha256` is where the policy gate fires: `dev-host` refuses a write to
# a service host whose payload hash does not match the declared identity.
step "传输产物"
dev-host cp "$HOST_TARGET" "$ARTIFACT" --dest "$REMOTE_TARBALL" --artifact-sha256 "$ARTIFACT_SHA" \
  || die "传输失败"

# ── 5. 执行 ─────────────────────────────────────────────────────────────────
step "在 36 上部署"
REMOTE_OUT=$(dev-host exec "$HOST_TARGET" --allow-service-exec --timeout "$REMOTE_TIMEOUT" -- "$REMOTE_CMD") || {
  printf '%s\n' "$REMOTE_OUT" >&2
  die "远端执行返回非零（部署失败或自检失败，见上）"
}
printf '%s\n' "$REMOTE_OUT" | sed 's/^/   /'

# Parse by exact key match rather than substring search: the remote prints a mix of
# states, and "no-change" must not be satisfied by a line that merely mentions it.
STATE=$(printf '%s\n' "$REMOTE_OUT" | sed -n 's/^state=//p' | head -1)
REVISION=$(printf '%s\n' "$REMOTE_OUT" | sed -n 's/^revision=//p' | head -1)
SELFCHECK=$(printf '%s\n' "$REMOTE_OUT" | sed -n 's/^selfcheck=//p' | head -1)

step "结果"
info "commit=$FULL_SHA"
if [ "$STATE" = "no-change" ] && [ "$REVISION" = "$FULL_SHA" ]; then
  info "已是该修订，未做任何写入、未重启服务"
  exit 0
fi
[ "$SELFCHECK" = "passed" ] || die "36 上的自检未通过 —— 已按脚本内记录回滚；见上面的 restore= 与 backup= 行"
info "自检通过；36 上运行的即 $FULL_SHA"
