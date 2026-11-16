# DeviceHands 主页(首屏 3D 故事)的维护说明

## 主页用到的文件

下面只列主页(`docs/index.html`)用到的文件;`docs/` 下其余页面是原 android-llm-bridge 的文档站,原首页现在是 `docs/guide.html`。

```
docs/                        GitHub Pages 根目录
  index.html                 主页,中英双语(右上角切换,或 ?lang=en / ?lang=zh)
  assets/css/site.css        样式
  assets/js/site.js          滚动、语言切换、圆桌对话回放、图表
  assets/js/scene.js         首屏 3D 故事的源码
  assets/js/scene.bundle.js  页面实际加载的文件,由 `npm run build` 生成
  assets/img/                poster.jpg(不支持 WebGL 时的替代图)· og.jpg(分享卡片)· favicon.svg
  assets/fonts/              IBM Plex Mono(SIL OFL 1.1,许可证在同目录)
  assets/vendor/three/       three.js 0.170.0(MIT,许可证在同目录)
tools/build-scene.mjs        把 scene.js 和 three.js 合并成一个脚本
tools/check-sharpness.mjs    检查每一幕里角色是否清晰
archive/                     早期版本的首屏,留作参考,不在主页上
```

## 首屏讲的故事

一段跟着滚动走的 3D 故事,九幕:外地一台设备死机 → 实验室电脑里的本地 AI 接手 → 在家的主脑连上来 →
三个 AI 讨论 → 飞进设备里面 → 打开 DeviceHands 工具包 → 找到 PC 指针和调用栈 →
主脑亲自进来把线索连起来 → 把报告交给人。

故事里的 bug、调用栈、函数名、时间都是编的,用来演示流程。每一幕下面的小标签标了对应能力是「现在能用」还是「规划中」。
画面里的电路板和产品(手机、音箱、电视、电脑、服务器)是为演示画的,不对应任何真实产品。

## 本地预览

直接双击 `docs/index.html` 就能看,3D 也会起来。

## 改了 3D 场景之后

页面加载的是 `docs/assets/js/scene.bundle.js`,它由 `scene.js` 和 three.js 合并而成。
改了 `scene.js` 要重新合并,否则页面还是旧的:

```bash
npm install      # 只需一次,装 esbuild
npm run build    # 生成 docs/assets/js/scene.bundle.js
```

为什么要合并:浏览器不允许从本地文件(file://)打开的页面加载 ES module,不合并的话双击打开时 3D 场景起不来。

合并后跑一遍清晰度检查。它逐幕把每个角色的头部裁出来,比较开景深和关景深时的锐度,低于 0.8 就失败:

```bash
PLAYWRIGHT_PATH=/path/to/playwright/index.mjs node tools/check-sharpness.mjs
PLAYWRIGHT_PATH=/path/to/playwright/index.mjs node tools/check-sharpness.mjs --self-test   # 故意对不准焦,必须报失败
```

## 页面上的数字从哪来

| 页面上写的 | 出处 | 核对日期 |
|---|---|---|
| 36 个 MCP 工具 | 本仓库 `src/alb/mcp` 里 `@mcp.tool` 装饰器计数 | 2026-11-13 |
| 3,037 条自动测试 | 本仓库 `uv run pytest --collect-only -q` | 2026-11-13 |
| 现在能用 / 正在做 / 规划中的划分 | 本仓库 `docs/models.html`、`docs/roundtable.html` | 2026-11-13 |

圆桌对话、内存曲线、火焰图是概念示意,数据是编的,页面上已标「规划中 · 示意」。
