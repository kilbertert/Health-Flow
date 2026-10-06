# 粘贴图片上传

## Sub-features

- 桌面粘贴生成 MIME 对应扩展名的文件项
- 非图片剪贴内容被忽略并轻提示
- 识别 `text/html` 中的 `data:image` base64
- `webp` / `heic` 剪贴物提示暂不支持
- 移动端粘贴区聚焦后接收图片

## How to get to it (user POV)

首页上传区（桌面）或移动端粘贴区 → 聚焦 → 按屏幕提示 `长按屏幕 → 粘贴`。

## Driving it with Playwright

Two steps are required — **navigate to the upload page, then dispatch a real paste
event**. Asserting the filename without dispatching the event finds nothing.

```js
const TINY_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC';

async function dispatchPaste(locator, { files = [], textHtml = '', plainText = '' } = {}) {
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
await loginWithSeed(page, seeded);
await page.getByRole('button', { name: '体检报告解读' }).click();
await expect(page.getByText('点击或拖拽多张报告文件到此区域')).toBeVisible();

// 2. 再派发粘贴事件到 .report-paste-zone
const zone = page.locator('.report-paste-zone');
await dispatchPaste(zone, {
  files: [{ name: 'copied.png', type: 'image/png', base64: TINY_PNG_BASE64 }],
});

// 3. 断言的是**生成名**，不是原名 —— 粘贴项按序号重命名为 粘贴-N.<ext>
await expect(page.getByText(/粘贴-\d+\.png/)).toBeVisible();
```

桌面用例需 `test.use({ viewport: { width: 1280, height: 800 } })`；移动端走
「粘贴图片」按钮 + 长按聚焦路径。

## Gotchas

- **粘贴是剪贴板事件，不是文件选择。** 用 `page.evaluate` 派发带 `DataTransfer` 的
  `paste` 事件，不要用 `setInputFiles`。
- `text/html` 里嵌 `data:image` base64 是一条独立分支，容易被漏掉。
- 不支持的格式（webp/heic）期望是**提示**而不是静默接受；断言提示文案存在。
- 非图片剪贴内容必须被忽略，断言"没有产生任何待上传项"，而不是只看有没有报错。
