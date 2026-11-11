"""An unavailable model cannot turn a saved upload into sample metrics."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.data.models import Base, MedicalReport, MetricRecord, ReportExtractionJob, ReportFile
from app.service.report_worker import run_next_job


def test_real_worker_with_http_503_persists_no_fake_success(tmp_path, monkeypatch):
    class Unavailable(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Unavailable)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("OPENAI_RESPONSES_URL", f"http://127.0.0.1:{server.server_port}/v1/responses")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("VLLM_API_KEY", "")
    monkeypatch.setenv("REPORT_JOB_MAX_ATTEMPTS", "1")
    get_settings.cache_clear()
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    buf = BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="PNG")
    image = tmp_path / "report.png"
    image.write_bytes(buf.getvalue())
    try:
        with factory() as db:
            report = MedicalReport(patient_id="synthetic", owner_id="synthetic", status="processing")
            db.add(report)
            db.flush()
            db.add(
                ReportFile(
                    report_id=report.id,
                    file_index=1,
                    original_filename=image.name,
                    media_type="image/png",
                    stored_path=str(image),
                    page_count=1,
                )
            )
            db.add(ReportExtractionJob(report_id=report.id, status="queued"))
            db.commit()
        assert run_next_job(factory) is not None
        with factory() as db:
            assert db.query(MetricRecord).count() == 0
            report = db.query(MedicalReport).one()
            assert report.status == "failed"
            assert report.parsed_content["failure"] == "provider_unavailable"
            assert report.evidence_result is None
            assert db.query(ReportExtractionJob).one().status == "failed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        engine.dispose()
        get_settings.cache_clear()
