# 实测指标（自动生成，勿手改）

> 生成命令：`python scripts/eval_metrics.py` · 生成时间：2026-11-14T21:26:40+08:00
> 原始数据：`runs/eval/metrics.json`
> 「实测值」列只写本次真跑出来的数字（未测写「未测（原因）」）；「目标」列是单独标注的**非实测**赛题目标（来源 `docs/赛题分析.md`），两列不混写。

## 环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows-11-10.0.26300-SP0 |
| 机器/架构 | AMD64 |
| CPU | Intel64 Family 6 Model 183 Stepping 1, GenuineIntel |
| GPU | 未使用（本机纯 CPU，无 GPU 参与） |
| Python | 3.14.4 |
| Node | v24.14.1 |

## 指标

| # | 指标 | 实测值（本次） | 目标（非实测，来源 docs/赛题分析.md） | n | 环境 | 时间戳 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 页面覆盖率 | 21/21 源页 = 100.0% | 100%（docs/赛题分析.md §6） | 2 本书 | Windows 11 / 纯 CPU / Py3.14.4 | 2026-11-14T21:26:37+08:00 | charlie_lola 20/20；guoqing 1/1；reader 共 31 屏；另有 1 本无 reader 包，未参与比对 |
| 2 | /api/tts 二次命中首字节 | 中位 63.9 ms / 最大 76.1 ms | —（赛题未单列；相关：语音首响 P95 ≤1.5 s，§6） | 10 | Windows 11 / 纯 CPU / Py3.14.4 | 2026-11-14T21:26:38+08:00 | 首次请求 156 ms 后，同一文本连续 10 次命中；X-TTS-Cache hit=10 / miss=0 / 失败=0 |
| 3 | /api/ask 规则模式首响 P50/P95 | P50 15.2 ms / P95 16.4 ms | 点击首响 P95 ≤1 s（docs/赛题分析.md §6、§4.2） | 30 | Windows 11 / 纯 CPU / Py3.14.4 | 2026-11-14T21:26:39+08:00 | 规则模式（source='rule', fallback='no_llm_key'）；30 次单请求含 HTTP 往返，失败 0 次；warm-up 45.8 ms |
| 4 | 前端静态资源总字节 | 117367 bytes（9/9 个资源） | —（赛题未单列） | 9 | Windows 11 / 纯 CPU / Py3.14.4 | 2026-11-14T21:26:39+08:00 | 全部 200：reader.html, reader.css, reader.js, qa.js, voice.js, tts.js, offline.js, sw.js, manifest.json |
| 5 | /api/ask 引用可回溯率 | 19/19 条 quote 为原文子串 = 100.0% | —（赛题未单列；相关：幻觉率 ≤5%，§6） | 19 | Windows 11 / 纯 CPU / Py3.14.4 | 2026-11-14T21:26:39+08:00 | 抽样 12 页/问（charlie_lola），citations 共 19 条；fallback 分布 {'no_llm_key': 12} |

## 说明

- 「实测值」只写这次脚本真跑出来的数字；没跑到的写「未测（原因）」，不是 0，也不是目标值。
- 「目标」列**不是实测**：逐条抄自 `docs/赛题分析.md` §6 的赛题目标，标「—」表示赛题没有给该指标目标；标「相关」只是列了相近的赛题目标供对照，不等价于本行指标。
- 覆盖率按 `source_page` 精确比对，扣掉 `curation.json` 里 `skip:true` 的源页；拆成多屏的源页算覆盖 1 页（屏数另计）。
- 「首字节」用原始 socket 计时：发请求 → 收到第一个 body byte；含 HTTP 往返。
