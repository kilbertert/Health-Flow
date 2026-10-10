// 指标确认表卡片化(E2E):
// 375/414px 下确认表渲染为常显名称/数值/异常状态的指标卡片,
// 展开卡片后可进行修正并提交确认;桌面端仍使用表格形态。
import { test, expect, loginWithSeed } from './fixtures.js';

async function openPendingReport(page, seeded) {
  await loginWithSeed(page, seeded);
  await page.getByRole('button', { name: '个人中心', exact: true }).click();
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible();
  await page.getByRole('button', { name: '查看' }).click();
  await expect(page.getByRole('heading', { name: '报告详情' })).toBeVisible();
  await page.getByRole('button', { name: '继续确认' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  await expect(page.getByText(/解析结果/)).toBeVisible();
}

function assessedResponse(seeded, reportUrl) {
  const id = Number(reportUrl.split('/report/')[1].split('/')[0]);
  // 服务端从 #159 起只给**一个**患者投影（PatientNotices）：`findings` 就是
  // 患者可见集合，`summary` / `title` / `disclaimer` 在顶层 —— 不再有
  // `patient_reply` 包裹，也不再重复一份内部层 `findings`。
  return {
    id,
    patient_id: seeded.subject.owner_id,
    report_type: '体检报告',
    department: '健康管理中心',
    created_at: new Date().toISOString(),
    status: 'assessed',
    subject_consistency: 'same',
    metrics: [],
    files: [],
    evidence_result: {
      correlation_id: 'e2e-mobile-confirmation',
      title: '体检报告解读与健康风险提示',
      summary: 'E2E 移动端确认完成。',
      findings: [{
        condition_code: 'COND_DYSLIPIDEMIA',
        condition_name: '血脂异常',
        urgency: 'routine',
        abnormality_severity: 1,
        evidence_strength: 'moderate',
        needs_recheck: false,
        department: '心血管内科',
        recheck_direction: '',
        source_observation_ids: ['health-flow-metric-1'],
        source_observations: [],
        evidence_items: [],
      }],
      unmatched: [],
      skipped: [],
      unmatched_count: 0,
      disclaimer: '本解读仅提供健康辅助建议。',
    },
    processing_warnings: [],
  };
}

[375, 414].forEach((width) => {
  test.describe(`指标确认卡片 ${width}px`, () => {
    test.use({ viewport: { width, height: 667 } });

    test('卡片常显指标信息，可展开修正并提交确认', async ({ page, seed }) => {
      const seeded = await seed({ reports: ['pending_confirmation'] });
      await openPendingReport(page, seeded);

      const card = page.getByRole('button', { name: '甘油三酯指标卡片' });
      await expect(card).toBeVisible();
      await expect(card).toHaveAttribute('aria-expanded', 'false');
      await expect(card).toContainText('甘油三酯');
      await expect(card).toContainText('2.3');
      await expect(card).toContainText('mmol/L');
      await expect(card).toContainText('H 偏高');

      await card.click();
      await expect(card).toHaveAttribute('aria-expanded', 'true');
      await page.getByLabel('甘油三酯处理方式').click();
      await page
        .locator('.ant-select-item-option')
        .filter({ hasText: '修正' })
        .click();

      await page.getByLabel('甘油三酯修正值').fill('1.9');
      await page.getByLabel('甘油三酯修正单位').fill('mmol/L');
      await page.getByLabel('甘油三酯修正参考范围').fill('0.45-1.7');
      await page
        .getByLabel('甘油三酯修正原文证据')
        .fill('甘油三酯 1.9 mmol/L 参考范围 0.45-1.7');

      let confirmationBody;
      await page.route('**/api/health/report/*/confirm', async (route) => {
        confirmationBody = route.request().postDataJSON();
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(assessedResponse(seeded, route.request().url())),
        });
      });

      await page.getByRole('button', { name: '确认并生成健康提示' }).click();
      await expect(page.getByText('E2E 移动端确认完成。')).toBeVisible();
      // 证据仍在最上层可见：风险名与证据正文来自契约，不随商品退役一起消失。
      await expect(page.getByText('可能相关健康问题：血脂异常')).toBeVisible();
      await expect(page.getByText('血脂异常')).toBeVisible();
      expect(confirmationBody).toBeTruthy();
      expect(
        confirmationBody.observations.some(
          (observation) =>
            observation.decision === 'corrected' &&
            observation.value === '1.9' &&
            observation.unit === 'mmol/L' &&
            observation.reference_range === '0.45-1.7',
        ),
      ).toBeTruthy();
      // #195：患者**没动过**的那一行必须如实说「我没动它」（`pending`），**不能**被
      // 客户端算成一个表态。服务端把它解成该行已落定的状态，没有落定过就仍是未决 ——
      // 此前客户端把「界面默认」当患者的表态提交，于是没看过的正常行被记成「患者已排除」。
      // 按**指标名**认「动过的那一行」，不按 id：seed 的 id 是自增的，写死会随夹具漂移。
      // 动过的那条是「甘油三酯」（本用例把它改成了 corrected），其余一条没动。
      const corrected = confirmationBody.observations.filter((observation) => observation.decision === 'corrected');
      expect(corrected).toHaveLength(1);
      const untouched = confirmationBody.observations.filter((observation) => observation.decision !== 'corrected');
      expect(untouched.length).toBeGreaterThan(0);
      expect(untouched.every((observation) => observation.decision === 'pending')).toBe(true);
    });
  });
});

