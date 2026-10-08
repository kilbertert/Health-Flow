# 粘贴图片上传

## Sub-features

- 桌面粘贴**保留源文件名**；剪贴板 blob（无源名）生成 `粘贴-<时间戳>-<序号>.<ext>`
- 非图片剪贴内容被忽略并轻提示
- 识别 `text/html` 中的 `data:image` base64
- 不受理的格式（webp/heic 等）提示「暂不支持 X 格式（支持 …）」
- 受理集合与上限由服务端 `/api/health/upload-policy` 下发（`accept`、`maxCount`、文案）
- 移动端粘贴区聚焦后接收图片

## How to get to it (user POV)

首页上传区（桌面）或移动端粘贴区 → 聚焦 → 按屏幕提示 `长按屏幕 → 粘贴`。

## Driving it with Playwright

Two steps are required — **navigate to the upload page, then dispatch a real paste
event**. Asserting the filename without dispatching the event finds nothing.

```js
const TINY_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC';

// `files` 走 `clipboardData.items` 且**带**文件名（桌面复制文件）；
// `items` 同样走 items 但**不带**名字（粘贴截图的真实形状）。
async function dispatchPaste(locator, { files = [], items = [], textHtml = '', plainText = '' } = {}) {
  await locator.evaluate((element, options) => {
    const data = new DataTransfer();
    for (const file of options.files) {
      const bytes = Uint8Array.from(atob(file.base64), (c) => c.charCodeAt(0));
      data.items.add(new File([bytes], file.name, { type: file.type }));
    }
    if (options.textHtml) data.setData('text/html', options.textHtml);
    if (options.plainText) data.setData('text/plain', options.plainText);

    const event = document.createEvent('Event');
    event.initEvent('paste', true, true);
    try {
      Object.defineProperty(event, 'clipboardData', { get: () => data });
    } catch {
      // Chromium 的合成 Event 可能已有只读 clipboardData；无此属性则无法覆盖。
    }
    element.dispatchEvent(event);
  }, { files, textHtml, plainText });
}

// 1. 上传页的导航按钮名是「体检报告解读」，不是「粘贴图片」
const seeded = await seed({ reports: [] });   // loginWithSeed 需要 seeded.subject
await loginWithSeed(page, seeded);
await page.getByRole('button', { name: '体检报告解读' }).click();
await expect(page.getByText('点击或拖拽多张报告文件到此区域')).toBeVisible();

// 2. 再派发粘贴事件到 .report-paste-zone
const zone = page.locator('.report-paste-zone');
await dispatchPaste(zone, {
  files: [{ name: 'copied.png', type: 'image/png', base64: TINY_PNG_BASE64 }],
});

// 3. **有源文件名就保留它** —— 词条「报告原始材料」承诺保留文件名。
await expect(page.getByText('copied.png')).toBeVisible();

// 没有源名（items 路径 / data:image）才生成：`粘贴-<时间戳>-<序号>.<ext>`
await dispatchPaste(zone, {
  items: [{ name: '', type: 'image/jpeg', base64: TINY_PNG_BASE64 }],
});
await expect(page.getByText(/粘贴-\d+-\d+\.jpg/)).toBeVisible();
```

桌面用例需 `test.use({ viewport: { width: 1280, height: 800 } })`；移动端走
「粘贴图片」按钮 + 长按聚焦路径。

## Sibling spec

**类型判定**（这份材料是什么、能不能受理）不是粘贴特有的，它在上传闸门里对
文件选择与粘贴一视同仁。那条链路的证据在 `frontend/e2e/upload-material.spec.js`：
把 PNG 改名成 `.pdf` 会被明确拒绝（服务端 `app/service/report_material.py`
按内容判定），内容与后缀相符的 PDF 照常受理。

**受理集合与上限**来自服务端 `/api/health/upload-policy`（策略的单一权威是
`app/service/upload_policy.py`）。`page.route` 改这个端点就能驱动「运维者收紧
受理集合」「调大上限」两条真实场景 —— 本 spec 里各有一条。

## Gotchas

- **粘贴是剪贴板事件，不是文件选择。** 用 `page.evaluate` 派发带 `DataTransfer` 的
  `paste` 事件，不要用 `setInputFiles`。
- `text/html` 里嵌 `data:image` base64 是一条独立分支，容易被漏掉。
- 不支持的格式（webp/heic）期望是**提示**而不是静默接受；断言提示文案里带上了
  被拒的格式名与支持列表（「暂不支持 WEBP 格式（支持 PDF / JPG / …）」）。
- **`data:image` 那条分支的正则捕获的是子类型**（`png`，不是 `image/png`）。
  受理判据看的是完整类型串，补前缀这一处漏了就会静默不受理。
- 非图片剪贴内容必须被忽略，断言"没有产生任何待上传项"，而不是只看有没有报错。
