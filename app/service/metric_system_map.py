"""「指标 → 器官/系统」映射的唯一声明（health-flow #291/#294 的契约侧，#292 冻结）。

只有声明，不 import 任何项目模块——词表纪律同 `admission_vocabulary.py`。

出处与审查：

- 归属依据是**常规体检套餐的检验面板约定**（肝功能面板、肾功能/尿常规、
  血常规、血脂/血糖代谢、电解质、骨代谢），不是诊断学结论。每项归属是
  「这份报告上它印在哪一栏」的同义改写，可由审核者逐行复查。
- 本表按**证据服务的 canonical `metric_code`** 归组（与准入、计数同一个键）。
  报告上的项目名经 `metric_code` 解析落进来；解析不出 canonical 码的行
  归 `other`，不猜。
- 一个指标只归一组（单归属）。有双重归属争议的项（`uric_acid` 代谢/肾、
  `alp` 肝/骨、`ck_mb` 心/肌）按**报告面板归属**裁定，理由逐条写在下表。

语义边界（#291 决策）：分组只回答「这项指标属于哪个系统」。**不**给组
一个单一健康状态，不给分，不在无检测数据时宣称器官健康——组里没有的
系统就是不出现。组内指标的「异常/待核对/范围内」各行自己携带，同组
并存时两者都展示。
"""

from typing import Final

#: 展示序即声明序。`other` 永远在最后：它是兜底，不是一个「系统」。
SYSTEM_GROUPS: Final[tuple[tuple[str, str], ...]] = (
    ("cardiovascular_metabolic", "心血管与代谢"),
    ("hepatobiliary", "肝胆"),
    ("renal_urinary", "肾脏与泌尿"),
    ("hematologic", "血液"),
    ("electrolyte", "电解质"),
    ("bone_muscle", "骨骼与肌肉"),
    ("eye", "眼科"),
    ("special_marker", "专项标志物"),
    ("other", "其他"),
)

OTHER_GROUP_KEY: Final[str] = "other"

SYSTEM_GROUP_KEYS: Final[frozenset[str]] = frozenset(
    key for key, _name in SYSTEM_GROUPS
)

#: canonical metric_code → 分组 key。覆盖面 = 证据服务现行指标目录全量；
#: 目录新增码在映射评审前落 `other`，不猜归属。
METRIC_SYSTEM_MAP: Final[dict[str, str]] = {
    # ── 心血管与代谢：血压、血糖、血脂、尿酸、体格 ──────────────────────
    # `uric_acid` 双属代谢与肾排泄；体检套餐把它印在代谢/肾功能交界，
    # 按代谢归组——它的问题是生成与排泄失衡，患者侧归到「代谢」更说得通。
    "systolic_blood_pressure": "cardiovascular_metabolic",
    "diastolic_blood_pressure": "cardiovascular_metabolic",
    "fasting_glucose": "cardiovascular_metabolic",
    "hba1c": "cardiovascular_metabolic",
    "triglycerides": "cardiovascular_metabolic",
    "hdl_c": "cardiovascular_metabolic",
    "ldl_c": "cardiovascular_metabolic",
    "total_cholesterol": "cardiovascular_metabolic",
    "non_hdl_c": "cardiovascular_metabolic",
    "uric_acid": "cardiovascular_metabolic",
    "bmi": "cardiovascular_metabolic",
    "waist_circumference": "cardiovascular_metabolic",
    "body_fat_rate": "cardiovascular_metabolic",
    # `ck_mb` 是心肌损伤标志物（体检印在心肌酶栏）；`ck` 是骨骼肌为主的
    # 总酶，归「骨骼与肌肉」——两者同名不同归属，不能合并。
    "ck_mb": "cardiovascular_metabolic",
    # ── 肝胆：肝功能面板常规项 ─────────────────────────────────────────
    # `alp` 双属肝与骨；体检套餐把它印在肝功能栏，按面板归属。
    "alt": "hepatobiliary",
    "ast": "hepatobiliary",
    "ggt": "hepatobiliary",
    "alp": "hepatobiliary",
    "total_bilirubin": "hepatobiliary",
    "direct_bilirubin": "hepatobiliary",
    "indirect_bilirubin": "hepatobiliary",
    "total_protein": "hepatobiliary",
    "albumin": "hepatobiliary",
    "globulin": "hepatobiliary",
    "albumin_globulin_ratio": "hepatobiliary",
    "prealbumin": "hepatobiliary",
    # ── 肾脏与泌尿：肾功能 + 尿常规 ────────────────────────────────────
    "egfr": "renal_urinary",
    "creatinine": "renal_urinary",
    "uacr": "renal_urinary",
    "urine_protein": "renal_urinary",
    "urine_leucocytes": "renal_urinary",
    "urine_erythrocytes": "renal_urinary",
    "urine_specific_gravity": "renal_urinary",
    "urine_ph": "renal_urinary",
    # ── 血液：血常规与铁代谢 ───────────────────────────────────────────
    "hemoglobin": "hematologic",
    "mcv": "hematologic",
    "ferritin": "hematologic",
    "tsat": "hematologic",
    "wbc": "hematologic",
    "neutrophils_percent": "hematologic",
    "lymphocytes_percent": "hematologic",
    "monocytes_percent": "hematologic",
    "eosinophils_percent": "hematologic",
    "basophils_percent": "hematologic",
    "neutrophils_absolute": "hematologic",
    "lymphocytes_absolute": "hematologic",
    "monocytes_absolute": "hematologic",
    "eosinophils_absolute": "hematologic",
    "basophils_absolute": "hematologic",
    # ── 电解质 ─────────────────────────────────────────────────────────
    "sodium": "electrolyte",
    "potassium": "electrolyte",
    "chloride": "electrolyte",
    "phosphate": "electrolyte",
    "calcium": "electrolyte",
    "corrected_calcium": "electrolyte",
    # ── 骨骼与肌肉 ─────────────────────────────────────────────────────
    "bone_density_t_score": "bone_muscle",
    "25_oh_vitamin_d": "bone_muscle",
    "grip_strength": "bone_muscle",
    "walking_speed": "bone_muscle",
    "muscle_mass": "bone_muscle",
    "ck": "bone_muscle",
    # ── 眼科 ───────────────────────────────────────────────────────────
    "uncorrected_vision": "eye",
    "intraocular_pressure": "eye",
    # ── 专项标志物：器官/病种特异的单项，不进任何面板 ──────────────────
    "psa": "special_marker",
    "rheumatoid_factor": "special_marker",
    # `ldh` 是全身细胞更新酶，无单一系统归属——显式归兜底，而不是猜一个。
    "ldh": "other",
}