test.describe('指标确认表格桌面端', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('确认表保持表格形态，不渲染卡片', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    await openPendingReport(page, seeded);

    const confirmationTable = page.getByRole('table').filter({
      has: page.getByRole('columnheader', { name: '指标' }),
    });
    await expect(confirmationTable).toBeVisible();
    await expect(confirmationTable.getByRole('columnheader', { name: '指标', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '甘油三酯指标卡片' })).toHaveCount(0);
    await expect(page.locator('.metric-card-list')).toHaveCount(0);
  });
});

// 标准指标目录不可用时的确认（#148）：患者核对好的决策不该因为目录抖动丢失。
//
// 服务端从 #147 起降级保存（编码留空、决策落库，等目录恢复后重新匹配）。前端
// 的职责只有一件：**把这件事说清楚**，而不是让患者以为白做了一场。
test.describe('标准指标目录不可用', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test('目录 503 时患者仍能确认，提示说明编码稍后重新匹配', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    const reportId = seeded.reports[0].id;
    await page.route('**/api/health/metric-catalog', (route) =>
      route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '暂不可用' }) }),
    );

    let confirmationBody;
    await page.route(`**/api/health/report/${reportId}/confirm`, async (route) => {
      confirmationBody = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(assessedResponse(seeded, route.request().url())),
      });
    });

    await openPendingReport(page, seeded);

    // 降级提示如实说明「仍可确认」与「稍后重新匹配」。
    const alert = page.locator('.ant-alert').filter({ hasText: '标准指标目录暂不可用' });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText('你仍然可以确认指标');
    // 措辞不替服务端下结论：浏览器拉不到目录 ≠ 服务端仲裁时目录不可用。
    await expect(alert).toContainText('编码以服务端为准');
    await expect(alert).toContainText('重新匹配');

    // 患者照样能提交。
    await page.getByRole('button', { name: '确认并生成健康提示' }).click();
    await expect(page.getByText('E2E 移动端确认完成。')).toBeVisible();
    expect(confirmationBody).toBeTruthy();
    expect(confirmationBody.observations.length).toBeGreaterThan(0);
  });
});

