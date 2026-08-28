# Product Recommendation End-to-End Result

- Requirement: Genesis Evidence `#103` and HealthFlow recommendation display
- Tested HealthFlow commit: `b44f3652fc9f0c2b8e0bfa4d073d0d46ea2d9f57`
- Tested Genesis commit: `49d7639c6da343f913f44d1c38560b7e93129861`
- Executed at: `2026-08-28T20:02:50+08:00`
- Environment: Linux x86_64, Python 3.13.13, Node 24.15.0, isolated SQLite and loopback services

## Results

| QA ID | Result | Observable result |
| --- | --- | --- |
| `QA-REC-001` | PASS | Genesis recommendation fields survive HealthFlow validation and report persistence. |
| `QA-REC-002` | PASS | Product, nutrient, reason, safety message, disclaimer, and evidence link render at 375px, 414px, and desktop widths. |
| `QA-E2E-003` | PASS | A real 10-page report produced 68 metrics, no parsing warnings, and no false abnormal condition or recommendation. |
| `QA-E2E-004` | PASS | A controlled LDL-C report completed upload, parsing, confirmation, condition matching, and product recommendation. |

## Positive Chain

- Parsed metric: LDL-C `4.20 mmol/L`, reference upper bound `3.40 mmol/L`, abnormal flag `H`.
- Confirmation retained complete source evidence and the report reached `assessed`.
- Matched condition: `COND_DYSLIPIDEMIA`.
- Product response: `available`; product `郅臻堂®植物甾醇咀嚼片`; nutrient `植物甾醇`.
- Matching remainder: `unmatched=[]`; `skipped=[]`.

## Safety And Review Gates

- The real classification workbook produced 12 mapping drafts; 10 were published through Review API.
- Two high-risk mappings remained `needs_more_info`; no manual bypass was used.
- An incomplete confirmation without the reference bound was rejected as `missing_source_evidence`. The complete request passed.

## Deterministic Checks

- Backend: `143 passed`, `1 skipped`; changed Python files pass Ruff.
- Frontend: production build passed; Playwright recommendation assertions passed at 375px, 414px, and desktop widths.
- Full-repository Ruff has pre-existing findings outside the changed paths and is not reported as passing.

Raw reports, tokens, private identifiers, isolated databases, and unredacted logs are intentionally excluded.

## Product Image Extension

- Tested HealthFlow commit: `ff3cd20` (feature commit `9a17e8a`)
- Executed at: `2026-08-28T23:43:38+08:00`
- Environment: Linux x86_64, Node 24.15.0, Python 3.13.13, isolated SQLite and loopback service
- Result: PASS. The published 郅臻堂®植物甾醇咀嚼片 recommendation displays its bundled packaging image at 375px and 414px; the image asset returned HTTP 200 and the recommendation text remained visible without horizontal overflow.
- Deterministic checks: `npm run build` passed; `HEALTHFLOW_E2E_PYTHON=/home/claude/Projects/health-flow/.venv/bin/python npm run test:e2e` passed (`38 passed`).
