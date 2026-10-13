# 响应式布局与溢出

## Sub-features

- 无会话视图无横向溢出且卡片完整落在视口内
- 首页与个人中心无横向溢出，固定导航不遮挡，底部退出按钮可见
- 登录后首页保留桌面导航且无横向溢出
- 报告页无横向溢出；技术详情默认收起可展开
- 桌面端原文弹窗保留 960px 宽度

## How to get to it (user POV)

横跨所有页面 —— 这是一条跨功能的布局契约，不是独立入口。

## Driving it with Playwright

```js
// 溢出判据（真实用例的做法）
const overflow = await page.evaluate(
  () => document.documentElement.scrollWidth - document.documentElement.clientWidth
);
expect(overflow).toBeLessThanOrEqual(0);
```

## Gotchas

- **溢出断言要看 `scrollWidth - clientWidth`，不要靠截图肉眼判断** —— 1–2px 的溢出
  在截图里看不出来，但真实设备上会出现横向滚动。
- **量溢出前先把指针移开、并等悬浮浮层消失。** Playwright 的 `click()` 会先把指针移到
  按钮中心 —— 点完页头「个人中心」图标按钮后，它的 antd `Tooltip` 是显示着的，而该浮层
  贴屏幕右缘，会给文档带来约 4px 的横向溢出。浮层有约 100ms 延迟，所以「点完立刻量」
  平时量到 0、并发下量到 3~4（#234 的第二条）。正确写法见 `mobile-layout.spec.js` 的
  `excessWithoutHoverOverlay()`：`mouse.move(1,1)` + 轮询 `.ant-tooltip:visible` 为 0。
- 这条契约对**每一个**页面都成立，包括无会话视图。新增页面时补一处断言。
- 固定导航的遮挡要单独断言元素可见性与位置，溢出为 0 不代表没被盖住。