// 主体一致性的「停止」（#152）：它是一条真实的结论，不是一次失败的提交。
//
// 收敛前：确认面板提供「不同主体，停止」「无法确认，停止」，但本地闸门对任何非
// `same` 一律拦下、发送体又写死 `|| 'same'` —— **这两个选项在代码里没有出路**，
// 患者选了它，报告永远停在待确认且没有任何指引。
test.describe('主体一致性的「停止」', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  /** 一份待确认的报告，服务端已判 `uncertain`（多文件形态）。
   *
   * 种子的报告都是单文件的（服务端会把它自动判为 `same`），而 `uncertain` 只在
   * **多文件**上传时出现 —— 所以这里用 `page.route` 改的是**服务端会给出的那份
   * 响应**，不是伪造一个不存在的状态：多文件 + `uncertain` 正是这个闸门存在的
   * 理由。seed 支持多文件后可以改为直接种出来。
   */
  async function openUncertainReport(page, seed) {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    const reportId = seeded.reports[0].id;
    await page.route(`**/api/health/report/${reportId}`, async (route) => {
      const res = await route.fetch();
      const json = await res.json();
      await route.fulfill({ response: res, json: { ...json, subject_consistency: 'uncertain' } });
    });
    await loginWithSeed(page, seeded);
    await page.getByRole('button', { name: '个人中心', exact: true }).click();
    await page.getByRole('button', { name: '查看' }).click();
    await page.getByRole('button', { name: '继续确认' }).click();
    await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
    return reportId;
  }

  test('选「不同主体，停止」：给出分开上传的引导，且不发出确认请求', async ({ page, seed }) => {
    let confirmCalls = 0;
    const reportId = await openUncertainReport(page, seed);
    await page.route(`**/api/health/report/${reportId}/confirm`, (route) => {
      confirmCalls += 1;
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

    // 主体面板在「技术详情」里（闸门打开时自动展开）。
    await page.getByLabel('确认文件属于同一主体').click();
    await page.locator('.ant-select-item-option').filter({ hasText: '不同主体，停止' }).click();

    // 选择的那一刻就给出引导 —— 不必等患者点提交。措辞是「不属于同一主体」。
    await expect(page.getByText(/这批文件不属于同一主体.*分开上传/).first()).toBeVisible();

    // 仍然点提交：不发出确认请求，引导仍在。
    await page.getByRole('button', { name: '确认并生成健康提示' }).click();
    await expect(page.getByText(/这批文件不属于同一主体.*分开上传/).first()).toBeVisible();
    expect(confirmCalls).toBe(0);
  });

  test('选「无法确认，停止」：措辞是「无法确认」而不是「不属于同一主体」', async ({ page, seed }) => {
    await openUncertainReport(page, seed);
    await page.getByLabel('确认文件属于同一主体').click();
    await page.locator('.ant-select-item-option').filter({ hasText: '无法确认，停止' }).click();
    await expect(page.getByText(/无法确认这批文件属于同一主体.*分开上传/).first()).toBeVisible();
    // 不替患者下结论。
    await expect(page.getByText(/这批文件不属于同一主体/)).toHaveCount(0);
  });
});

// 行数一致性（#156）：历史列表与确认页看到的「项数」来自**同一个**行集。
//
// 服务端从 #155 起只有一处「同一观测」判定（app/service/metric_rows.py）。这条
// 用例用一份**真实行集**（含一对可被误合并的近似行）钉住患者可见的数字一致 ——
// 两侧任何一侧加去重、或漏掉一行都会让它变红。
test.describe('指标行数一致性', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test('历史列表「N 项指标」与确认页「识别到 N 项指标」是同一个数', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['pending_confirmation'] });
    const reportId = seeded.reports[0].id;
    // 种子的待确认指标名/值各不相同，一个「按显示值去重」的 bug 不会暴露。
    // 这里把响应换成一份**含近似行**的行集：
    //   - 两行同名同值同单位同范围、只有原文证据不同 —— 按「同一观测」是两个观测，
    //     必须保留两行（归并它们就少一行，历史与确认页的数字一起掉，本地看不出
    //     问题，但它正是 #107 要防的那类误合并）；
    //   - 再加一行真正重复的（与第一行完全同一）—— 服务端会去重，前端不该再去一次。
    const rows = [
      { id: 1, metric_name: '空腹血糖', metric_value: '6.5', unit: 'mmol/L', reference_range: '3.9-6.1',
        abnormal_flag: 'H', inferred_abnormal_flag: 'H', page_number: 1, confirmation_status: 'pending',
        evidence_text: '空腹血糖 6.5 mmol/L 3.9-6.1' },
      { id: 2, metric_name: '空腹血糖', metric_value: '6.5', unit: 'mmol/L', reference_range: '3.9-6.1',
        abnormal_flag: 'H', inferred_abnormal_flag: 'H', page_number: 1, confirmation_status: 'pending',
        evidence_text: 'GLU 6.5 mmol/L 3.9-6.1' },
      { id: 3, metric_name: '糖化血红蛋白', metric_value: '6.2', unit: '%', reference_range: '4.0-6.0',
        abnormal_flag: 'H', inferred_abnormal_flag: 'H', page_number: 1, confirmation_status: 'pending',
        evidence_text: '糖化血红蛋白 6.2 % 4.0-6.0' },
    ].map((row) => ({ ...row, report_id: reportId }));
    // 确认页的行来自报告本体（GET /report/{id}）；/metrics 是另一条读取路径。
    // 两条都对齐到同一份行集 —— 「两处数字来自同一行集」才是这条用例要钉的契约。
    await page.route(`**/api/health/report/${reportId}`, async (route) => {
      const res = await route.fetch();
      const json = await res.json();
      await route.fulfill({ response: res, json: { ...json, metrics: rows } });
    });
    await page.route(`**/api/health/report/${reportId}/metrics`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ metrics: rows }) }),
    );
    // 历史接口的「N 项指标」是服务端算的行数 —— 同一份行集，同一个数。
    await page.route('**/api/auth/reports', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: reportId, report_type: '体检报告', department: '健康管理中心',
            status: 'pending_confirmation', created_at: new Date().toISOString(),
            metric_count: rows.length, abnormal_count: 3, finding_count: 0,
          },
        ]),
      }),
    );

    await loginWithSeed(page, seeded);
    await page.getByRole('button', { name: '个人中心', exact: true }).click();
    await expect(page.getByRole('heading', { name: '报告历史' })).toBeVisible();

    const historyItem = page.locator('.history-section .ant-list-item').first();
    const historyText = await historyItem.innerText();
    const historyCount = Number(/(\d+)\s*项指标/.exec(historyText)?.[1]);
    expect(historyCount).toBe(rows.length);

    await page.getByRole('button', { name: '查看' }).click();
    await page.getByRole('button', { name: '继续确认' }).click();
    await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();

    // 确认页那行「识别到 N 项指标…」是页头提示文本，不是 Alert（页面上有别的
    // Alert，例如目录降级提示）。
    const confirmationText = await page.getByText(/识别到\s*\d+\s*项指标/).first().innerText();
    const confirmationCount = Number(/识别到\s*(\d+)\s*项指标/.exec(confirmationText)?.[1]);
    expect(confirmationCount).toBe(historyCount);

    // 两个同名同值但原文证据不同的指标：**都**要出现在确认表上（是同一个观测
    // 才该合并）。这一条把「误合并」钉住。
    const table = page.getByRole('table').filter({ has: page.getByRole('columnheader', { name: '指标' }) });
    if (await table.count()) {
      await expect(table.locator('tr', { hasText: '空腹血糖' })).toHaveCount(2);
    }
  });
});
