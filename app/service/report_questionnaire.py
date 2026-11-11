"""生活问卷的服务端判定（#295）：提交校验、状态推导、并发裁决、个性化提示。

持久化只有 `ReportQuestionnaireResponse` 一行（#292 冻结）：一份报告一行、
重答 UPDATE 原行、`revision` 单调递增做乐观并发裁决。个性化提示按已保存的
答案从声明表**派生**，不落库 —— 删报告带走答案行时派生内容自然消失，
没有第二份数据要清。

题目、选项与状态在 `questionnaire_vocabulary.py`。本模块仅把自述组织为就医沟通问题。
"""

from typing import Any

from app.service.questionnaire_vocabulary import (
    QUESTIONNAIRE_OPTION_IDS,
    QUESTIONNAIRE_QUESTION_IDS,
    QUESTIONNAIRE_QUESTIONS,
    QUESTIONNAIRE_VERSION,
)


def validate_submission(submission: Any) -> str | None:
    """提交的词表校验：版本、题目 ID、选项 ID 都必须出自声明集合，否则给原因。"""
    if submission.questionnaire_version != QUESTIONNAIRE_VERSION:
        return "问卷版本与服务端不一致"
    for question_id, option_id in (submission.answers or {}).items():
        allowed = QUESTIONNAIRE_OPTION_IDS.get(question_id)
        if allowed is None:
            return f"提交包含未声明的题目：{question_id}"
        if option_id not in allowed:
            return f"题目 {question_id} 的选项 {option_id} 不在声明集合内"
    return None


def status_for(answers: dict[str, str], skipped: bool) -> str:
    """落库状态：`skipped` 是明确表态；否则按题目覆盖度（空 answers 也计
    `partial` —— 契约 #292 已冻结这条，它不是 skipped）。
    """
    if skipped:
        return "skipped"
    return "answered" if set(answers) >= QUESTIONNAIRE_QUESTION_IDS else "partial"


def is_same_answer_set(row: Any, submission: Any) -> bool:
    """幂等判据：与已保存内容**相同**的重复提交不再递增版本、也不按冲突报错。

    它与并发裁决是两个不同问题：内容相同 → 重放安全，如实返回现状；内容不同 →
    旧凭据想写新东西，必须拒绝（否则并发旧请求覆盖了新答案）。
    """
    if row is None or row.questionnaire_version != submission.questionnaire_version:
        return False
    if submission.skipped:
        return row.status == "skipped"
    return row.status == status_for(submission.answers, False) and dict(row.answers or {}) == dict(
        submission.answers or {}
    )


def personalized_tips(answers: dict[str, str]) -> list[dict[str, str]]:
    """Only organize self-reported answers into clinician questions, not health advice."""
    tips = []
    for question in QUESTIONNAIRE_QUESTIONS:
        option_id = answers.get(question["id"])
        if option_id not in QUESTIONNAIRE_OPTION_IDS[question["id"]]:
            continue
        tips.append(
            {
                "question_id": question["id"],
                "option_id": option_id,
                "text": "就医时可沟通：这个生活情况是否需要结合报告进一步了解？",
            }
        )
    return tips


def state_for(row: Any) -> dict[str, Any]:
    """行的当前读法：题目按当前版本出，旧版本的答案不再算「已答」。

    旧版本行**保留**（revision 照读，等待重答覆盖）—— 拿旧答案套新题等于替患者
    回答了他没答过的题。
    """
    if row is None or row.questionnaire_version != QUESTIONNAIRE_VERSION:
        return {
            "questionnaire_version": QUESTIONNAIRE_VERSION,
            "status": "unanswered",
            "questions": list(QUESTIONNAIRE_QUESTIONS),
            "answers": {},
            "revision": row.revision if row is not None else 0,
            "tips": [],
        }
    answers = dict(row.answers or {})
    return {
        "questionnaire_version": QUESTIONNAIRE_VERSION,
        "status": row.status,
        "questions": list(QUESTIONNAIRE_QUESTIONS),
        "answers": answers,
        "revision": row.revision,
        "tips": personalized_tips(answers),
    }
