"""The phase-one runtime exposes only report interpretation routes."""

from fastapi.testclient import TestClient

from app.main import app


def test_frozen_routes_are_not_mounted() -> None:
    client = TestClient(app)
    for path, method in (
        ("/api/health/chat", "post"),
        ("/api/health/kg/query", "post"),
        ("/api/health/metric/trend", "get"),
        ("/api/health/train/tasks", "get"),
        ("/api/health/reports", "get"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 404
