// 重复 PRD 的判据（#133）。
//
// 背景：architecture-review 曾在同一次运行里把同一份 PRD 提交两遍（#90/#91、
// #109/#110、#92/#93 三对），而复查"已提过什么"靠的是 `source:architecture-review`
// 标签 —— 其中一份没拿到标签时就看不见自己刚创建的那份。
//
// 所以判据是：**正文归一化后相等，且至少一份带来源标签**。「至少一份」是关键 ——
// 今天缺标签的那一份正是被漏掉的那份。
//
// 这个函数只报告，不修：删 issue 是不可逆动作，人去决定保留哪一份。

export function normalizeBody(body) {
  return String(body ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "")
    .join("\n");
}

export function findDuplicatePrds(issues, label = "source:architecture-review") {
  const labelled = issues.filter((issue) => (issue.labels ?? []).includes(label));
  const byBody = new Map();
  for (const issue of issues) {
    const key = normalizeBody(issue.body);
    if (!key) continue;
    byBody.set(key, [...(byBody.get(key) ?? []), issue]);
  }
  const duplicates = [];
  for (const group of byBody.values()) {
    if (group.length < 2) continue;
    // 至少一份带标签 —— 两份都没标签时它们不是「这条链路的产物」，不在此列。
    const survivors = group.filter((issue) => labelled.includes(issue));
    if (survivors.length === 0) continue;
    duplicates.push({
      keep: survivors[0].number,
      drop: group.filter((issue) => issue.number !== survivors[0].number).map((issue) => issue.number),
    });
  }
  return duplicates;
}
