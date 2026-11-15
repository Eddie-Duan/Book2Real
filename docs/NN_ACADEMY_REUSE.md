# NN Academy 可复用方案盘点（对照「纸书智伴」）

> 取证对象：`chhaaabbbccc/nn-academy`，浅克隆于 `.tmp/nn-academy`，commit `bf34e3a`（2026-11-14）。
> 只读范围：README / AI-INTEGRATION / docs 五篇 / server.js / public/js 关键模块 / tools 四个门 / 四套语音与 RAG 测试。
> 对照锚点：`docs/赛题分析.md`、`docs/HANDOFF.md` §6.3/§9、`book2real/` 现状。
> 本文所有结论都落到具体文件与行号；`.tmp/` 是临时克隆物，不入库。

---

## 1 一句话结论

**能直接拿来用的是「工程骨架」——零依赖 server 的五个片段、零构建 SPA 的目录形态、句级 TTS 的缓存/时间轴/插值、`augmentPrompt` 的引用强制协议、苏格拉底状态机、口语归一化器、整书离线下载 + SW 策略、qc 五门的形状**；
**看着像但其实是陷阱的是「产品能力口径」——他们是文字教科书平台（判定机器化 + 间隔重复），我们是有图有视频的绘本产品**：
他们没有图像/VLM 通道（`点击局部识别` 一行都没有）、没有打断（`speech.js` 与 `speech-recog.js` 零耦合，只有 pause/stop）、没有内容审核层、APK/桌面的构建管线在公开仓库里是断的（`desktop/` 不存在、`tools/e2e/` 缺失、Capacitor 路径硬编码 `E:/AndroidDev/...`）。
凡是我们赛题里写「视觉/语音/合规」的地方，他们只能提供「答的一半」和「坑档」，不能提供整条链路。

---

## 2 对照表

