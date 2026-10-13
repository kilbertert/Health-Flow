// 回归：报告把判据印成**同一个界**的行，确认页不该要求患者「修正数值」（#235 的第三类）。
//
// 病根：`Rheumatoid Factor <20 IU/ml (<20)` 的值与它自己的参考范围是同一个界 —— 报告
// 说的是「低于这个界」，而那个界就是它印的参考上界。系统一见 `<` 就判「数值不是一个数
// （如带符号或混入文字），需要修正」，而报告上**根本没有精确值可填**：患者只能编一个。
//
// 这一条是**前端判定**（确认页在提交之前就要说明白，那时服务端还没有准入结论），所以它
// 有一个真实浏览器里可断言的面。另外两类（比值/标度值缺单位、编码解析）是**服务端**判定，
// 由 `tests/test_admission.py` 与 `tests/test_metric_code_resolution.py` 钉住 —— 在确认页
// 上把服务端的结论 mock 出来再断言，只会测到那个 mock。
import { test, expect, loginWithSeed } from './fixtures.js';

// 报告给了同一个界 → 判得出（值没有坏）。
//
// `abnormal_flag: 'H'` 是**刻意**的：确认页的「值用不了」是从「模型宣称异常、而服务端判不
// 出一个数」反推出来的。两行都让它宣称异常，于是这一条测的是**判定本身**，而不是「某一行
// 刚好没被标异常」。
const SAME_BOUND = {
  id: 2201,
  metric_name: 'Rheumatoid Factor',
  metric_value: '<20',
  unit: 'IU/ml',
  reference_range: '(<20)',
  abnormal_flag: 'H',
  page_number: 1,
  evidence_text: 'Rheumatoid Factor 类风湿因子 <20 IU/ml (<20)',
  confirmation_status: null,
  inferred_abnormal_flag: null,
};

// 负控：参考范围里**没有**界（影射到哪个阈值是医学判断）—— 仍然要患者核对，标记照挂。
const NO_BOUND = {
  ...SAME_BOUND,
  id: 2202,
  metric_name: 'AIP',
  metric_value: '< 0.01',
  unit: null,
  reference_range: null,
  evidence_text: 'AIP < 0.01',
};

const METRICS = [SAME_BOUND, NO_BOUND];
const MARKER = '数值不是一个数（如带符号或混入文字），需要修正';

function json(route, body) {
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function openPendingReport(page, seeded) {
  await loginWithSeed(page, seeded);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await page.getByRole('button', { name: '继续确认' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  // 上传页的标题**也是**「体检报告解读」—— 区分靠这句只有确认流程才有的文本，否则
  // 导航没成功时上面那条断言照样通过，测试会对着上传页断言而「假通过」。
  await expect(page.getByText(/解析结果/)).toBeVisible();
  // 确认页默认只显示异常候选项与未映射项。本用例的两行都不是异常候选，所以先打开
  // 「显示全部指标」—— 要断言的是**每一行自己卡上的标签**，与它是否被优先显示无关。
  // antd 的 Switch 没有可访问名（「显示全部指标」是它旁边的兄弟节点），所以按结构取。
  await page.locator('.ant-switch').first().click();
}

test('「报告给了同一个界」的行不再要求患者修正，而参考范围里没有界的行仍然要求', async ({ page, seed }) => {
  test.setTimeout(90_000);
  const seeded = await seed({ reports: ['pending_confirmation'] });
  const report = {
    id: seeded.reports[0].id,
    patient_id: seeded.subject.owner_id,
    report_type: '体检报告',
    department: '健康管理中心',
    created_at: '2020-01-01T00:00:00.000Z',
    status: 'pending_confirmation',
    subject_consistency: 'same',
    metrics: METRICS,
    files: [],
  };
  await page.route(new RegExp(`/api/health/report/${report.id}/metrics`), (route) =>
    json(route, { metrics: METRICS }),
  );
  await page.route(new RegExp(`/api/health/report/${report.id}$`), (route) => json(route, report));

  await openPendingReport(page, seeded);

  // 修好的那一行：卡上不该有那句要求他动手的话。
  const fixed = page.getByRole('button', { name: 'Rheumatoid Factor指标卡片' });
  await expect(fixed).toBeVisible();
  await expect(fixed.getByText(MARKER)).toHaveCount(0);

  // 负控：AIP 的参考范围里没有界，判定不该替它猜一个 → 标记仍在（且**只有**它一处）。
  const control = page.getByRole('button', { name: 'AIP指标卡片' });
  await expect(control.getByText(MARKER)).toBeVisible();
  await expect(page.getByText(MARKER)).toHaveCount(1);
});
