// 图片粘贴(E2E):
// 桌面端通过合成粘贴事件把剪贴板图片注入待上传列表,
// 移动端验证粘贴按钮/长按聚焦隐藏可编辑区、不滚动聚焦与进列表。
import { test, expect, loginWithSeed } from './fixtures.js';

const TINY_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC';

async function openUploadPage(page, seeded) {
  await loginWithSeed(page, seeded);
  await page.getByRole('button', { name: '体检报告解读' }).click();
  await expect(page.getByRole('heading', { name: '体检报告解读' })).toBeVisible();
  await expect(page.getByText('点击或拖拽多张报告文件到此区域')).toBeVisible();
}

function uploadResponse(seeded, files) {
  return {
    id: 9001,
    patient_id: seeded.subject.owner_id,
    report_type: '体检',
    department: '',
    created_at: new Date().toISOString(),
    status: 'pending_confirmation',
    // 这里不再手抄上传时的初始判定规则（单文件 same / 多文件 uncertain）。
    // 那条规则属于服务端（app/service/report_subject.py 的 initial_consistency）。
    // 本用例只关心粘贴入列，用 `same` 让闸门保持关闭即可。
    subject_consistency: 'same',
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

async function dispatchPaste(
  locator,
  { files = [], items = [], noFileItems = [], textHtml = '', plainText = '' } = {},
) {
  await locator.evaluate((element, options) => {
    const data = new DataTransfer();
    // `items` 与 `files` 是**两条真实路径**：items 走 `clipboardData.items`，
    // 而浏览器在 items 路径上给的是没有文件名的 blob（`File.name` 为空串）。
    // 桌面拖拽/复制图片通常走 files，粘贴截图通常走 items。
    for (const file of options.files) {
      const bytes = Uint8Array.from(atob(file.base64), (char) => char.charCodeAt(0));
      data.items.add(new File([bytes], file.name, { type: file.type }));
    }
    for (const item of options.items) {
      const bytes = Uint8Array.from(atob(item.base64), (char) => char.charCodeAt(0));
      data.items.add(new File([bytes], item.name, { type: item.type }));
    }
    if (options.textHtml) data.setData('text/html', options.textHtml);
    if (options.plainText) data.setData('text/plain', options.plainText);

    // `noFileItems` 走一个**手写的 clipboardData**：`DataTransfer.items.add()`
    // 只收 File，而这几条要覆盖的正是「`getAsFile()` 返回 null」的剪贴项 ——
    // 真实存在（浏览器在拿不到内容时就是这么给的），也是分类必须照常回答的形状。
    const clipboardData = options.noFileItems.length
      ? {
          items: options.noFileItems.map((item) => ({
            kind: 'file',
            type: item.type,
            getAsFile: () => null,
          })),
          files: [],
          getData: () => '',
        }
      : data;

    const event = document.createEvent('Event');
    event.initEvent('paste', true, true);
    try {
      Object.defineProperty(event, 'clipboardData', { get: () => clipboardData });
    } catch {
      // Chromium 的合成 Event 可能已有只读 clipboardData;若无此属性则无法覆盖。
    }
    element.dispatchEvent(event);
  }, { files, items, noFileItems, textHtml, plainText });
}

test.describe('报告图片粘贴', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('桌面粘贴保留源文件名，剪贴板 blob 生成可回溯的名字', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);
    const zone = page.locator('.report-paste-zone');

    // 有源文件名就**保留它** —— 词条「报告原始材料」承诺保留文件名，走粘贴与走
    // 选择文件拿到的 `original_filename` 不能是两种语义。
    await dispatchPaste(zone, {
      files: [{ name: 'copied.png', type: 'image/png', base64: TINY_PNG_BASE64 }],
    });
    await expect(page.getByText('copied.png')).toBeVisible();
    await expect(page.locator('.ant-upload-list-item').filter({ hasText: /copied\.png/ })).toHaveCount(1);

    // 没有源文件名的（items 路径拿不到名字）才生成一个：序号 + 扩展名，可回溯。
    await dispatchPaste(zone, {
      items: [{ name: '', type: 'image/jpeg', base64: TINY_PNG_BASE64 }],
    });
    await expect(page.getByText(/粘贴-\d+-\d+\.jpg/)).toBeVisible();
  });

  test('粘贴项的扩展名跟着服务端下发的受理集合', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    // 服务端把受理集合收成只有 PNG：前端下一份 JPG 立刻不受理（判据只有一处）。
    await page.route('**/api/health/upload-policy', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          accepted_extensions: ['.png'],
          max_files: 20,
          max_file_bytes: 20971520,
          max_total_bytes: 52428800,
        }),
      }),
    );
    await openUploadPage(page, seeded);
    const zone = page.locator('.report-paste-zone');

    await dispatchPaste(zone, {
      files: [{ name: 'copied.jpg', type: 'image/jpeg', base64: TINY_PNG_BASE64 }],
    });
    await expect(page.getByText(/暂不支持 JPG 格式/)).toBeVisible();
    await expect(page.locator('.ant-upload-list-item')).toHaveCount(0);

    await dispatchPaste(zone, {
      files: [{ name: 'copied.png', type: 'image/png', base64: TINY_PNG_BASE64 }],
    });
    await expect(page.getByText('copied.png')).toBeVisible();
  });

  test('拿不到内容的剪贴项：不受理的说格式，受理的说「没有可粘贴的图片」', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);
    const zone = page.locator('.report-paste-zone');

    // `getAsFile()` 返回 null 的剪贴项（浏览器里真实存在）。分类照常回答，
    // 于是提示仍然是「说清是什么格式」，而不是笼统的「暂不支持」。
    await dispatchPaste(zone, { noFileItems: [{ type: 'image/webp' }] });
    await expect(page.getByText(/暂不支持 WEBP 格式/)).toBeVisible();
    await expect(page.locator('.ant-upload-list-item')).toHaveCount(0);

    // 受理的类型却拿不到内容：不能谎报「格式不支持」。
    await dispatchPaste(zone, { noFileItems: [{ type: 'image/png' }] });
    await expect(page.getByText('剪贴板中没有可粘贴的图片')).toBeVisible();
    await expect(page.getByText(/暂不支持/)).toHaveCount(0);
  });

  test('上传区文案与 accept 跟着服务端下发的策略', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await page.route('**/api/health/upload-policy', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          accepted_extensions: ['.pdf', '.png'],
          max_files: 3,
          max_file_bytes: 1048576,
          max_total_bytes: 52428800,
        }),
      }),
    );
    await openUploadPage(page, seeded);

    // 运维者调大/调小之后不需要再发一次前端版本。
    await expect(page.locator('.report-paste-zone input[type="file"]')).toHaveAttribute('accept', '.pdf,.png');
    await expect(page.getByText(/最多 3 个文件，每个不超过 1MB/)).toBeVisible();
  });

  test('非图片剪贴物被忽略并轻提示', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);
    const zone = page.locator('.report-paste-zone');

    await dispatchPaste(zone, { plainText: '这是一段普通文本' });
    await expect(page.getByText('剪贴板中没有可粘贴的图片')).toBeVisible();
    await expect(page.locator('.ant-upload-list-item')).toHaveCount(0);
  });

  test('识别 text/html 中的 data:image base64', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);
    const zone = page.locator('.report-paste-zone');

    await dispatchPaste(zone, {
      textHtml: `<div><img src="data:image/png;base64,${TINY_PNG_BASE64}"></div>`,
    });
    // data:image **拿不到源文件名**（剪贴板里只有 base64），所以这里是生成名。
    await expect(page.getByText(/粘贴-\d+-\d+\.png/)).toBeVisible();
  });

  test('webp/heic 剪贴物提示暂不支持', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);
    const zone = page.locator('.report-paste-zone');

    for (const type of ['image/webp', 'image/heic']) {
      const extension = type.split('/')[1];
      await dispatchPaste(zone, {
        files: [{ name: `copied.${extension}`, type, base64: TINY_PNG_BASE64 }],
      });
      await expect(page.getByText(`暂不支持 ${extension.toUpperCase()} 格式`)).toBeVisible();
      await expect(page.locator('.ant-upload-list-item')).toHaveCount(0);
    }
  });

  test('粘贴图片与已选文件一起进入上传请求', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);

    await page.locator('.report-paste-zone input[type="file"]').setInputFiles({
      name: 'existing.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4 healthflow e2e'),
    });
    await expect(page.getByText('existing.pdf')).toBeVisible();

    await dispatchPaste(page.locator('.report-paste-zone'), {
      files: [{ name: 'copied.png', type: 'image/png', base64: TINY_PNG_BASE64 }],
    });
    const pastedItem = page.locator('.ant-upload-list-item').filter({ hasText: /copied\.png/ });
    await expect(pastedItem).toBeVisible();
    const pastedName = (await pastedItem.innerText()).match(/copied\.png/)?.[0];
    expect(pastedName).toBeTruthy();

    let uploadBody;
    await page.route('**/api/health/report/upload', async (route) => {
      uploadBody = route.request().postDataBuffer();
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify(uploadResponse(seeded, [
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
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);

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
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);

    await page.locator('.report-paste-zone').evaluate((element) => {
      element.dispatchEvent(new Event('paste', { bubbles: true, cancelable: true }));
    });
    await expect(page.getByText('当前环境不支持粘贴，请选择文件')).toBeVisible();
  });
});

