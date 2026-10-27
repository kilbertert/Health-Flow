// 健康数据同意（#269）：状态读取与上传闸门。
//
// 本仓在这一次切片里**没有同意界面**——同意页在商城 H5 模块里（PRD #266），界面那一半
// 不在本仓。能被浏览器证明的是服务端这一半，而且**必须跨源证明**：商城 H5 在另一个源上
// 用 Bearer 调接口，所以这条用例开一张真的商城源页面再发请求（同源请求不经过 CORS，
// 用它证明跨源等于没证明 —— 与 `cors-bearer.spec.js` 同一个理由）。
//
// 三件事，各自对应验收标准的一条：
//   1. 种子代主体授予「报告解读」后，读取状态显示该项在当前版本下有效；
//   2. 没有有效同意时上传被拒，且**患者侧读得到**具名原因头（`X-Report-Access-Reason`）；
//   3. 记录一次同意之后同一主体的上传不再被闸门拦下，撤回之后重新拦下。
import process from 'node:process';

import { test, expect, loginWithSeed } from './fixtures.js';

const apiOrigin = process.env.HEALTHFLOW_E2E_BASE_URL;
const mallOrigin = process.env.HEALTHFLOW_E2E_MALL_ORIGIN;

// 商城源由 `e2e/mall-origin.mjs` 的 globalSetup 起好、活到本次运行结束 —— 本文件不绑
// 端口。跨源 spec 不止一条而 worker 并行，各自监听同一个端口会撞成 `EADDRINUSE`。

/** 从商城源的页面里调 API。`Origin` 由浏览器按页面源填，脚本改不了它。 */
async function callApi(page, requestPath, init) {
  return page.evaluate(
    async ({ apiBase, requestPath: path, requestInit }) => {
      const res = await fetch(`${apiBase}${path}`, requestInit);
      return {
        status: res.status,
        // 只有出现在 `Access-Control-Expose-Headers` 里的头才读得到；其余一律 null。
        accessReason: res.headers.get('X-Report-Access-Reason'),
        body: await res.json().catch(() => null),
      };
    },
    { apiBase: apiOrigin, requestPath, requestInit: init },
  );
}

function bearer(token) {
  return { Authorization: `Bearer ${token}` };
}

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC',
  'base64',
);

