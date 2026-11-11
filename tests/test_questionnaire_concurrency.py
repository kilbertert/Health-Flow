"""Two writers must not both commit different answers based on the same revision."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api import report as api
from app.data.models import Base, MedicalReport, ReportQuestionnaireResponse
from app.schema.report import QuestionnaireSubmission
from app.service.questionnaire_vocabulary import QUESTIONNAIRE_VERSION


def test_two_simultaneous_updates_have_one_winner(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'answers.db'}", connect_args={"timeout": 10})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions() as db:
        report = MedicalReport(patient_id="test", owner_id="test", status="assessed")
        db.add(report)
        db.flush()
        report_id = report.id
        db.add(
            ReportQuestionnaireResponse(
                report_id=report_id,
                owner_id="test",
                questionnaire_version=QUESTIONNAIRE_VERSION,
                status="partial",
                answers={"sleep": "short"},
                revision=1,
            )
        )
        db.commit()
    barrier = Barrier(2)
    original = api._questionnaire_row

    def read_together(db, report_id):
        row = original(db, report_id)
        if row is not None and row.revision == 1:
            barrier.wait(timeout=5)
        return row

    def write(answer):
        with sessions() as db:
            try:
                result = asyncio.run(
                    api.put_questionnaire(
                        report_id,
                        QuestionnaireSubmission(
                            questionnaire_version=QUESTIONNAIRE_VERSION, answers={"sleep": answer}, base_revision=1
                        ),
                        Request({"type": "http"}),
                        db=db,
                    )
                )
                return 200, result.revision
            except HTTPException as exc:
                return exc.status_code, None

    with (
        patch.object(api, "resolve_owner", return_value=SimpleNamespace(storage_id="test")),
        patch.object(api, "_questionnaire_row", side_effect=read_together),
        ThreadPoolExecutor(2) as pool,
    ):
        results = list(pool.map(write, ["regular", "troubled"]))
    assert sorted(code for code, _ in results) == [200, 409]
    with sessions() as db:
        row = db.query(ReportQuestionnaireResponse).one()
        assert row.revision == 2
        assert row.answers["sleep"] in {"regular", "troubled"}
    engine.dispose()