test.describe('移动端粘贴入口', () => {
  test.use({ viewport: { width: 375, height: 667 } });

  test('粘贴图片按钮聚焦隐藏可编辑区且不滚动页面', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);

    const pasteButton = page.getByRole('button', { name: '粘贴图片' });
    await pasteButton.scrollIntoViewIfNeeded();
    const before = await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));

    await pasteButton.click();
    await expect(page.getByText('长按屏幕 → 粘贴')).toBeVisible();

    const focusState = await page.evaluate(() => ({
      className: document.activeElement?.className || '',
      contentEditable: document.activeElement?.contentEditable,
      x: window.scrollX,
      y: window.scrollY,
    }));
    expect(focusState.className).toContain('report-paste-editable');
    expect(focusState.contentEditable).toBe('true');
    expect(focusState.x).toBe(before.x);
    expect(focusState.y).toBe(before.y);

    const editableBox = await page.locator('.report-paste-editable').boundingBox();
    expect(editableBox).not.toBeNull();
    expect(editableBox.x < 0 || editableBox.y < 0).toBeTruthy();
  });

  test('长按上传区聚焦隐藏可编辑区并显示引导', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);

    const zone = page.locator('.report-paste-zone');
    await zone.scrollIntoViewIfNeeded();
    const before = await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));

    await zone.evaluate((element) => {
      const event = new PointerEvent('pointerdown', {
        bubbles: true,
        cancelable: true,
        composed: true,
        pointerId: 1,
        pointerType: 'touch',
        isPrimary: true,
        button: 0,
        clientX: 120,
        clientY: 180,
      });
      element.dispatchEvent(event);
    });
    await page.waitForTimeout(700);

    await expect(page.getByText('长按屏幕 → 粘贴')).toBeVisible();
    const focusState = await page.evaluate(() => ({
      className: document.activeElement?.className || '',
      contentEditable: document.activeElement?.contentEditable,
      x: window.scrollX,
      y: window.scrollY,
    }));
    expect(focusState.className).toContain('report-paste-editable');
    expect(focusState.contentEditable).toBe('true');
    expect(focusState.x).toBe(before.x);
    expect(focusState.y).toBe(before.y);

    await zone.evaluate((element) => {
      element.dispatchEvent(new PointerEvent('pointerup', {
        bubbles: true,
        cancelable: true,
        composed: true,
        pointerId: 1,
        pointerType: 'touch',
        isPrimary: true,
        button: 0,
        clientX: 120,
        clientY: 180,
      }));
    });
  });

  test('聚焦后的移动粘贴区接收图片并进入待上传列表', async ({ page, seed }) => {
    const seeded = await seed({ reports: [] });
    await openUploadPage(page, seeded);

    await page.getByRole('button', { name: '粘贴图片' }).click();
    await expect(page.getByText('长按屏幕 → 粘贴')).toBeVisible();

    await dispatchPaste(page.locator('.report-paste-editable'), {
      files: [{ name: 'copied.png', type: 'image/png', base64: TINY_PNG_BASE64 }],
    });
    await expect(page.getByText('copied.png')).toBeVisible();
  });
});