| 赛题要求 / 我们的缺口 | nn-academy 的做法与文件路径 | 能不能直接搬 | 搬过来大概要改什么 |
|---|---|---|---|
| **零依赖 server**（我们 `book2real/reader_app.py:175-228` 已是 stdlib Python server，但缺安全头/路径守卫） | 单文件 941 行，只 require 5 个内置模块（`server.js:29-33`）；MIME 表 `server.js:261-267`；`safeJoin` 用 `root+path.sep` 前缀校验而非裸 `startsWith`（`server.js:281-285`，注释点明「裸 startsWith 会放过兄弟目录 public-xxx」）；静态 + SPA 回退（`server.js:897-906`，找不到文件即回 `index.html`）；HTML 发 CSP + nosniff（`server.js:910-927`）；未知 API 一律 404 JSON（`server.js:894`）；启动打印局域网地址（`server.js:937-940` + `lanIPv4s()` `428`） | **改一改** | 五个片段逐段移植到 `reader_app.py`：`safeJoin`→`Path.resolve()+is_relative_to`；CSP 可直接用（我们 `reader.html:132` 只有一个外链 script，无内联脚本，`script-src 'self'` 成立；但 `reader.js:169-186` 用 innerHTML 拼 `style=` 属性，`style-src` 必须留 `'unsafe-inline'`）；`/api/*` 404 语义、局域网 URL 打印照抄 |
| **零构建 SPA 的前端工程方式** | 仓库根**没有 package.json**，无安装、无打包（`README.md:12`「无任何依赖安装步骤」）；`public/index.html:1-32` 只有 `<link>` 与两个前置 `<script src="/js/boot/...">`；第三方库全部本地化在 `public/vendor/`（663 个文件）；模块用浏览器原生 ESM，根绝对路径 `import { doc } from "/js/doc.js"`（`docs/AUTHORING.md:125-135`） | **能（照结构，不搬文件）** | 我们的 `book2real/web/` 已经是这个形态（`reader.html` + `reader.js` 无构建），只需补两件：① 静态资源版本号/缓存策略（他们靠 `sw.js:16` 的 `CACHE_V`）；② 把第三方库（如需要）落到 `book2real/web/vendor/` 而不是 CDN |
| **语音 TTS**（我们 `/api/tts` 每次现合成、无缓存、无时间轴、截断 180 字，`reader_app.py:216-227`） | 服务端句级缓存 + 字级时间轴：`server.js:506-523`（`TTS_VOICES`、缓存目录、`TTS_CACHE_MAX` 300MB、LRU 目标 2/3）、`:860-890` 处理与 `probe=1` 零合成探测、`:873` 引擎不可用 → `503 {fallback:"l1"}`；前端句工厂 + 预取（`public/js/l2tts.js:15-37`）、时间轴映射三纯函数（`:42/60/77`）；播放侧主方案=按句 utterance + 句内**字数插值**（`public/js/speech.js:1-8` 给出硬证据：中文 `onboundary` 不可靠，crbug 40715888，Android WebView 全不支持） | **能** | 后端逻辑翻成 Python 写进 `reader_app.py:/api/tts`：`sha1(text+voice+rate)` 作 key、`?probe=1` 只探活、失败返回 `fallback` 字段而不抛 500；前端新建 `book2real/web/tts.js` 搬 `l2tts.js:21-37` 与 `speech.js:14-46`（切句器 + `RATES` + `interpChars`），旧路径 `reader.js:386` 与 `:391-392` 作为 L1 回落保留 |
| **语音 ASR** | `public/js/speech-recog.js`：能力边界写死在文件头（`:4` —— Chrome/Edge 桌面 partial、**Android WebView ❌（APK 直接出局）**、Edge ❌、Firefox 默认关）；`recogSupported()` 不可用即隐藏按钮（`:15`）；首次使用弹隐私确认（`:19-30`，`localStorage` 记忆）；`onspeechend` 自动收音（`:57`）；`maxAlternatives` 多候选 → 逐个过判分器（`:34-56`，`exercise.js:142/219` 两处接入） | **改一改（且只对我们「浏览器/平板」路线有效）** | 我们赛题要求「平板 Demo + APK」——照搬这条就正好踩在 WebView 不支持上。结论：ASR 只在桌面 Chrome / 平板 Chrome 演示，APK 走预录或服务端 ASR；隐私确认 + 多候选 + 能力探测这三段可直接搬进 `reader.js` |
| **打断（barge-in）** | **没有实现。** `speech.js` 只 import `mathspeech.js`（`:9`），`speech-recog.js` **零 import**，`listen.js:13-19` 也不 import 识别器 —— 朗读与识别是两条互不相通的链；只有 `pause/resume/stop`（`speech.js:239-249`）与 `speakAside`（`:303`） | **别搬（要自研）** | 真打断需要：VAD 或"检测到人声即 `speechSynthesis.cancel()`/`<audio>.pause()`"+ 半句截断状态机 + 打断后的追问上下文。他们这套是「点一下说一句」的单轮范式，改了也不是打断 |
| **口语归一化**（把「十五」「三点一四」「零一零一」变成判分器能吃的东西） | `public/js/voice-norm.js` 三个纯函数：`cnNumToDigit`（`:16`，位值/小数/负数/**逐位串保前导零**）、`normalizeSpoken`（`:66`，运算口语/次方/根号/分之/百分比/壳词剥离/全角折半）、`mapSpokenChoice`（`:99`）；测试 `tests/voice_norm.test.mjs:1-10` 明写判分链路契约 | **能（直接搬，纯函数零依赖）** | 我们点击→问答里若有语音，这句归一化必须放在判分器前；JS 直接拷进 `book2real/web/`，Python 侧做等价实现时照 `cnNumToDigit` 的逐位串规则写用例 |
| **点击 / 局部识别问答** | **他们没有图像通道。** grep `VLM|image_url|multimodal|bbox` 在 `public/js/*.js` 零命中，`server.js` 无上传端点，全书是 JS 模块里的文字段（`docs/AUTHORING.md:42-58`）。最接近的能力是「屏幕上下文注入」：`public/js/view-context.js:1-6`（发送时实时查 DOM，零常驻观察器）+ `public/js/labkit.js:210` `askAIBox({ctx: () => 实时状态})` | **别搬（识别那半我们自己做）；答的那半能搬** | 我们的点击链路已经存在：`reader.js:141-143` 字级点击 → `showWord`（`:350-364` 词典/拼音/上下文句）、`:195-199` 角色热点 → `showSpot`（`:315-319` 只显示预存 `name/desc`），**没有模型参与**。缺的正是「把 {bbox, 实体名, 人设 desc, 本页正文, 本页 scene} 作为 context 发给 LLM 并流式回答」 |
| **RAG / 检索问答** | `public/js/ai-context.js`：MiniSearch 配置 `:144`、词级∪bigram 双路 tokenize `:178`（测试 `tests/rag_packa.test.mjs:24-30` 用 Node full-ICU 词典实证行为断言）、静态文本语料提取 `:209`、`buildBookIndex` `:331`、跨书 top-k `searchBooks` `:384`、`buildContext` 拼【教材原文·书名 第N章 第X节】+ 引用列表 `:399`、`augmentPrompt` **在 prompt 前插原文块并强制引用+未覆盖如实说** `:420-426`；整条问答流水与语义缓存见 `public/js/ask.js:1-10`（`HIT_TAU 0.90` `:12`）；调试台 `public/js/ragdev.js:1-8` | **能（协议直接搬，索引层重写）** | `augmentPrompt`（`:424-425`）那两句中文约束可以**一字不改**用在我们的量子模型提示里；索引层我们不要 MiniSearch，用 Python 侧 BM25/关键词即可（语料=每页 `page.text` + `scene` + 角色 `desc`），这样"页级引用"天然对齐赛题的「结合本页上下文」 |
| **AI 导师式多轮追问** | 状态机在 `public/js/ai-hint.js`：提示阶梯 `HINT_LEVELS`/`HINT_LABELS` `:25-36`、`ladderProtocol` `:90`、`socraticNext` **纯函数**（索答/越狱 → 锁定+要求先猜；连错 1 次澄清 / 2 次指认矛盾 / 3 次升阶梯）`:186-225`、指令→注入行 `socraticDirectiveLine` `:228-244`、每题状态快照 cap 40 `:248-275`；对话侧 `public/js/ai.js:13-18`（四模式 + 温度）、`:75` `chatStream`、`:122-155` SSE 逐 delta 解析；红队用例 `ai-context.js:638` + `runRedTeam :658`（10 组对抗，断言注入约束关键词在场）；测试 `tests/socratic_v2.test.mjs:1-12`（§A 状态机 / §B 索答与越狱 / §E 13 组红队） | **能（纯函数，可 1:1 翻成 Python）** | 这是全仓库对我们**最值钱**的一块：赛题的「追问」要求正好对应 `socraticNext` 的 4 个 directive。搬到 `book2real/socratic.py`，`socraticDirectiveLine` 的 5 段中文文案原样保留，`RED_TEAM_CASES` 翻成 13 条 pytest 用例 |
| **离线 PWA 与断网降级** | `public/sw.js:1-14` 策略写死在文件头：导航 network-first→`/index.html`、`/api/` network-first 可回退缓存、其余静态 cache-first；`CACHE_V` 单点版本号（`:13`）+ `SHELL` 预缓存清单（`:15-45`，逐项 add 失败只告警）；整书离线下载 `public/js/offline.js:85-144`（清单→逐项 fetch 写**同一个** cacheName→`verifyBook`/`removeBook`）、能力诚实判定 `:20-35`、配额与持久化 `:158-183`；服务端配套 `GET /api/books/<id>/files`（`server.js:13` 契约、`:693-719` 实现）与 `.nnbk` 打包/导入（格式 `:440-441`、路由 `:720` 起） | **能** | **我们目前零 SW**：`reader.js` 里 `navigator.onLine|serviceWorker|manifest` 全部零命中。照搬顺序=先在 `reader_app.py` 加 `/api/books/<id>/files`（列 `books/<id>/reader/**`），再新建 `book2real/web/sw.js`（保留 `CACHE_V` 纪律）与 `offline.js` 的下载器；注册处必须学他们加 `isSecureContext` 守卫（`public/js/app.js:1139`，并照 `:1136-1141` 在非安全上下文时**明说"离线缓存不可用"而不是假装可用**——局域网明文 http 下 SW 本就不加载，见 `sw.js:11` 注） |
| **评测脚本：性能门禁** | `tools/perf_gate.mjs:1-13`：本机 Edge headless + CDP 注入 longtask/layout-shift/paint 三个 Observer 采 FCP/LCP/CLS/长帧/DOM 节点，**分级判定**（CLS/DOM/长帧=error，FCP/LCP=warn 因机器负载抖动）；`:22` 记录真因（CDP 端口撞车导致"手动通、脚本内超时"）；**文件头自述本脚本在其环境反复超时、未接通** | **改一改（抄设计，不抄实现）** | 我们赛题有「首响 P95 ≤1.5s / 点击 ≤1s」硬指标，缺一个可复跑的数。抄它的「确定性指标=硬门、纯时序=软门」分级，实现在 Playwright/CDP 上比它的裸 CDP 稳；不要直接搬 `.mjs` |
| **评测脚本：覆盖率** | `tools/coverage_check.mjs:1-16`：`toc-source.json`（正典 TOC）↔ 节文件首行 `/* cov: 5.1,5.1.1,习题5[1/6] */` 锚 ↔ 章装配器注册，三向校验，末行铁格式 `结果：X 覆盖，Y 缺失`，`--strict` 时 Y>0 → exit 1 | **别搬（协议绑定他们内容格式）** | 我们已有 `pages.json`/`manifest` 的页面计数（`HANDOFF.md:37`「页面覆盖 100%」是靠它），覆盖率用页数比对即可；值得借的是「末行固定格式 + `--strict` 退出码」这条门禁纪律 |
| **评测脚本：无障碍** | `tools/check-a11y.mjs:1-13`：解析 CSS `:root` / `[data-theme=light]` 变量算 WCAG 对比度（正文 ≥4.5 / 大字 ≥3.0），再叠三色觉模拟矩阵算语义色两两 ΔE ≥12 | **改一改** | 硬编码读 `public/css/skin-yuehai.css`（`:12`）。我们 `book2real/web/reader.css:2-4` 只有 5 个颜色变量，改路径 + 换语义色名单即可复用；但绘本的对比度主要落在**页图上的文字**，CSS 对比度门只能覆盖 UI 层 |
| **一键质量门** | `tools/qc.mjs:6-8` 五门：语法门（`node --check` 全 `public/js`+`tools`）`:21`、测试门（277 个 `tests/*.test.mjs` 逐个跑）`:43`、服务门（:8788 探活）`:71`、内容门（`content_audit.mjs`）`:118`、桌面门（依赖缺失→SKIP 不拦）`:148`；WIP 隔离机制 `tests/.qc_skip`（`:40-42`，必须在 DECISIONS 互引）；测试文件末行格式必须严格 `结果：N 通过，M 失败`（`docs/AUTHORING.md:158-164`） | **能（形状照搬）** | 我们只有 `tests/test_cast.py`、`test_layers.py`、`test_smoke.py` 三套 + `book2real/qc.py`（单镜头质检，不是工程门）。新建 `scripts/gate.py` 复刻五门：`py_compile`、`pytest tests/`、启服务打 `/api/books`、页面覆盖率、`/api/tts` 探活；退出码 0/1 |
| **内容审核与 AI 标识** | **没有审核层**：grep `敏感|moderat|safety|不当` 在 `public/js/*.js` + `server.js` 只命中无关的算法词，零命中审核实现。只有三处近似物：① `public/js/plan.js:109-117` AI 择优 vs 规则版双路、`:139` UI 标注「AI 择优 / 规则版」、`:147` 「AI 生成未过校验（原因），已回退规则版」——**产出可追溯 + 失败自动回退 + 界面标明来源**；② `public/js/ai-context.js:597` prompt 内「越权拒答：只依据学员当前教材与已提供材料作答……不得编造」；③ `public/js/ai-quizgen.js:1-13` AI 出题五道校验链（schema → 选项洗牌 → 盲解自洽 → **evidence span 必须能在原文反查到** → bigram 疑似重复只警示；队列 200，人审是最终闸门） | **别搬（合规模块必须自建）；但"标注+回退"的形状要抄** | 赛题红线在 `docs/赛题分析.md:67`（《人工智能生成合成内容标识办法》标识）与 `:63`（敏感词 + 输入/输出双审）。nn-academy 面向成人教材，没有任何儿童内容安全设计。可借的是 `plan.js` 的**三件套**：每个产物带 `source: ai|rule`、失败带 `aiFallback` 原因、UI 明示来源；以及 `ai-quizgen` 的 **span 反查**（生成内容必须在原书原文里找到出处才放行）——这条对"不许篡改原书事实"（`赛题分析.md:9`）几乎是量身定做的 |
| **多端（平板 / 桌面 / APK）** | PWA：`public/manifest.json:5-7`（`start_url`/`scope`/`display:standalone`、图标三档 `:12-30`）、`public/index.html:9`（`viewport-fit=cover` + `interactive-widget=resizes-content` 让软键盘压缩布局，`:8` 注释给了原因）与 `:13` manifest 链接、SW 安全上下文守卫 `public/js/app.js:1139`；安卓返回键壳 `public/js/native-shell.js:1-13`（`@capacitor/app` 的 backButton：可回退则 history.back，根页双击 2.2s 退出）；构建管线 `tools/build_android.mjs:1-7`（版本号→slim→cap sync→clean assemble→aapt 出包门+SHA256）与 `tools/build_desktop.mjs:1-6`（electron-builder nsis+portable） | **别搬代码，只抄坑档** | 实测：`desktop/` 目录**不存在**、`tools/e2e/` 目录**不存在**（`mobile_shots.mjs:1-10` 直接 import 失败）、`build_android.mjs:14` 的 `CAP = "E:/AndroidDev/nn-academy-cap"` 是硬编码绝对路径。我们就用「纯 PWA + Capacitor 包壳」，把他们踩过的两条记下来：① 不 clean 会出陈包；② WebView 83/Chromium 85 语法差异会让整包白屏（`sw.js:16` 的 `CACHE_V` 历史里记了 `v71` 那次 `??=` 事故） |
| **内容生产脚手架**（我们对应物是 `scripts/import_book.py` + `book2real/storybook.py`） | `tools/new-book.mjs:1-24` 一条命令生成可运行骨架（book.json / book.js / 章模块 / 六件套模板）；写作契约 `docs/AUTHORING.md:6-59`（书目录四层、`book.json` 单点事实源、新增章"三件套缺一即断链"的教训 `:29`）；封面色板 `docs/BOOK_COVER_SPEC.md:23-38`、徽章形制 `docs/BADGE_SPEC.md:6-24`（资产由 `tools/gen-badges.mjs` 从一张色表生成） | **改一改（概念可借）** | 可借两条纪律：① 「新增章三件套必须同批落，否则运行时不可达」——我们的对应坑是 `book.json` / `pages.json` / `reader/` 产物三者不同步；② 徽章/封面色表**单一真源 + 脚本生成**，比手工出图可维护 |

---

## 3 建议搬运清单（按投入产出比排序）

### ① 断网可演：整书离线包（1–2 天，最高性价比）

- **改**：`book2real/reader_app.py` 加 `GET /api/books/<id>/files`（照 `server.js:693-719`：白名单 id → 递归列 `books/<id>/reader/**` → 返回 `{files:[…],bytes}`）；`book2real/web/reader.html` 末尾加 SW 注册（`isSecureContext` 守卫，照 `public/js/app.js:1139`；非安全上下文照 `:1136-1141` 明示不可用）。
- **新建**：`book2real/web/sw.js`（照 `public/sw.js:1-45`，保留 `CACHE_V` 单点 + 逐项 add 失败只告警）、`book2real/web/offline.js`（照 `offline.js:85-144`，只留 `bookFiles/downloadBook/verifyBook/removeBook`）。
- **验收**：`python -m book2real reader` 打开一本书 → 点"离线下载"→ DevTools 切 Offline → **刷新后仍能翻页、图能出、已下音轨能播**；`localStorage` 有已下载 id；缓存清单里含 `reader.js`/`reader.css`/该页 `art/*.jpg`。

### ② 语音链路补完：句级 TTS + 缓存 + 时间轴（1 天，直接吃掉 S6/S7 部分指标）

- **改**：`book2real/reader_app.py:216-227` —— `/api/tts` 加 `probe=1`、`cache key = sha1(text|voice|rate)`、磁盘缓存（上限可配）、失败返回 `{"error":...,"fallback":"l1"}` 而非 500；`book2real/web/reader.js:383-392` —— `speak()` 改走新 `tts.js`，L1 `speechSynthesis` 保留为回落。
- **新建**：`book2real/web/tts.js`（搬 `l2tts.js:21-37` 句工厂 + `speech.js:14-46` 切句器/`RATES`/`interpChars`）。
- **验收**：同一句第二次播放**零合成**（服务端日志无新合成记录、首字节 < 50 ms）；字级高亮与音频偏差 < 150 ms；拔网后自动回落 `speechSynthesis` 且不报错。

### ③ 问答 + 追问：把 `augmentPrompt` 协议和苏格拉底状态机搬进来（2 天）

- **新建**：`book2real/qa.py`（页级语料 = `page.text` + `page.scene` + 角色 `desc`；提示词里原样写入 `ai-context.js:424-425` 那两句「须引用【教材原文】标注出处 / 未覆盖如实说明，不得编造」）；`book2real/socratic.py`（1:1 翻译 `ai-hint.js:186-244`，5 段 directive 文案原样保留）。
- **改**：`book2real/web/reader.js:315-319` `showSpot()` —— 点击热点后把 `{bbox, name, desc, page_text, scene}` POST 到 `/api/ask`，流式渲染回答；`:188-191` 空白处点击保持现状（场景描述）。
- **验收**：13 条红队用例（搬 `ai-context.js:638` `RED_TEAM_CASES`）全部按协议回应——索答不越级、越狱不破框、问书外内容如实说"本页未提及"；每条回答带页码引用；无命中时**不编造**（返回空 context 路径，照 `buildContext:401` 的诚实返回）。

### ④ 一条命令的质量门（1 天，交付材料直接用它出数据）

- **新建**：`scripts/gate.py` —— 五门复刻 `tools/qc.mjs:6-8`：① `python -m py_compile book2real/*.py`；② `pytest tests/ -q`；③ 启 `reader` 打 `/api/books` 探活；④ 页面覆盖率（`pages.json` 页数 vs `reader/book.json` 页数）；⑤ `/api/tts?probe=1`。
- **验收**：一条命令，全过 exit 0 / 任一失败 exit 1 并点名；故意把某页从 `book.json` 删掉，覆盖率门必须红。

### ⑤ server 工程头夯实（半天）

- **改**：`book2real/reader_app.py` —— 加 HTML 的 CSP + `nosniff`（照 `server.js:910-927`，`style-src` 留 `'unsafe-inline'`）、路径守卫改 `resolve()` 前缀校验（照 `server.js:281-285`）、未知路径 JSON 404、启动打印局域网 URL（照 `server.js:937-940`）。
- **验收**：`curl -I http://127.0.0.1:8770/` 能看到 `Content-Security-Policy` 与 `X-Content-Type-Options`；`curl "http://127.0.0.1:8770/books/../../etc/passwd"` 返回 403；终端启动能看到手机可访问的 IP。

---

## 4 不要搬的东西

| 东西 | 路径 | 理由 |
|---|---|---|
| 277 个学科测试与领域引擎 | `tests/c_*.mjs`、`tests/dsa_*.mjs` 等；`public/js/ds.js`（384 KB）、`py.js`（313 KB）、`mlz.js`（303 KB） | 是他们的教材内容资产（C 语言解释器、Python 解释器、数据结构动画），与绘本链路零交集；搬进来只会污染我们的测试时长与包体 |
| 五子棋与机器人 | `tools/gomoku_net_src.js`（13 MB）、`tests/robotics_*.mjs` | 体积荒谬且完全无关（任务书已指明跳过） |
| 教材内容审计器 | `tools/content_audit.mjs`（1-30 行管线） | 深度绑定他们的内容 DSL：`registerHooks` 映射 `/js/`、把 `exercise.js` 重定向成 data-URL stub、手写 DOM shim 逐节 `render()`、复用 `math.js:toTex` 与 `graders.js`。我们的"内容"是页图 + 分镜 JSON，这套管线没有落点。**只借概念**：链接/资源实物校验、答案自洽重算、混淆词表三件 |
| 覆盖率审计器 | `tools/coverage_check.mjs` | 依赖 `toc-source.json` 与节文件首行 `/* cov: */` 锚协议，我们没有这套格式；我们的覆盖口径是"页数 100%"，几行代码就够 |
| 安卓/桌面构建管线与端上壳 | `tools/build_android.mjs`（`CAP` 硬编码 `E:/AndroidDev/nn-academy-cap`）、`tools/build_desktop.mjs`（依赖不存在的 `desktop/`）、`tools/mobile_shots.mjs`（依赖不存在的 `tools/e2e/`）、`public/js/native-shell.js` | 公开仓库里这些脚本**跑不起来**（实测目录缺失）。抄流程与坑（clean、版本号、WebView 语法下限）即可，代码在别人机器上 |
| 86 篇调研 | `docs/RESEARCH_*.md` | 全部是教育类产品口径（FSRS、徽章、皮肤、安卓 M3、听读认知负荷），结论对我们不适用；只在需要具体坑位时按标题定点查，不通读 |
| 间隔重复与运营体系 | `public/js/retention.js`、`badges.js`、`plan.js`、`fsrs_tune.mjs`、`gen-srs-rewards.mjs` | SRS/成就/周计划是"长期学习者"产品的地基，绘本是"一次读完 + 家长陪伴"，运营面完全不同；赛题里也没有这两项要求 |
| 双主题皮肤与设计系统 | `public/css/skin-yuehai.css`、`public/js/motion.js`、`docs/DESIGN_双主题皮肤计划.md` | 我们的视觉是"纸书质感 + 页图"（`reader.css:2-4` 只有 5 个颜色变量），套他们的十二书色谱与 elevation 体系会把绘本做成交互教材的样子 |
| 无鉴权的 `/api/chat` | `AI-INTEGRATION.md:70-73`（自己写明「不做鉴权（本机工具定位）；若暴露到局域网请自行加反代鉴权」） | 我们现场要在局域网/平板上演示，模型密钥绝不能走这个形状；必须带 token 或只绑 127.0.0.1 + 现场代理 |

---

## 附：口径差异速查（为什么很多"像"的东西不能直接用）

| 维度 | nn-academy | 纸书智伴（我们） |
|---|---|---|
| 内容单位 | 文字段 + 判定函数（`docs/AUTHORING.md:39-58`） | 页图 + 分镜/beat JSON（`book2real/storybook.py`、`web/reader.js:654-663`） |
| 正确性来源 | 纯函数判分器 + node 单测（277 套） | 页覆盖率 + 镜头质检（`book2real/qc.py`）+ 原书忠实度 |
| 交互面 | 点选项 / 拖滑块 / 问 AI | 翻页 / 点角色与字 / 听 / 问 |
| AI 的角色 | 导师（不给答案，追问到底） | 讲述者 + 笔友（要回答问题，且不许编造） |
| 失败兜底 | 规则版 / 摘录卡 / L1 语音（`plan.js:147`、`ask.js:1-8`、`server.js:873`） | 静态页 / 预制音轨 / 离线包 |
| 合规压力 | 几乎没有（成人自学） | 儿童内容 + AI 生成标识 + 数据授权（`docs/赛题分析.md:63-67`） |
