"""生活问卷的唯一词表与定义（health-flow #291/#295 的契约侧，#292 冻结）。

只有声明，不 import 任何项目模块——与 `admission_vocabulary.py` /
`consent_vocabulary.py` 同一个理由：词表被 schema、service、测试三侧同源
派生，任何一处 import 业务模块都会把「声明」变成「实现」。

边界（与 #291 的决策一致）：

- 问卷是**可选**的：跳过、部分回答、重答、刷新恢复都是合法状态。
- 题目是**服务端固定**的：题目版本、题目 ID、选项 ID 由服务端校验，
  客户端只能提交这里声明过的组合，不能自造题目或选项。
- 答案**不改写报告事实**：数值、范围、异常判定、健康问题、紧急程度
  都不因回答而变化；问卷只影响单独呈现的个性化提示。
"""

from typing import Final, Literal

#: 当前生效的题目版本。改题、改选项、改语义都要换新版本——答案行记的是
#: 作答那一刻的版本，旧答案在新版本下不再算「已答」（题目可能已不同义）。
QUESTIONNAIRE_VERSION: Final[str] = "lifestyle-2026-11"

#: 首版固定 4 题：睡眠、饮食、运动、近期压力（#291 决策）。`id` 是出域的
#: 稳定键，文案可以改但 `id` 不能换——客户端按 `id` 提交，不按文案匹配。
QUESTIONNAIRE_QUESTIONS: Final[tuple[dict, ...]] = (
    {
        "id": "sleep",
        "text": "最近一个月，你的睡眠情况更接近哪种？",
        "options": (
            {"id": "regular", "label": "作息规律，基本睡够"},
            {"id": "short", "label": "经常熬夜或睡不够"},
            {"id": "troubled", "label": "入睡困难、易醒或睡不解乏"},
        ),
    },
    {
        "id": "diet",
        "text": "最近一个月，你的日常饮食更接近哪种？",
        "options": (
            {"id": "balanced", "label": "三餐规律，荤素较均衡"},
            {"id": "irregular", "label": "经常漏餐，或以外卖/速食为主"},
            {"id": "heavy", "label": "偏油偏咸，或应酬聚餐较多"},
        ),
    },
    {
        "id": "exercise",
        "text": "最近一个月，你的身体活动更接近哪种？",
        "options": (
            {"id": "regular", "label": "每周运动三次或以上"},
            {"id": "occasional", "label": "偶尔活动，没有规律"},
            {"id": "sedentary", "label": "基本久坐，很少运动"},
        ),
    },
    {
        "id": "stress",
        "text": "最近一个月，你感受到的压力更接近哪种？",
        "options": (
            {"id": "calm", "label": "比较平稳"},
            {"id": "moderate", "label": "有压力，但能应付"},
            {"id": "high", "label": "持续高压或明显焦虑"},
        ),
    },
)

QUESTIONNAIRE_QUESTION_IDS: Final[frozenset[str]] = frozenset(question["id"] for question in QUESTIONNAIRE_QUESTIONS)

#: 每题允许的选项 ID。答案提交按这个表校验——题目/选项之外的 ID 一律拒绝。
QUESTIONNAIRE_OPTION_IDS: Final[dict[str, frozenset[str]]] = {
    question["id"]: frozenset(option["id"] for option in question["options"]) for question in QUESTIONNAIRE_QUESTIONS
}

#: 问卷状态。**唯一词表**，schema 的 Literal 从这里派生：
#:
#: - ``unanswered``：从未提交过（包括功能上线前的旧报告——它们没有答案行）。
#: - ``skipped``：患者明确选择跳过。记录它，是因为「还没答」与「选择不答」
#:   是两件事：前者该继续引导，后者不该反复打扰。跳过之后仍可重答。
#: - ``partial``：回答了部分题目。
#: - ``answered``：全部题目都已回答。
QUESTIONNAIRE_STATUSES: Final[tuple[str, ...]] = (
    "unanswered",
    "skipped",
    "partial",
    "answered",
)

QuestionnaireStatusLiteral = Literal["unanswered", "skipped", "partial", "answered"]
