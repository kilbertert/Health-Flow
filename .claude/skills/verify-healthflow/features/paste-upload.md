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

```js
const seeded = await seed({ reports: [] });
await loginWithSeed(page, seeded);
await page.getByRole('button', { name: '粘贴图片' }).click();
await expect(page.getByText(/粘贴-\d+\.png/)).toBeVisible();
```

## Gotchas

- **粘贴是剪贴板事件，不是文件选择。** 用 `page.evaluate` 派发带 `DataTransfer` 的
  `paste` 事件，不要用 `setInputFiles`。
- `text/html` 里嵌 `data:image` base64 是一条独立分支，容易被漏掉。
- 不支持的格式（webp/heic）期望是**提示**而不是静默接受；断言提示文案存在。
- 非图片剪贴内容必须被忽略，断言"没有产生任何待上传项"，而不是只看有没有报错。
