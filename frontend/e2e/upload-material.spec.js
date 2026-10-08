// 报告原始材料的类型判定（E2E）。
//
// 服务端现在按**内容**判定类型（app/service/report_material.py）：把 PNG 改名成
// .pdf 不再被静默放行。这条用例走真实的浏览器 → 真实的 FastAPI（服务里的判定
// 未被 mock）→ 断言患者看到的是明确拒绝，不是「上传成功、查看原文时才炸」。
//
// 阴面对照（写这条时实际跑过）：把闸门里的 `material.mismatch` 判断去掉、
// 只留后缀白名单，本用例立刻变红（HTTP 202 而不是 415）。
import { test, expect, loginWithSeed } from './fixtures.js';

const TINY_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC',
  'base64',
);

async function openUploadPage(page, seeded) {
  await loginWithSeed(page, seeded);
  await page.getByRole('button', { name: '体检报告解读' }).click();
  await expect(page.getByText('点击或拖拽多张报告文件到此区域')).toBeVisible();
}

test('把 PNG 改名成 .pdf：明确告诉患者格式不对，不静默放行', async ({ page, seed }) => {
  const seeded = await seed({ reports: [] });
  await openUploadPage(page, seeded);

  let uploadCalls = 0;
  page.on('request', (request) => {
    if (request.url().includes('/api/health/report/upload')) uploadCalls += 1;
  });

  await page.locator('.report-paste-zone input[type="file"]').setInputFiles({
    name: '报告.pdf',
    mimeType: 'application/pdf',
    buffer: TINY_PNG,
  });
  await expect(page.getByText('报告.pdf')).toBeVisible();

  await page.getByRole('button', { name: '上传并解析' }).click();

  // 服务端 415 + 可读原因。措辞由服务端给出，页面不得把它换成 HTTP 码。
  // 同一条详情会同时出现在页面告警与 toast 里（`.first()` 是两者的选择，不是
  // 对重复的容忍 —— 下面断言它们说的是同一句话）。
  const reasons = page.getByText(/扩展名说的是 PDF.*实际是 PNG 图片/);
  await expect(reasons.first()).toBeVisible();
  await expect(reasons).toHaveCount(2);
  // 拒绝之后不能落到「解析完成」的假象里。
  await expect(page.getByText(/已解析\s*\d+\s*个文件/)).toHaveCount(0);
  expect(uploadCalls).toBeGreaterThan(0);
});

test('内容与后缀相符的 PDF 照常受理', async ({ page, seed }) => {
  const seeded = await seed({ reports: [] });
  await openUploadPage(page, seeded);

  await page.route('**/api/health/report/upload', async (route) => {
    await route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 9002,
        patient_id: seeded.subject.owner_id,
        report_type: '体检',
        department: '',
        created_at: new Date().toISOString(),
        status: 'processing',
        subject_consistency: 'same',
        metrics: [],
        files: [
          {
            file_index: 1,
            original_filename: '体检报告.pdf',
            media_type: 'application/pdf',
            page_count: 3,
            source_url: '/api/health/report/9002/files/1/pages/1',
          },
        ],
        extraction_job: { status: 'queued', attempt_count: 0 },
      }),
    });
  });

  await page.locator('.report-paste-zone input[type="file"]').setInputFiles({
    name: '体检报告.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4\n%%EOF\n'),
  });
  await page.getByRole('button', { name: '上传并解析' }).click();

  // 受理路径没有被这条新闸门误伤。
  await expect(page.getByText('体检报告.pdf')).toBeVisible();
  await expect(page.getByText(/扩展名说的是/)).toHaveCount(0);
});
