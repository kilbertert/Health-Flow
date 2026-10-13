# 14. 定性结果按报告自身的语义判：Not Detected = 阴性 = 正常

日期：2026-10-13
状态：已采纳
影响版本：本仓库（`app/service/admission.py`、`app/service/admission_vocabulary.py`、
`app/schema/report.py`、`app/api/auth.py`、`frontend/src/admissionText.js`、
`frontend/src/pages/Upload.jsx`）

## 背景

`HBsAg` 一类的定性项，报告印的是 `Not Detected`，原文里**没有**取值域：

```
r56/r57/r59  Hepatitis B Surface antigen - HBsAg   val="Not Detected"
             evidence="Hepatitis B Surface antigen - HBsAg B型肝炎表面抗原 Not Detected"
             reference_range=(空)
```

`#206` 修的是「报告印了取值域」的定性项（`Bilirubin … Nil (Nil)` —— 括号里那个就是
判据）。这一行**没印**，于是 `value_reason` 判 `invalid_value`，患者看到
**「数值不是一个数（如带符号或混入文字），需要修正」** —— 面对一个「未检出」，系统要求
他修正一个数字。全库 3 行都推给了患者。

## 决定

### 一、判据是**结果词本身**，依据是标准

- **HL7 v2 OBX-7（References Range）** 的定义写在**定量**观测上
  （"When the observation **quantifies** the amount of a toxic substance, then the
  upper limit of the range identifies the toxic limit."）。
- **同一标准的 OBX-8（Interpretation Codes）**：
  `One or more codes specifying a **categorical assessment** of the observation value`，
  取值即 `Normal / Abnormal / Positive / Negative` 这一类。

所以一个 `Not Detected` 这样的**分类结果**，它的判据就是它自己；报告没印取值域
**≠** 这一项没有判据。

### 二、判据是**一份封闭词表 + 整值匹配**，不是「非数值词就是阴性」

这是本决定里唯一需要论证「为什么可以有一份词表」的地方 —— 因为本仓库别处刻意不维护
「指标名清单」（那份清单会随每一份新报告过期）。两者不同：指标名随报告变长，而**分类
结果词是一个封闭的规范集合** —— 检验报告判阳/阴就是那几个词，而且它们是**判据本身**，
不掺任何指标名。

```python
_QUALITATIVE_WORDS = (
    ("negative", ("not detected", "undetected", "negative", "nil", "absent",
                  "non-reactive", "nonreactive")),
    ("positive", ("detected", "positive", "reactive", "present")),
)
```

两条**证伪条件**，缺一这份词表就会咬人：

1. **必须整值匹配，不是子串。** 真语料里的假朋友 `AB Rh(D) POSITIVE` 是**血型**
   （描述项），含 "POSITIVE" 却不是阳性检验结果 —— 子串匹配会把它误判成阳性异常。整值
   匹配之下它落 `no_reference_concept`（它本来就是描述项）。
2. **词表外的词一律不判。** `Pale Yellow`、`Fasting` 不在表里，一律落回今天的原因。
   把任意非数值词都当成阴性，会把**「值读不出来」也判成正常** —— 那正是本 PRD 一直在
   消灭的错法。

### 三、`Not Detected` = 正常、`Detected` = 异常；两者都不需求患者修正

- 阴性（`Not Detected` / `Negative` / `Nil`）→ 新结论 `qualitative_negative`，患者
  **不必动手**。为什么单列而不并进 `within_reference_range`：那一句说的是「在参考区间
  内」，而这里根本没有区间 —— 报告给的是「未检出」。
- 阳性（`Detected` / `Positive`）→ 新结论 `qualitative_positive`，报告单如实说
  「检测到（阳性）」。

**异常判定与准入必须同向**（#235 的评审教训：两处各判一次会让同一行一边说正常、一边报
H）。分类结果**没有方向**，所以阳性是 `A`（异常），不是 `H`（偏高）—— 说「H 偏高」会在
一个阳性/阴性检验上说假话。`A` 与 `H`/`L` 一样被患者侧显示为异常、被摘要计数，但不带
方向。

### 四、阳性**不跨证据边界**（本决定的一处折中，写在这里而不是悄悄绕过）

票面期望「`Detected` 进解读、连知识卡」。**它今天做不到**，实测：

```
SourceObservation.value: float            # 必填浮点
single_number("Detected")          -> None
SourceObservation(value=None, ...)  -> ValidationError
# 连 Unmatched.source_observation 也是同一个必填 float
```

跨证据边界的载荷与知识卡的匹配都要求**一个数值**，而一个词形的分类结果**没有数值可跨**。
让它跨过去只有两条路：

- 给 `SourceObservation` 加分类值/编码通道 —— **跨服务契约变更**（对面是
  Genesis Evidence 的 v3 契约，有版本协商与回归面），
- 给 `Detected` 编一个数 —— 编造数据，直接排除。

本次**不改契约**：阳性落 `qualitative_positive`，患者侧如实看到异常并被告知按提示就诊，
但它不进入知识卡匹配。**我们不假装能连。** 让分类结果真正跨边界是独立一次改动、独立一条
记录（票面范围之外）。

## 被否掉的方案

