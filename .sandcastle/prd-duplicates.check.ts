/**
 * Runnable regression check for the duplicate-PRD classifier.
 *
 * Run: `npx tsx .sandcastle/prd-duplicates.check.ts`
 *
 * The judgement that matters is "at least one copy carries the provenance
 * label": the copy that lost the label is exactly the one the next run cannot
 * see, so it would be proposed again the following day. Without this check, a
 * well-meaning refactor to `every()` would quietly stop reporting those.
 */
import { findDuplicatePrds, normalizeBody } from "./prd-duplicates.mjs";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(`prd-duplicates check failed: ${message}`);
}

const LABEL = "source:architecture-review";

// Body normalisation: trailing whitespace and blank lines are not a difference.
assert(normalizeBody("A\n\nB  \n") === normalizeBody("A\n B"), "normalisation must fold blank lines and trim");

// The real shape: one copy labelled, one not.
const duplicates = findDuplicatePrds([
  { number: 90, body: "A\n\nB", labels: [LABEL] },
  { number: 91, body: "A\n \nB", labels: [] },
]);
assert(duplicates.length === 1, "a labelled/unlabelled pair must be reported");
assert(duplicates[0].keep === 90, "the labelled copy is the one to keep");
assert(duplicates[0].drop.length === 1 && duplicates[0].drop[0] === 91, "the unlabelled copy is the one to drop");

// Two unlabelled copies are not this workflow's output — out of scope.
assert(
  findDuplicatePrds([
    { number: 1, body: "C", labels: [] },
    { number: 2, body: "C", labels: [] },
  ]).length === 0,
  "unlabelled pairs are not architecture-review output",
);

// Distinct bodies are not duplicates.
assert(
  findDuplicatePrds([
    { number: 3, body: "D", labels: [LABEL] },
    { number: 4, body: "E", labels: [LABEL] },
  ]).length === 0,
  "distinct bodies are not duplicates",
);

// Empty bodies must not all collapse into one group.
assert(
  findDuplicatePrds([
    { number: 5, body: "", labels: [LABEL] },
    { number: 6, body: "", labels: [LABEL] },
  ]).length === 0,
  "empty bodies are not evidence of duplication",
);

console.log("prd-duplicates check passed");
