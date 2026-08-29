# Health-Flow agent instructions

Read the project-specific `CLAUDE.md`, `CONTEXT.md`, `docs/`, and applicable
ADRs before changing code. Keep user-visible health language within the
project's domain glossary and run the documented Python checks.

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