- **把 `Detected` 也判成正常。** 那是把阳性说成没事 —— 系统性风险最大的错法，直接排除。
- **子串匹配「POSITIVE / DETECTED」。** `AB Rh(D) POSITIVE` 会被误判成阳性异常。
- **把任意非数值词都当成阴性。** 「值读不出来」也会变成「正常」。
- **给 `Detected` 一个数字让它跨边界。** 编造数据。
- **把 `Not Detected` 并进 `within_reference_range`。** 患者侧的说法不同（「在参考区间
  内」vs「报告给出的是未检出」），合并会让两句话变成一句不相干的话。

## 后果

- **回放暴露的一处**：`value_level_reason` 原先在「值没坏」之后无脑接
  `reference_reason`，于是 `HBsAg: Not Detected`（原文没有取值域）会先被判成「值没坏」、
  又被补上 `missing_reference_range` —— 判定守卫说它判不了、准入说它正常，患者仍被请去
  核对一个已经判成正常的行。修法是**自带判据的值不看参考范围**（
  `_value_carries_its_own_criterion`，`value_only_reason` 与 `value_level_reason` 共用）。
  这处是被**回放**逮到的，不是被某条单点测试 —— 准入与异常判定「互相打架」这一类的守卫
  只有回放能兜住。

### 评审（#239）改掉的四处

1. **报告自己的判据优先于内置词表。** 第一版让分类词表在 `word_domain_verdict` **之后**
   跑，于是 `Negative (Positive)`（报告印的判据是 `Positive`、值 `Negative` 对不上）被判成
   `qualitative_negative` —— 把一个**真的阳性项说成正常**。修法：报告印了取值域时内置词表
   让位，`_value_carries_its_own_criterion` 里把 `word_domain_verdict` 也收进来（相符才算
   自带判据），于是准入与判定守卫对「相符 / 不符」给出同一对答案。
2. **摘要不能把阳性埋进「未能判定」。** 阳性落 `skipped`，而 `build_patient_notices` 原来
   只把 `within_reference_range` 排除在 `inconclusive` 之外 —— 于是一份只有 `HBsAg:
   Detected` 的报告，摘要写「1 项未能完成解读，其余未见需要关注的问题」，患者面前却挂着
   一个阳性。修法：`qualitative_positive` 有**自己的摘要支**（明确说「定性结果为阳性……
   请咨询医生」且不参与知识卡匹配），`qualitative_negative` 不算 `inconclusive`。
3. **历史摘要说「N 项偏高/偏低」是假话。** `A` 无方向，说它偏高或偏低在阳性/阴性检验上
   不成立。改成中性的「N 项异常」。
4. **GLOSSARY 的「异常判定」词条仍只列 H/L/N**，与出域契约不一致。补上 `A` 与它的语义。
- 全库 3 行 `Not Detected` 从「数值不是一个数，需要修正」变成
  `qualitative_negative`（正常、无需操作）。
- 结论词表加两条（`qualitative_negative` / `qualitative_positive`），`NORMAL_REASONS`
  加一条，`NO_ACTION_REASONS` 加一条 —— 前端 `NO_ACTION_REASONS` 由既有守卫钉住两者相等。
- 判定标志加一个取值 `A`（分类阳性）。它出现在响应契约
  （`inferred_abnormal_flag: Literal["H","L","N","A"]`）、异常计数
  （`app/api/auth.py` 的 `{"H","L"}` → `{"H","L","A"}`）、前端 `needsReview` /
  `suggestedDecision` / `abnormalTag`（`A` 早已显示为「异常」）。
- `value_reason`（只看值）与 `value_level_reason`（值 + 参考范围）之间抽出
  `value_only_reason`：确认页的反推**只到值这一层**，两侧逐字相同的基准就是它。新增的
  两条出口（带符号、分类结果词）都在这一层里，所以两侧都不会分叉。

## 验证

| 断言 | 结果 |
| --- | --- |
| `HBsAg: Not Detected` 准入 | `qualitative_negative`（原 `invalid_value`） |
| `HBsAg: Detected` 准入 | `qualitative_positive` |
| 异常判定 `Detected` / `Not Detected` | `A` / `N`（与准入同向） |
| 生产库 259 行回放 | 3 行 `Not Detected` 全部落 `qualitative_negative` 且异常判定为 `N`（修 `_value_carries_its_own_criterion` 前是 `None`） |
| `AB Rh(D) POSITIVE`（血型） | `no_reference_concept`（**不**被当成阳性） |
| `Pale Yellow` / `Fasting` / 空 / 含数字 | 不是分类结果词（落回原原因） |
| 跨语言两侧一致（`test_both_sides_give_the_same_reason_for_every_shape`） | 通过（基准改为 `value_only_reason`） |
| 台账全量覆盖（每个词表取值各一行） | 通过 |
| 真实浏览器：确认页分类结果行不再挂「需要修正」 | 通过（负控：还原前端出口则该用例变红，含 `AB Rh(D) POSITIVE` 仍挂标记） |
| 报告自己的判据优先（`Negative (Positive)` / `Detected (Detected)`） | 通过（评审 #239 找出的 false-normal 方向；准入与判定守卫同一对答案） |
| 摘要对阳性单独说话、阴性不算「未能完成解读」 | 通过（`test_patient_notices.py` 新增两条） |
| 历史摘要说「N 项异常」而非「偏高/偏低」 | 通过（两条 e2e 断言同步更新） |
