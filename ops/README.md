# User service deployment

Health-Flow serves the patient-facing report portal and the report API. It runs
on the development host beside the `genesis-evidence` services, which own the
reviewed evidence side of the product.

## Services

| unit | role | address |
|---|---|---|
| `health-flow.service` | user portal + report API (FastAPI, serving the built React frontend) | `127.0.0.1:8127` |
| `health-flow-report-worker.service` | durable report extraction queue (`app/service/report_worker.py`) | none |

Public entry is `genesis-evidence.ranlei.work`. The app binds loopback only; the
public name is terminated in front of it, and the report portal is never reached
by its port directly. Patient access is an account session
(`REPORT_ACCOUNT_REQUIRED=true`); RFC 7617 Basic Auth is an operator
compatibility gate that defaults off.

Uploads are persisted before any model call and return `202 processing`. The
worker consumes the persisted queue, after which the report reaches
`pending_confirmation` and the browser polls with short authenticated requests
rather than holding a long reverse-proxy connection.

## Environment

Create the private `var/health-flow.env` from
[`examples/health-flow.env.example`](examples/health-flow.env.example), then
install both unit files into `~/.config/systemd/user/`. Never commit the env
file; `var/` and `.env` are gitignored.

- `DATABASE_URL` — SQLite in development, a server database in production.
- `APP_ENV` — set `production` when the deployment is not a development one.
  It also decides the defaults for the account gate and the secure cookie.
- `FRONTEND_DIST` / `SERVE_FRONTEND` — the app serves the built frontend from
  this directory. Build it with `npm run build` in `frontend/` before starting
  the service; the service does not build it.
- `OPENAI_API_KEY`, `OPENAI_RESPONSES_URL`, `VLLM_MODEL` — the report extraction
  provider. Report extraction fails without a key; the rest of the app runs.
- `GENESIS_EVIDENCE_API_URL`, `GENESIS_EVIDENCE_API_KEY` — the evidence service
  edge. The key must match the value the evidence service expects under the same
  name.

## Evidence boundary

Health-Flow submits confirmed metrics to the evidence service and renders what
comes back. It does not hold or rank evidence itself: the evidence service
decides anomalies from the confirmed values and returns only published
knowledge cards with their claim and paper traceability. The call is made after
the user confirms the metrics, and it goes to the evidence service on loopback —
the evidence service has no user-facing domain of its own.

## Operations

Both units run as `claude` user units:

```bash
systemctl --user status health-flow health-flow-report-worker
systemctl --user restart health-flow             # after changing the frontend build or env
systemctl --user restart health-flow-report-worker
```

If uploads stay at `processing`, check the worker first: it is the only consumer
of the report queue, and the portal does not parse reports inline.