/** 跨源 multipart 上传：`FormData` 由页面里的脚本构造，浏览器带令牌与 CORS 预检。 */
async function uploadFromMall(page, token) {
  return page.evaluate(
    async ({ apiBase, authorization, pngBase64 }) => {
      const bytes = Uint8Array.from(atob(pngBase64), (char) => char.charCodeAt(0));
      const form = new FormData();
      form.append('files', new File([bytes], '报告.png', { type: 'image/png' }));
      const res = await fetch(`${apiBase}/api/health/report/upload`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${authorization}` },
        body: form,
      });
      return {
        status: res.status,
        accessReason: res.headers.get('X-Report-Access-Reason'),
        body: await res.json().catch(() => null),
      };
    },
    { apiBase: apiOrigin, authorization: token, pngBase64: TINY_PNG.toString('base64') },
  );
}

function itemOf(body, item) {
  return body.items.find((entry) => entry.item === item);
}

test.describe('健康数据同意', () => {
  test('种子的「报告解读」同意在当前版本下有效，且两项都出现在读取结果里', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    const token = seeded.subject.session_token;
    await page.goto(`${mallOrigin}/`);

    const status = await callApi(page, '/api/auth/consent', { headers: bearer(token) });

    expect(status.status).toBe(200);
    expect(status.body.items.map((entry) => entry.item).sort()).toEqual([
      'product_recommendation',
      'report_interpretation',
    ]);
    const interpretation = itemOf(status.body, 'report_interpretation');
    expect(interpretation).toMatchObject({ decision: 'granted', valid: true, source: 'seed' });
    // 文本版本由服务端给出，患者侧不自己记一份；有效性就是它们相等。
    expect(interpretation.required_text_version).toBeTruthy();
    expect(interpretation.text_version).toBe(interpretation.required_text_version);
    // 两项彼此独立：推荐是可选的一项，种子没有代他勾选。
    expect(itemOf(status.body, 'product_recommendation')).toMatchObject({ decision: null, valid: false });
  });

  test('没有有效的报告解读同意时，跨源上传得到 403 与可读的具名原因', async ({ page, seed }) => {
    const seeded = await seed({ reports: [], consent: [] });
    await page.goto(`${mallOrigin}/`);

    const denied = await uploadFromMall(page, seeded.subject.session_token);

    expect(denied.status).toBe(403);
    // 前端读得到那个头，才可能说「请先完成授权」而不是「上传失败」。
    expect(denied.accessReason).toBe('report_interpretation_consent_required');
    // `detail` 是人类可读的那一句，仍然照旧出域（成因与文案各自为本）。
    expect(denied.body.detail).toContain('报告解读');
  });

  test('记录同意之后同一主体可以上传，撤回后闸门重新关上', async ({ page, seed }) => {
    // 起始份数**读出来**而不是假设：种子脚本在缺省状态下会建出两份报告（已完成 + 待
    // 确认），与这里传的 `reports: []` 无关。撤回删掉的份数 = 起始份数 + 这次上传的 1 份。
    const seeded = await seed({ reports: [], consent: [] });
    const token = seeded.subject.session_token;
    await page.goto(`${mallOrigin}/`);
    const existing = await callApi(page, '/api/auth/reports', { headers: bearer(token) });
    const before = existing.body.length;

    const granted = await callApi(page, '/api/auth/consent', {
      method: 'POST',
      headers: { ...bearer(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ item: 'report_interpretation', decision: 'granted' }),
    });
    expect(granted.status).toBe(200);
    expect(itemOf(granted.body, 'report_interpretation')).toMatchObject({
      decision: 'granted',
      valid: true,
      // 来源由服务端按会话通道判定：带 Bearer 的就是商城 H5。
      source: 'mall_h5',
    });
    // 撤回是又一条记录，不是改写上一条：此刻仍有一条 granted 的旧行。
    expect(itemOf(granted.body, 'report_interpretation').recorded_at).toBeTruthy();

    const accepted = await uploadFromMall(page, token);
    expect(accepted.status).toBe(202);
    expect(accepted.body.status).toBe('processing');

    const revoked = await callApi(page, '/api/auth/consent', {
      method: 'POST',
      headers: { ...bearer(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ item: 'report_interpretation', decision: 'revoked' }),
    });
    expect(itemOf(revoked.body, 'report_interpretation')).toMatchObject({
      decision: 'revoked',
      valid: false,
      // #272：撤回的后果在服务端兑现，记录里写着这次删掉了多少份报告 —— 起始的那些
      // 加刚才上传得到 202 的那一份。
      deleted_report_count: before + 1,
    });

    const reports = await callApi(page, '/api/auth/reports', { headers: bearer(token) });
    expect(reports.status).toBe(200);
    expect(reports.body).toEqual([]);

    const deniedAgain = await uploadFromMall(page, token);
    expect(deniedAgain.status).toBe(403);
    expect(deniedAgain.accessReason).toBe('report_interpretation_consent_required');
  });

  // #272：撤回的结果必须是**患者看得见的**——报告列表空了，旧报告再也打不开，而不是
  // 列表里还挂着一条点进去就报错的记录。这条用例从商城源走一遍，因为患者界面就在那里。
  test('撤回报告解读后，商城模块里的报告列表变空，旧报告打不开', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['assessed', 'pending_confirmation'] });
    const token = seeded.subject.session_token;
    const reportIds = seeded.reports.map((report) => report.id);
    await page.goto(`${mallOrigin}/`);

    const before = await callApi(page, '/api/auth/reports', { headers: bearer(token) });
    expect(before.body).toHaveLength(2);

    const revoked = await callApi(page, '/api/auth/consent', {
      method: 'POST',
      headers: { ...bearer(token), 'Content-Type': 'application/json' },
      body: JSON.stringify({ item: 'report_interpretation', decision: 'revoked' }),
    });
    expect(itemOf(revoked.body, 'report_interpretation').deleted_report_count).toBe(2);

    const after = await callApi(page, '/api/auth/reports', { headers: bearer(token) });
    expect(after.body).toEqual([]);

    for (const reportId of reportIds) {
      const detail = await callApi(page, `/api/health/report/${reportId}`, { headers: bearer(token) });
      // 不是 403 —— 那不是「报告还在、只是没授权」；它已经不存在了。
      expect(detail.status).toBe(404);
      expect(detail.accessReason).toBe('report_not_found');
    }
  });

  // 界面路径：旧前端的报告历史在撤回之后**肉眼可见地空了**。同意页不在本仓，但撤回的
  // 后果会落在患者每天看的那一页上 —— 一条还挂在列表里、点开就报错的记录等于没删。
  test('撤回之后，旧前端的报告历史里一份报告都不剩', async ({ page, seed }) => {
    const seeded = await seed({ reports: ['assessed', 'pending_confirmation'] });
    await loginWithSeed(page, seeded);
    await page.getByRole('button', { name: '个人中心', exact: true }).click();
    await expect(page.getByRole('heading', { name: '报告历史' })).toBeVisible();
    await expect(page.getByRole('button', { name: '查看' })).toHaveCount(2);

    // 撤回页在商城 H5 模块里；这里用同一主体的会话令牌驱动服务端那一半。
    const revoked = await page.evaluate(
      async ({ apiBase, authorization }) => {
        const res = await fetch(`${apiBase}/api/auth/consent`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${authorization}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ item: 'report_interpretation', decision: 'revoked' }),
        });
        return res.json();
      },
      { apiBase: apiOrigin, authorization: seeded.subject.session_token },
    );
    expect(revoked.items.find((entry) => entry.item === 'report_interpretation').deleted_report_count).toBe(2);

    await page.reload();
    await page.getByRole('button', { name: '个人中心', exact: true }).click();
    await expect(page.getByText('还没有报告记录')).toBeVisible();
    await expect(page.getByRole('button', { name: '查看' })).toHaveCount(0);
  });

  test('旧前端（Cookie 会话）在同一道闸门前得到同样的拒绝', async ({ page, seed }) => {
    // 旧前端没有同意页——这正是 PRD 接受的已知后果。它在这里证明的是**闸门不挑通道**：
    // 没有有效同意时，Cookie 与 Bearer 走同一条判定。
    const seeded = await seed({ reports: [], consent: [] });
    await loginWithSeed(page, seeded);
    await page.getByRole('button', { name: '体检报告解读' }).click();
    await expect(page.getByText('点击或拖拽多张报告文件到此区域')).toBeVisible();

    await page.locator('.report-paste-zone input[type="file"]').setInputFiles({
      name: '报告.png',
      mimeType: 'image/png',
      buffer: TINY_PNG,
    });
    await page.getByRole('button', { name: '上传并解析' }).click();

    // 患者看到的是服务端给的那句话（告警与 toast 各一处，说的是同一句话）。
    await expect(page.getByText(/尚未同意「报告解读」授权/).first()).toBeVisible();
    // 被拒之后不能落到「解析完成」的假象里。
    await expect(page.getByText(/已解析\s*\d+\s*个文件/)).toHaveCount(0);
  });
});
