# External consumer browser verification

Set `HEALTHFLOW_E2E_CONSUMER_FIXTURE` to a trusted local module exporting `startConsumer({origin, apiOrigin, runDir})`, returning its cleanup callback. Set `HEALTHFLOW_E2E_CONSUMER_TEST_DIR` to its tests. Run the existing Playwright configuration. The optional `HEALTHFLOW_E2E_UPSTREAM_ENV` JSON configures synthetic upstream services. These execute local code: only use the consumer repository already authorized for the task.

Build the normal React assets first because the server runner requires them. Keep distinct loopback origins via `HEALTHFLOW_E2E_PORT` and `HEALTHFLOW_E2E_MALL_ORIGIN`. The harness continues to own temporary RSA keys, database and cleanup. Never point it at a working database.

Record exactly which consumer scenarios ran and identify both source revisions/build artifacts. Upstream fixture results do not prove production authentication, scientific correctness, model extraction or payment. See `docs/mall-module/qa-plan.md`.
