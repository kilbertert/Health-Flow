# Optional external consumer check

The external consumer supplies an authorized fixture module and test directory. The fixture starts the consumer's loopback origin and returns its cleanup callback; the existing harness starts the real HealthFlow API with a fresh database and signing keypair. Optional upstream settings point only to synthetic local services.

1. Build the consumer before the test starts. Set `HEALTHFLOW_E2E_CONSUMER_FIXTURE` and `HEALTHFLOW_E2E_CONSUMER_TEST_DIR`; use distinct loopback origins.
2. Run the existing Playwright configuration with the consumer's spec selected.
3. Retain the browser report, scenario results, screenshots, consumer artifact identity and HealthFlow revision.
4. Verify teardown removes the run's temporary database, keys and listeners.

Normal runs with no consumer configuration retain the current empty mall page and test directory. Consumer-specific routes, assets and fixtures belong in its private repository. This extension does not prove production authentication, model extraction, medical correctness or payment.

Report upgrade acceptance additionally covers mobile report pages at 360/390px,
keyboard interaction, expanded ranges, absent additive fields and no-card/unknown
states. Exercise questionnaire persistence, failure recovery, skip/reanswer,
concurrent writes and deletion. Health-related output must distinguish self-reports
from report observations; unreviewed prescriptive advice is not part of acceptance.
An injected browser response verifies rendering only. Parser/worker failure checks
must verify no candidate metrics or successful report are persisted.
