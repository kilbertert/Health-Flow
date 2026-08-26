// 图片粘贴核心(E2E):
// 桌面端通过合成粘贴事件把剪贴板图片注入待上传列表,
// 并验证命名、非图片忽略、text/html data:image、不支持的格式与输入框防劫持。
import { test, expect } from './fixtures.js';

const TINY_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC';

async function login(page, account) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: '欢迎回来' })).toBeVisible();
  await page.getByLabel('邮箱').fill(account.email);
  await page.getByLabel('密码').fill(account.password);
  await page.getByRole('button', { name: /^登\s*录$/ }).click();
  await expect(page.getByRole('heading', { name: '呵护您的健康' })).toBeVisible();
}

async function openUploadPage(page, account) {
  await login(page, account);
  await page.getByRole('button', { name: '体检报告解读' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  await expect(page.getByText('点击或拖拽多张报告文件到此区域')).toBeVisible();
}

function uploadResponse(account, files) {
  return {
    id: 9001,
    patient_id: account.id,
    report_type: '体检',
    department: '',
    created_at: new Date().toISOString(),
    status: 'pending_confirmation',
    subject_consistency: files.length === 1 ? 'same' : 'uncertain',
    metrics: [],
    files: files.map((file, index) => ({
      file_index: index + 1,
      original_filename: file.name,
      media_type: file.type,
      page_count: 1,
      source_url: `/api/health/report/9001/files/${index + 1}/pages/1`,
    })),
    processing_warnings: [],
  };
}

async function dispatchPaste(locator, { files = [], textHtml = '', plainText = '' } = {}) {
  await locator.evaluate((element, options) => {
    const data = new DataTransfer();
    for (const file of options.files) {
      const bytes = Uint8Array.from(atob(file.base64), (char) => char.charCodeAt(0));
      data.items.add(new File([bytes], file.name, { type: file.type }));
    }
    if (options.textHtml) data.setData('text/html', options.textHtml);
    if (options.plainText) data.setData('text/plain', options.plainText);

    const event = document.createEvent('Event');
    event.initEvent('paste', true, true);
    try {
      Object.defineProperty(event, 'clipboardData', { get: () => data });
    } catch {
      // Chromium 的合成 Event 可能已有只读 clipboardData;若无此属性则无法覆盖。
    }
    element.dispatchEvent(event);
  }, { files, textHtml, plainText });
}

test.describe('报告图片粘贴', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('桌面粘贴生成 MIME 对应扩展名的文件项', async ({ page, seed }) => {
    const { account } = await seed({ reports: [] });
    await openUploadPage(page, account);
    const zone = page.locator('.report-paste-zone');

    await dispatchPaste(zone, {
      files: [{ name: 'copied.png', type: 'image/png', base64: TINY_PNG_BASE64 }],
    });
    await expect(page.getByText(/粘贴-\d+\.png/)).toBeVisible();

    await dispatchPaste(zone, {
      files: [{ name: 'copied.jpg', type: 'image/jpeg', base64: TINY_PNG_BASE64 }],
    });
    await expect(page.getByText(/粘贴-\d+\.jpg/)).toBeVisible();
  });

  test('非图片剪贴物被忽略并轻提示', async ({ page, seed }) => {
    const { account } = await seed({ reports: [] });
    await openUploadPage(page, account);
    const zone = page.locator('.report-paste-zone');

    await dispatchPaste(zone, { plainText: '这是一段普通文本' });
    await expect(page.getByText('剪贴板中没有可粘贴的图片')).toBeVisible();
    await expect(page.locator('.ant-upload-list-item')).toHaveCount(0);
  });

  test('识别 text/html 中的 data:image base64', async ({ page, seed }) => {
    const { account } = await seed({ reports: [] });
    await openUploadPage(page, account);
    const zone = page.locator('.report-paste-zone');

    await dispatchPaste(zone, {
      textHtml: `<div><img src="data:image/png;base64,${TINY_PNG_BASE64}"></div>`,
    });
    await expect(page.getByText(/粘贴-\d+\.png/)).toBeVisible();
  });

  test('webp/heic 剪贴物提示暂不支持', async ({ page, seed }) => {
    const { account } = await seed({ reports: [] });
    await openUploadPage(page, account);
    const zone = page.locator('.report-paste-zone');

    for (const type of ['image/webp', 'image/heic']) {
      const extension = type.split('/')[1];
      await dispatchPaste(zone, {
        files: [{ name: `copied.${extension}`, type, base64: TINY_PNG_BASE64 }],
      });
      await expect(page.getByText('暂不支持').last()).toBeVisible();
      await expect(page.locator('.ant-upload-list-item')).toHaveCount(0);
    }
  });

  test('粘贴图片与已选文件一起进入上传请求', async ({ page, seed }) => {
    const { account } = await seed({ reports: [] });
    await openUploadPage(page, account);

    await page.locator('.report-paste-zone input[type="file"]').setInputFiles({
      name: 'existing.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4 healthflow e2e'),
    });
    await expect(page.getByText('existing.pdf')).toBeVisible();

    await dispatchPaste(page.locator('.report-paste-zone'), {
      files: [{ name: 'copied.png', type: 'image/png', base64: TINY_PNG_BASE64 }],
    });
    const pastedItem = page.locator('.ant-upload-list-item').filter({ hasText: /粘贴-\d+\.png/ });
    await expect(pastedItem).toBeVisible();
    const pastedName = (await pastedItem.innerText()).match(/粘贴-\d+\.png/)?.[0];
    expect(pastedName).toBeTruthy();

    let uploadBody;
    await page.route('**/api/health/report/upload', async (route) => {
      uploadBody = route.request().postDataBuffer();
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify(uploadResponse(account, [
          { name: 'existing.pdf', type: 'application/pdf' },
          { name: pastedName, type: 'image/png' },
        ])),
      });
    });

    await page.getByRole('button', { name: '上传并解析' }).click();
    await expect(page.getByText(/已解析 2 个文件/)).toBeVisible();
    expect(Buffer.isBuffer(uploadBody)).toBeTruthy();
    expect(uploadBody.includes(Buffer.from('existing.pdf'))).toBeTruthy();
    expect(uploadBody.includes(Buffer.from(pastedName))).toBeTruthy();
  });

  test('普通输入框粘贴不会进入上传列表', async ({ page, seed }) => {
    const { account } = await seed({ reports: [] });
    await openUploadPage(page, account);

    const departmentInput = page.getByLabel('科室');
    await departmentInput.fill('桌面输入框');
    await departmentInput.evaluate((input) => {
      const data = new DataTransfer();
      const bytes = Uint8Array.from(atob('aGVsbG8='), (char) => char.charCodeAt(0));
      data.items.add(new File([bytes], 'should-not-upload.png', { type: 'image/png' }));
      const event = document.createEvent('Event');
      event.initEvent('paste', true, true);
      try {
        Object.defineProperty(event, 'clipboardData', { get: () => data });
      } catch {
        // 忽略不可覆盖的合成事件属性。
      }
      input.dispatchEvent(event);
    });

    await expect(page.locator('.ant-upload-list-item')).toHaveCount(0);
    await expect(departmentInput).toHaveValue('桌面输入框');
  });

  test('无法读取剪贴板时提示选择文件', async ({ page, seed }) => {
    const { account } = await seed({ reports: [] });
    await openUploadPage(page, account);

    await page.locator('.report-paste-zone').evaluate((element) => {
      element.dispatchEvent(new Event('paste', { bubbles: true, cancelable: true }));
    });
    await expect(page.getByText('当前环境不支持粘贴，请选择文件')).toBeVisible();
  });
});
