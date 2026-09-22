"""Exercise the health-flow → genesis-evidence bridge from inside the deployment.

This calls the same function the report API calls, so it proves the real
cross-service path: health-flow's configuration, its key, the loopback edge to
the evidence service on this host, and the response contract.
"""

import asyncio

from app.service.evidence_bridge import EvidenceBridgeError, fetch_metric_catalog, match_published_evidence


async def main() -> int:
    print("--- metric catalog (GET /api/metrics) ---")
    try:
        catalog = await fetch_metric_catalog()
        print(f"metric codes returned: {len(catalog)}")
        for item in catalog[:3]:
            print("  ", item.get("code"), "|", item.get("label"))
    except EvidenceBridgeError as exc:
        print("catalog FAILED:", exc)
        return 1

    print("--- evidence match (POST /api/evidence/matches) ---")
    observations = [{
        "observation_id": "bridge-probe-1",
        "confirmation_status": "confirmed",
        "metric_code": "fasting_glucose",
        "value": 7.2,
        "unit": "mmol/L",
        "reference_low": 3.9,
        "reference_high": 6.1,
        "evidence_text": "空腹血糖 7.2 mmol/L 参考范围 3.9-6.1",
        "source_file_index": 1,
        "source_page": 1,
    }]
    try:
        result = await match_published_evidence(observations)
    except EvidenceBridgeError as exc:
        print("match FAILED:", exc)
        return 1

    print("schema_version:", result.get("schema_version"))
    print("correlation_id:", result.get("correlation_id"))
    print("message:", result.get("message"))
    unmatched = result.get("unmatched") or []
    print("unmatched count:", len(unmatched))
    if unmatched:
        first = unmatched[0]
        print("  reason:", first.get("reason"))
        print("  condition_codes:", first.get("condition_codes"))
    print()
    print("BRIDGE OK: health-flow reached the evidence service on this host")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
