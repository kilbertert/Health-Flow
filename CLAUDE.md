# Claude Code

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles use the default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Verification

Unit and integration tests are necessary but not sufficient. When a change
alters behavior a patient can see, prove it on the running app before you call
it done: invoke the `verify-healthflow` skill, drive the affected feature in a
real browser, and keep the captured evidence.

`verify-healthflow` lists the covered features in its `features/` map — check
there first rather than inventing a new harness. Because it follows a feature
map rather than a change set, "the suite passed" is not the claim: say which
features you drove and where the evidence is.

When it cannot run — a missing browser, a service that will not start, a
feature named on the map but unreachable — that is `blocked`: name the
prerequisite and the route attempted. A blocked verification is never reported
as a pass, and in the PRD workflow it is `<promise>BLOCKED</promise>` rather
than a commit.

<!-- afk-bootstrap:managed:start -->
## AFK workflow gate

For idea or planning work, read `docs/afk-workflow.md` and the applicable
files under `docs/agents/` first.

`/grill-with-docs` ends only when its frontier is empty: report
`GRILLING_COMPLETE`, summarize the shared understanding, ask the user to
confirm it, and stop. Confirmation completes grilling only. Wait for the user
to explicitly invoke `/to-spec`, `/to-tickets`, `/implement`, or
`/implement-spec`; do not enter another phase automatically. Multi-session
work uses `/to-spec` then `/to-tickets` before implementation.
<!-- afk-bootstrap:managed:end -->
