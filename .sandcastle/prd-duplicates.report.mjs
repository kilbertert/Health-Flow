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

const raw = execFileSync(
  "gh",
  ["issue", "list", "--state", "open", "--limit", "200", "--json", "number,body,labels"],
  { encoding: "utf8", env: { ...process.env } },
);
const issues = JSON.parse(raw).map((issue) => ({
  number: issue.number,
  body: issue.body,
  labels: (issue.labels ?? []).map((label) => label.name),
}));

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
