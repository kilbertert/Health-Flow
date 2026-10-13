# 15. E2E 量几何量前必须等到状态静止（动画帧与悬浮浮层都不是布局）

日期：2026-10-13
状态：已采纳
影响版本：本仓库（`frontend/e2e/report-layout.spec.js`、`frontend/e2e/mobile-layout.spec.js`、
`.claude/skills/verify-healthflow/`）

## 背景

全量 Playwright 套件（`npm run test:e2e`，默认并发 = `ceil(nproc/2)` = 4）**间歇性失败**：
一条几何量断言偶发红，与本改动无关、每次命中的用例不同，于是「全套通过」变成一个不能信的
信号（#234）。

诊断过程中全量跑了 12 次，命中 **两次，机制不同**：

| 现场 | 断言 | 读数 |
| --- | --- | --- |
| `report-layout.spec.js`（桌面端原文弹窗宽度） | `955 <= w <= 961` | 192 |
| `mobile-layout.spec.js`（375/414 首页与个人中心无横向溢出） | `scrollWidth - clientWidth <= 0` | 2~4 |

实测（干净 `main`）：`--workers=1` 全绿；默认并发才复发。单跑任一 spec 反复跑也全绿 ——
**两条都需要全量并发**。

## 机制（钉死，不是方向列表）

两次是**同一类错误、两个来源**：都量了一个由「还没稳定下来的状态」决定的几何量。

### 一、入场动画：192 是动画帧，不是布局

`report-layout` 那条的失败读数是 `Expected: >= 955, Received: 192`。

**`192 = 960 × 0.2`，是 antd 入场动画的第一帧。** 原文弹窗是 antd `Modal`，带
`antZoomIn` 关键帧（`node_modules/antd/es/style/motion/zoom.js`：`scale(0.2) → scale(1)`，
约 360ms）。`boundingBox()` / `getBoundingClientRect()` 返回的是**渲染盒** —— 动画期间被
`transform: scale()` 缩放。

逐帧观测（临时诊断 spec，跑完即删）：

| 视口 | `offsetWidth`（布局） | 动画中渲染盒 | 动画结束 |
| --- | --- | --- | --- |
| 1280 | **960（恒定）** | 192（`960×0.2`） | 960 |
| 375 | **375（恒定）** | 75（`375×0.2`） | 375 |

帧序列：`w=960 且类名含 ant-zoom-appear-start 且 getAnimations()=[]`（**还没开始跑的第一
帧**）→ `w=192 且 ant-zoom-appear-active` → 回到 960 → 类名摘掉。

**产品没有缺陷**：布局宽度始终是 960 / 移动端全宽，`offsetWidth` 在动画每一帧都相同。

### 二、悬浮浮层：测试自己的点击留下的 antd Tooltip

`mobile-layout` 那条的失败读数是 `Expected: <= 0, Received: 3`。

Playwright 的 `click()` **先把指针移到按钮中心** —— 点完页头「个人中心」图标按钮之后，
它的 antd `Tooltip` 是**显示着**的。该浮层的定位容器贴着屏幕右缘（375 视口下
`right=379`），给文档带来 **4px 横向溢出**。浮层出现约 100ms 延迟，于是「点完立刻量」平时
量到 0、并发下量到 3~4。

迁移到元素上（逐帧找右边界越界的节点）：`ant-tooltip-placement-bottom` / `right=379` /
`w=84`；`mouse.move(1,1)` 后溢出归 0。

### 共同的根因

**两条都在量一个会变的中间状态，而且「什么时候量」由测试自己的动作（打开弹窗、点击按钮）
留下的副作用决定。** 平时那个中间状态与测量错开（动画还没跑 / 浮层还没出现），并发把时序
拉开，测量正好落在中间状态里。

## 决定

**量几何量之前，等到相关状态静止。** 判据是**结构与动画状态**，不是 `waitForTimeout`：

```js
// 弹窗：等入场动画结束（类名 + 动画状态，缺一不可）
async function settledBox(locator) {
  await expect
    .poll(() =>
      locator.evaluate((el) => {
        const appearing = el.className.includes('ant-zoom-appear');
        return appearing || el.getAnimations().length > 0;
      }),
    )
    .toBe(false);
  return locator.boundingBox();
}

// 横向溢出：移开指针，并等悬浮浮层消失
async function excessWithoutHoverOverlay(page) {
  await page.mouse.move(1, 1);
  await expect.poll(() => page.locator('.ant-tooltip:visible').count()).toBe(0);
  return horizontalExcess(page);
}
```

关键判据：

- **动画必须同时看类名。** 只看 `getAnimations().length === 0` 会在打开后的第一帧当场通过
  （那一刻动画还没注册）—— 正是原 `expect.poll` 误通过的那个瞬时帧。类名
  `ant-zoom-appear` 在开始帧与动画期间都在，结束时被摘掉。
- **浮层用「可视的 Tooltip 还在不在」判，不靠等时长。** 移开指针后 antd 先藏着再移除，
  轮询到它消失为止。
- **断言仍量 `boundingBox()`。** 位置（x/y）只有渲染盒能回答；既然位置只能靠渲染盒，就
  统一在动画结束后量一次，宽度与位置是同一帧的。

## 被否掉的方案

- **加大容差（`>= 955` → `>= 190`；`<= 0` → `<= 4`）。** 把「量到了中间状态」当成合法
  结果，永久失去这两条断言的意义。
- **加 `retries`（票面明令不做）。** 竞态不是「一次红」，加 retries 只是把「一次红」变成
  「两次没红」—— 掩盖而非修复，失败计数不再可信。
- **`waitForTimeout`。** 靠猜够不够，负载下可能不够、轻载下白等。
- **只修 `report-layout`（票面最初只点名它）。** 诊断中跑出的第二次失败证明这是**一类**
  错误，不是一处 —— 只修一处，`mobile-layout` 那条会接着红。
- **`--workers=1` 常驻。** 慢一倍，且根因（量中间状态）仍在，换个机器/worker 数就复发。
- **判定「产品有响应式缺陷」并改产品。** 证据不支持：布局宽度始终正确，溢出只由测试自己
  的悬浮浮层造成，`mouse.move` 后即归零。

## 后果

- `report-layout.spec.js` 的五条几何量断言在静止帧上量；`mobile-layout.spec.js` 的两处
  横向溢出断言移开指针并等浮层消失。
- **负控（两条各自会红）**：
  - 去掉 `settledBox` 的等待 → `report-layout --repeat-each=4`：**12 failed / 8 passed**
    （桌面读到 192、移动端读到 75）；
  - 把浮层判据反过来（停在「浮层显示着」）→ `mobile-layout --repeat-each=2`：**1 failed /
    9 passed**（`Received: 4`）。
- `.claude/skills/verify-healthflow/` 的 gotcha 补上这两条。
- **判据留在测试侧，产品一字未改** —— 票面 Out of Scope 说「除非证据表明是产品缺陷，那时
  另开票」；两次诊断的证据都表明不是。

## 验证

| 断言 | 结果 |
| --- | --- |
| 逐帧观测：动画中渲染盒 = `960×0.2` = 192，`offsetWidth` 恒为 960 | 实测（临时 spec，已删） |
| 逐帧观测：溢出 0→4，越界节点是 `ant-tooltip-*`，`mouse.move` 后归 0 | 实测（临时 spec，已删） |
| `npm run test:e2e` 连续全绿（默认并发，≥5 次） | 见 PR |
| 负控（两条） | 均会红，见上 |
