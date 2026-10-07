// Report duplicate architecture-review PRDs among open issues (#133).
//
// Prints "skipped" when no token is available — a check that cannot run is
// blocked, never silently green (`CLAUDE.md`). Reports only; never edits or
// closes anything.
import { execFileSync } from "node:child_process";

import { findDuplicatePrds } from "./prd-duplicates.mjs";

const repo = process.env.GH_REPO ?? "";
const token = process.env.GH_TOKEN ?? "";

if (!token || !repo) {
  console.log("prd-duplicates report: skipped (no GH_TOKEN/GH_REPO in this environment)");
  process.exit(0);
}

// 用 `gh api --paginate` 拉**全部** open issue：`gh issue list --limit` 会截断，
// 落在截断之外的重复会看不见，于是报「没有重复」—— 一个假绿（评审在 #166 指出）。
function listOpenIssues() {
  const raw = execFileSync(
    "gh",
    [
      "api",
      "--paginate",
      "--slurp",
      `repos/${repo}/issues?state=open&per_page=100`,
    ],
    { encoding: "utf8", env: { ...process.env }, maxBuffer: 64 * 1024 * 1024 },
  );
  // --slurp 把每一页包成一个数组，所以结果是「页的数组」。
  return JSON.parse(raw)
    .flat()
    .filter((issue) => !issue.pull_request) // 议题空间与 PR 共用编号，去掉 PR。
    .map((issue) => ({
      number: issue.number,
      body: issue.body,
      labels: (issue.labels ?? []).map((label) => label.name),
    }));
}

const issues = listOpenIssues();
console.log(`prd-duplicates report: scanned ${issues.length} open issue(s)`);

const duplicates = findDuplicatePrds(issues);
if (duplicates.length === 0) {
  console.log("prd-duplicates report: no duplicate open PRDs");
  process.exit(0);
}

console.log("prd-duplicates report: duplicate open PRDs found (report only — a human decides)");
for (const item of duplicates) {
  console.log(`  keep #${item.keep}, close ${item.drop.map((number) => `#${number}`).join(", ")}`);
}
// Deliberately exit 0: this is a report, not a gate. Making it fail would block
// every unrelated PR while a human triages the list.
