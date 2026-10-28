# 30. Verify the external mall consumer through the existing browser harness

Date: 2026-10-28
Status: Accepted

## Context

The patient interface moves into an external mall H5 under ADR 0026. A browser that only calls APIs from an empty page proves CORS but cannot prove that the consumer preserves consent, confirmation and report ownership.

## Decision

The existing Playwright harness optionally imports a caller-supplied fixture and test directory. The external fixture serves its build at the separate mall origin. It keeps the real HealthFlow service, ephemeral signing keys and throwaway database. The caller supplies synthetic upstream environment overrides for evidence and goods. A closed list permits only string-valued upstream settings; database, file storage, identity and origin isolation cannot be overridden through this input. Company interface fixtures remain in the private consumer repository; none are copied into this repository. Ordinary runs retain their current mall-origin behavior and do not require an external checkout.

## Alternatives considered

Mocking HealthFlow responses in the browser is faster, but would miss mismatches in persisted consent and confirmation. Starting a second standalone Python/browser stack would duplicate the database and key lifecycle already owned by the harness. Mandatory consumer CI would require a private external build that this repository does not own.

## Consequences

The optional run proves the integrated consumer/API behavior and retains browser evidence. Its caller must supply and identify a completed build. It does not prove production login, model extraction, upstream scientific correctness or payment. Normal deterministic CI remains independent of private source and deployment assets.
