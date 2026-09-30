# 对外展示页面的规格

这份文件是**改这个页面时的唯一依据**。改之前先读完，改完按「验收」一节逐条自查。

页面本体：`radar/app/static/{index.html,style.css,app.js}`，由 `radar/app/main.py` 在 `/` 提供。

## 定位

给**陌生人**（含搜索引擎）看的公开页：把采集到的技术资讯列出来，
每条给出标题、中文摘要、标签和原始链接。**没有登录，没有交互写操作。**

## 硬约束

1. **零外部依赖**。不用任何 CDN；字体只用系统栈；噪点是内联 `data:` URI。
   断网也必须能完整渲染。
2. **配色低饱和暖调**。墨色底 + 暖白正文 + **唯一**一个琥珀色强调（`#c08a3e`，色相 35°）。
   禁紫蓝渐变、霓虹、玻璃拟态、多色渐变。
3. **不出现任何个人信息**。页面里不得有用户姓名、姓名单字、Linux 用户名、私人邮箱、家宽域名。
4. **文案只留一个标题 + 至多一行平实事实句**。不写渲染腔的散文，不写「这个站是怎么组织的」，
   不出现「家庭」字样。
5. **渲染第三方文本必须当不可信输入**。标题/摘要/链接来自 HN、GitHub、V2EX，
   一律用 `textContent` 写入（**绝不 `innerHTML`**），链接只放行 `http/https`。
6. **窄屏（390px）可用**。窄屏下左侧元信息栏收进标题上方，避免正文被压成逐字断行。
7. **无障碍**。语义标签、键盘可遍历、`:focus-visible` 有可见焦点环、装饰层 `aria-hidden`、
   不用 emoji、`prefers-reduced-motion` 下停止动效。

## 已被否定的东西（不要在后续修改里加回来）

- ✗ 等权卡片网格 + 每张卡标题上方的圆角方块图标（最典型的模板长相）
- ✗ 副标题、第二句说明、eyebrow 小标
- ✗ 紫色/蓝色强调色、渐变按钮、大圆角
- ✗ 任何指向外部 CDN 的资源引用
- ✗ 在页面上解释「列表数据从哪来」「这个服务怎么部署的」

## 动效

背景由两层构成，都封在 `.grain` 里（`position:fixed` + `pointer-events:none`）：

- `::before` —— 缓慢漂移的细颗粒（内联 SVG `feTurbulence`），提供**可追踪的结构**。
- `::after` —— 两团极淡的暖色光晕，让画面不平，但不形成可辨认的形状。

**踩过的两个坑，改这个层之前必读：**

1. `feTurbulence` 默认**不可平铺**，直接铺砖会露出方格拼缝 ⇒ 必须加 `stitchTiles='stitch'`。
2. 接缝修好**还不够**：低频花纹仍会按图块尺寸循环，屏幕上出现规律的横向条带
   ⇒ 把 `baseFrequency` 提到 `1.6`（颗粒变细、低频结构消失）并把图块放大到 220px。
3. **位移量必须等于图块尺寸**（`translate3d(-220px,-220px,0)`），否则循环点会露接缝。
   改图块尺寸时 `@keyframes` 里那两处要一起改。

**判断"能不能看见在动"要用可追踪位移速度（px/s），不要用"每秒变化多少像素"。**
当前是 220px / 24s ≈ 9px/s。

## 验收

```bash
# 页面与接口的自动化测试（含缓存头、Content-Type、零外部依赖）
.venv/bin/python -m pytest radar/tests/test_public_page.py -q

# 发布前的各项闸门自检
bash scripts/dev/check_public_page.sh

# 配色闸门（色相 200–310° 且饱和度 ≥25% 的必须为 0 个）
python3 ~/.hermes/skills/software-development/selfhosted-web-frontend/scripts/check-palette.py \
  radar/app/static/style.css
```

**必须真的渲染出来看。** 没看过就说"好看"是编造。启动方式：

```bash
RADAR_SCHEDULER_ENABLED=false .venv/bin/python -m uvicorn radar.app.main:app --port 18801
```

自查用浏览器时**要主动绕开缓存**（URL 挂 `?v=<时间戳>`），否则会对着旧 CSS 误判成"改动没生效"。

## 缓存头

`radar/app/main.py` 里的 middleware 只给 `text/html` 与 `/static/` 发
`Cache-Control: no-cache, must-revalidate`；**JSON 接口故意不发**（免得监控读到过期数据）。
测试两头都钉住了，别只改一头。
