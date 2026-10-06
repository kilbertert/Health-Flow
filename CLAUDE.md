# Claude Code

## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles use the default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

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
