"""把官方模板 docs/纸书智伴项目书.docx 填成本队的项目说明书。

原则：
- 只用模板自己的段落/表格/样式，不另起排版；
- 模板里的【填写说明】、灰色提示、占位 "____" 一律替换成正文；
- 未知信息写成 "【待填】" 并加黄色高亮（删掉标签再填即可）；
- 数字全部取自仓库实测，和 docs/项目说明书-自排版.docx（上一版）一致。

用法：python scripts/build_manual.py [--template docs/纸书智伴项目书.docx] [--out docs/项目说明书.docx]
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

OLD_DOC = Path(".tmp/项目说明书-自排版.docx")
BLANK = "【待填】"

# 与《项目说明书》封面一致的已知信息
PROJECT = "纸书智伴"
TEAM = "纸间生灵"
SCHOOL = "中国科学院大学"
MEMBERS = "段柏旸 程浩珩 柯文轩 王一诺 袁汉林"
SUPERVISOR = "刘慧"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def keep_first_run(p):
    """只留第一个 run（保留它的字体格式），返回它。"""
    runs = p.runs
    if not runs:
        return None
    first = runs[0]
    for r in runs[1:]:
        r._element.getparent().remove(r._element)
    return first


def set_text(p, text: str, *, highlight: bool = False, bold: bool | None = None, body: bool = True):
    run = keep_first_run(p)
    if run is None:
        run = p.add_run("")
    run.text = text
    if highlight:
        run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    else:
        run.font.highlight_color = None
    if bold is not None:
        run.font.bold = bold
    if body:
        pf = p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(6)
        pf.line_spacing = 1.3
    return run


def clone_after(p, text: str = "", *, highlight: bool = False, bold: bool | None = None,
                body: bool = True):
    """复制段落格式，插到 p 之后。body=True 时套用正文节奏（段后 6pt、行距 1.3）。"""
    new_el = copy.deepcopy(p._element)
    p._element.addnext(new_el)
    from docx.text.paragraph import Paragraph
    new_p = Paragraph(new_el, p._parent)
    run = keep_first_run(new_p)
    if run is None:
        run = new_p.add_run("")
    run.text = text
    run.font.highlight_color = WD_COLOR_INDEX.YELLOW if highlight else None
    if bold is not None:
        run.font.bold = bold
    if body:
        pf = new_p.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(6)
        pf.line_spacing = 1.3
    return new_p


def add_after(p, text: str, *, highlight: bool = False, bold: bool = False):
    new_p = clone_after(p, text, highlight=highlight)
    run = new_p.runs[0] if new_p.runs else new_p.add_run(text)
    run.font.bold = bold
    return new_p


def set_rich(p, text: str):
    """写入文本，只给其中的 【待填】/【待确认】 标签加黄色高亮（其余不染色）。"""
    base = p.runs[0] if p.runs else p.add_run("")
    for extra in p.runs[1:]:
        extra._element.getparent().remove(extra._element)
    rpr = base._element.find(qn("w:rPr"))
    anchor = base._element
    marks = [BLANK, "【待确认】"]
    parts = []
    idx = 0
    while idx < len(text):
        candidates = [(text.find(m, idx), m) for m in marks if text.find(m, idx) >= 0]
        pos, mark = min(candidates) if candidates else (-1, None)
        if pos < 0:
            parts.append((text[idx:], False))
            break
        if pos > idx:
            parts.append((text[idx:pos], False))
        parts.append((mark, True))
        idx = pos + len(mark)
    if not parts:
        parts = [("", False)]
    for content, hl in parts:
        el = OxmlElement("w:r")
        if rpr is not None:
            el.append(copy.deepcopy(rpr))
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = content
        el.append(t)
        anchor.addnext(el)
        anchor = el
    base._element.getparent().remove(base._element)
    from docx.text.run import Run
    for child in p._element.findall(qn("w:r")):
        r = Run(child, p)
        if r.text in marks:
            r.font.highlight_color = WD_COLOR_INDEX.YELLOW
        else:
            r.font.highlight_color = None
    return p


def ensure_rows(table, needed: int):
    """把表格补到 needed 行（复制最后一行，保留边框）。"""
    while len(table.rows) < needed:
        table._tbl.append(copy.deepcopy(table.rows[-1]._tr))


def fill_cell(cell, text: str, *, highlight: bool = False):
    p = cell.paragraphs[0]
    for extra in cell.paragraphs[1:]:
        extra._element.getparent().remove(extra._element)
    if highlight or BLANK in text or "【待确认】" in text:
        set_rich(p, text)
    else:
        set_text(p, text)


# --------------------------------------------------------------------------- #
# 正文
# --------------------------------------------------------------------------- #
def build(template: Path, out: Path, old: Path) -> None:
    d = docx.Document(str(template))
    o = docx.Document(str(old))

    def OLD(i: int) -> str:
        return o.paragraphs[i].text.strip()

    P = d.paragraphs  # 模板段落（索引见 .tmp/template_structure.txt）

    # ---------------- 封面 ----------------
    set_text(P[6], f"作品名称：{PROJECT}")
    set_text(P[7], f"队　　伍：{TEAM}")
    set_text(P[8], "学　　校：中国科学院大学")
    set_text(P[9], f"队　　员：{MEMBERS}")
    set_text(P[10], f"指导教师：{SUPERVISOR}")
    set_rich(P[11], f"内容安全复核责任人：{BLANK}")
    set_rich(P[12], f"联系方式：{BLANK}")
    set_rich(P[13], f"提交日期：{BLANK}")
    P[14]._element.getparent().remove(P[14]._element)   # 删灰色提示行

    # 目录补页码（模板没有页码，这里给到章节起始页，导出后按 PDF 校对）
    toc_pages = {
        17: "1 摘要", 18: "2 需求分析", 19: "3 系统架构", 20: "4 数据与模型来源",
        21: "5 主要功能", 22: "6 关键实现与方法", 23: "7 测试结果", 24: "8 局限性与风险",
        25: "9 规范合规与伦理", 26: "附录",
    }
    for idx in toc_pages:
        set_text(P[idx], P[idx].text.strip())

    # ---------------- 1 摘要 ----------------
    summary_idx = [27, 28, 29, 30, 31]
    first = P[29]
    set_text(first, OLD(summary_idx[0]))
    cursor = first
    for i in summary_idx[1:]:
        cursor = clone_after(cursor, OLD(i))

    set_text(P[31], OLD(33))                       # 1.1 系统概述

    # 1.2 核心技术链路（正文，不再放表）
    p12 = P[33]
    set_text(p12, "系统只有一条主链：import-pdf 抽页图与 PDF 文本层并记 sha256 → analyze 做 OCR、"
                  "正文列选择、拼音/分词与清字，产出 reader 包 → film 单趟逐帧排版渲染（1280×720 / 24 fps），"
                  "一次编码后只混入音轨，并写出 timeline.json。交互侧与成片共用同一份 timeline："
                  "跳帧、点选、描述帧、查字都落在同一套坐标上，不存在第二套标注。")

    # 1.3 关键结果摘要
    p13 = P[35]
    results = [
        "样书导入：6 本官方样书全部导入（3 中 3 英，120 源页 / 123 屏），质量门覆盖 120/120。",
        "成片：cn01《一起搭积木吧》114.79 s、en01《Going to the Cinema》150.38 s，"
        "1280×720 / 24 fps / H.264 + AAC，均在 100–200 s 区间。",
        "交互录屏：cn01 30.68 s、en01 22.92 s，1276×804 / 25 fps，真实浏览器操作，未二次剪辑。",
        "检索四能力：按句跳帧、点选目标暂停并介绍、描述当前帧、查字/词并框出；"
        "两例实测均命中第 12 页（tests/test_film.py 9 passed）。",
        "质量门与测试：python scripts/gate.py 5 通过 0 失败；tests/test_*.py 合计 190 个用例通过。",
        "打包预演：8 通过 0 失败（源码 316 文件 / 33.8 MB）；提交包整体 78.5 MB。",
    ]
    set_text(p13, results[0])
    cursor = p13
    for line in results[1:]:
        cursor = clone_after(cursor, line)

    # 1.4 创新点
    innov = [
        "① 单趟渲染，时间轴同源。成片与检索共用一份 timeline.json：每个镜头带 t0/t1、source_page、"
        "art_rect、spots、ocr_lines；视频不做二次剪辑，检索框也落在同一坐标系上。",
        "② 帧内坐标可回投。点击先减 contain 留白、除缩放，换算成插图归一化坐标，再回到源页像素；"
        "服务端回填 source_w/source_h 并标注 mapping=book|pages|fallback，框不准时明确说。",
        "③ 单步降级，不假装成功。无 LLM key 走抽取式规则并给页码+原文引用；qa 异常、TTS 失败、"
        "预算不足、英文缺识别器各有一条降级路径，HTTP 不返回 500。",
        "④ 预算护栏先行。每次云端调用前先扣预算再发请求，逐次写 runs/cloud/ledger.jsonl；"
        "超出 CLOUD_MAX_SPEND_CNY 直接抛 BudgetError。",
    ]
    set_text(P[37], innov[0])
    cursor = P[37]
    for line in innov[1:]:
        cursor = clone_after(cursor, line)
    P[38]._element.getparent().remove(P[38]._element)
    P[39]._element.getparent().remove(P[39]._element)

    # ---------------- 2 需求分析 ----------------
    set_text(P[42], OLD(47))
    clone_after(P[42], OLD(48))

    # 2.2 赛题目标与核心任务对照
    mapping = [
        "① 6 本授权绘本样本 → 6 本全部导入（books/cn01–cn03、en01–en03，120 源页）。",
        "② 每本 50 条 Query → 实收 223 个 wav（要求 6×50=300，缺口 77 条，已向组委会反馈）。",
        "③ 提交 1 中 1 英两本 → cn01、en01 成片 + 交互录屏齐备。",
        "④ 成片 100–200 s、≥720P、≥24 fps → 114.79 s / 150.38 s，1280×720 / 24 fps。",
        "⑤ 一次生成、不二次编辑 → 单趟 encode_frames，仅 mux 音轨（第 6 章）。",
        "⑥ 语音首响 2 s（Q9/Q34 口径）→ 分项已测（TTS 缓存首字节中位 63.9 ms），端到端未测。",
        "⑦ 回答正确率 ≥0.60 → 问答正确率未测；检索 top1 页命中率 cn01 0.857(6/7)、en01 0.545(6/11)。",
        "⑧ 视频内检索四能力 → 跳帧 / 点选 / 描述帧 / 查字并框出，均已实现并可演示。",
        "⑨ 触控到独立语义对象级 → spots 进时间轴（cn01 31 个、en01 45 个），点选即暂停并展示介绍。",
        "⑩ 中英双语与跨语言问答 → 两语种成片跑通；跨语言问答未验。",
        "⑪ 现场新绘本实时生成 → import→analyze→film 单机可跑，无计时演练。",
        "⑫ 官方模型三件套 → 代码已接，key 未下发，真实调用未验证。",
    ]
    set_text(P[44], mapping[0])
    cursor = P[44]
    for line in mapping[1:]:
        cursor = clone_after(cursor, line)

    # 2.3 功能需求
    funcs = [
        "FR-1 任意绘本 PDF 导入：页图 + 文本层 + sha256，页数与源 PDF 一致。",
        "FR-2 互动阅读：点字给读音/词义，点画给场景/角色，可翻页与朗读。",
        "FR-3 单趟成片：100–200 s、720P/24 fps、带旁白、附 timeline。",
        "FR-4 视频内检索：跳帧、点选、描述帧、查字/词并框出，四项都要能演示。",
        "FR-5 语音问答：麦克风提问，答案带页码与原文引用；无 key 仍可答。",
        "FR-6 触控点选：点击目标即暂停并展示介绍与页码。",
        "FR-7 评测：gate 五门、指标脚本、Query 报告三者可复跑。",
        "FR-8 打包：4 份材料 + 源码 zip，命名 学校-院系-姓名.zip。",
    ]
    set_text(P[46], funcs[0])
    cursor = P[46]
    for line in funcs[1:]:
        cursor = clone_after(cursor, line)

    # 2.4 非功能需求
    nonfunc = [
        "运行环境：无 GPU 的单机可完整演示（Windows 11，Python 3.14.4，纯 CPU）。",
        "可复现：每次生成可定位到文件与段落（manifest.json + timeline.json + book.json sha256）。",
        "容错：外部依赖失败不返回 500，逐条降级（第 6 章降级矩阵）。",
        "隐私：不采集真实儿童数据；语音走浏览器识别，音频不进本机服务。",
        "成本：云端调用有硬上限 CLOUD_MAX_SPEND_CNY，逐笔记账。",
        "体积：源码包 316 文件 / 33.8 MB，远低于 2 GB。",
    ]
    set_text(P[48], nonfunc[0])
    cursor = P[48]
    for line in nonfunc[1:]:
        cursor = clone_after(cursor, line)

    # ---------------- 3 系统架构 ----------------
    set_text(P[52], OLD(62))        # 填写说明→分层描述
    set_text(P[53], OLD(63))        # 数据流与旁路
    # 3.2 四个模块（去掉与模板小标题重复的"（N）…模块。"前缀）
    def strip_label(s: str) -> str:
        return s.split("。", 1)[1].strip() if s.startswith("（") and "。" in s else s

    set_text(P[56], strip_label(OLD(65)))
    set_text(P[58], strip_label(OLD(66)))
    set_text(P[60], strip_label(OLD(67)))
    set_text(P[62], strip_label(OLD(68)))

    # 3.3 部署与运行环境（表 0）
    t0 = d.tables[0]
    fill_cell(t0.cell(1, 1), "Windows 11（10.0.26300）；Chrome（交互录屏 1276×804）")
    fill_cell(t0.cell(2, 1), "Python 3.14.4；标准库 http.server；前端原生 ESM，无构建步骤")
    fill_cell(t0.cell(3, 1), "未使用 GPU；纯 CPU；云端三件套仅接代码，未真实调用")
    fill_cell(t0.cell(4, 1), "本地：RapidOCR(onnxruntime)、pypinyin、jieba、OpenCV、ffmpeg；"
                             "可选：DeepSeek / Seedream / Seedance、edge-tts")
    fill_cell(t0.cell(5, 1), "python -m book2real reader --books books --port 8770（可选 --host 0.0.0.0）")

    # ---------------- 4 数据与模型来源 ----------------
    P[66]._element.getparent().remove(P[66]._element)    # 删【重要】提示

    t1 = d.tables[1]
    data_rows = [
        ("官方纸质绘本 PDF（3 中 3 英，6 本 / 284.8 MiB）", "组委会", "唯一输入（OCR、排版、成片）", "赛事授权，仅本赛事", "是"),
        ("Query 测试音频 223 个 wav（要求 300）", "组委会", "语音问答与检索评测", "赛事授权", "是（数量不足）"),
        ("交互录制脚本 6 个 TXT（含【必选】动作）", "组委会", "录制交互演示视频", "赛事授权", "是"),
        ("已导入页图与 reader 包（120 源页 / 123 屏）", "由官方 PDF 派生", "阅读、成片、检索", "赛事授权（派生物）", "派生"),
        ("成片、时间轴与交互录屏", "本项目生成", "交付与演示", "本队作品", "否"),
    ]
    for r, row in enumerate(data_rows, start=1):
        ensure_rows(t1, r + 1)
        for c, val in enumerate(row):
            fill_cell(t1.cell(r, c), val)

    t2 = d.tables[2]
    model_rows = [
        ("文本/故事/问答", "DeepSeek（OpenAI 兼容）", "deepseek-chat", "闭源", "仅推理；预算护栏；key 未下发", "待补【待确认】"),
        ("文生图", "豆包 Seedream（火山方舟）", "doubao-seedream-3-0-t2i-250415", "闭源", "cloud --image；补图演示", "待补【待确认】"),
        ("文生视频", "豆包 Seedance（火山方舟）", "doubao-seedance-1-0-lite-t2v-250428", "闭源", "cloud --video；试片", "待补【待确认】"),
        ("文字识别", "RapidOCR（PP-OCR）", "≥1.3", "开源", "本地离线，全量 6 本", "Apache-2.0"),
        ("拼音/分词", "pypinyin / jieba", "0.55.0 / vendor", "开源", "本地，拼音与换行", "MIT"),
        ("清字/编码", "OpenCV Telea / ffmpeg", "≥4.8 / 系统版", "开源", "本地清字与编码", "Apache-2.0 / LGPL-GPL"),
        ("旁白 TTS", "edge-tts（晓晓 / Aria）", "7.3.1（vendor）", "开源", "成片音轨；仅演示与评测", "GPL-3.0"),
    ]
    for r, row in enumerate(model_rows, start=1):
        for c, val in enumerate(row):
            fill_cell(t2.cell(r, c), val, highlight=("【待确认】" in val))

    set_text(P[72], "是否进行微调：否。仓库无训练脚本、无自训权重；6 本样书页图、OCR 与音频未用于任何预训练或微调，"
                    "隐藏测试集未接触、未获取。云端三件套只作为推理接口接入（cloud.py），当前 key 未填写，尚无真实调用流水；"
                    "接入后也只做推理，不做留存训练。")

    set_text(P[77], "本队补充承诺：只使用组委会授权素材，不复制、不转授权、不用于预训练；"
                    "云端服务仅用于本赛事推理，调用范围与额度按 11.30 下发的账号条款执行；"
                    "赛后按通知删除受限数据及本地副本。")

    # ---------------- 5 主要功能 ----------------
    set_text(P[79], OLD(86))
    cursor = P[79]
    feats = [
        "① 互动阅读：选书 → 翻页 → 点字/点画 → 朗读/问答。reader.js 渲染，/api/ask 作答；cn01 182 行 OCR、12 屏。",
        "② 单趟成片：选书 → film → 得到 mp4 + timeline。storyfilm.py 逐帧渲染，114.79 s / 150.38 s。",
        "③ 视频内检索：输入一句话 → 跳到该帧并暂停 → 框出目标或文字；film.js 的 runFind / findWord / jump。",
        "④ 语音问答：按住麦克风说话 → 转写 → 作答并朗读；规则首响 P95 16.4 ms，TTS 缓存首字节 63.9 ms。",
        "⑤ 触控点选：点画面目标 → 立即暂停 → 展示名称/介绍/页码；cn01 31、en01 45 个 spots。",
        "⑥ 现场导入出片：网页选 PDF → 后台任务 → 进度 → 出片；两本官方 PDF 实跑，无 GPU 单机可跑。",
    ]
    for line in feats:
        cursor = clone_after(cursor, line)

    # 插图
    shot = Path("runs/_book_doc/film-search-clean.png")
    if shot.exists():
        pic_p = clone_after(cursor, "")
        run = pic_p.add_run()
        run.add_picture(str(shot), width=Cm(14.5))
        pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap = clone_after(pic_p, "图 1　视频内检索：查询「找到搭了九块积木的画面」，跳到 1:42（第 12 页）并框出积木城堡")
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cursor = cap

    # ---------------- 6 关键实现与方法 ----------------
    impl_blocks = [
        ("6.1 单趟逐帧渲染：为什么满足“一次生成、不二次编辑”", [
            OLD(93),
            "旧 run 管线里的逐页 clip + concat 仍留在代码中，但交付命令固定为 film；本说明书写清这一点，"
            "避免被理解为“同一册做了二次剪辑”。",
        ]),
        ("6.2 时间轴映射：成片、点击框、检索共用一套坐标", [OLD(98)]),
        ("6.3 点击命中：从窗口坐标回到源页", [
            OLD(103), OLD(104), OLD(105), OLD(106),
        ]),
        ("6.4 查找与描述帧", [OLD(108), OLD(109)]),
        ("6.5 预算护栏与云端接入", [OLD(111), OLD(112)]),
        ("6.6 降级矩阵", [
            "六个失败点各有一条明确退路：无 LLM key → 抽取式规则并标 fallback=no_llm_key；"
            "qa 模块异常 → 200 + qa_unavailable，不抛 500；TTS 合成失败 → 503 + 前端回退 speechSynthesis；"
            "云端预算不足 → BudgetError，请求不发；英文无识别器 → 逐条 asr_unavailable 并跳过；"
            "成片不存在 → 404 JSON。",
        ]),
        ("6.7 多 Agent 协同", [OLD(117)]),
    ]
    head_ref = P[30].runs[0].font if P[30].runs else None
    cursor = P[82]
    for head, paras in impl_blocks:
        cursor = clone_after(cursor, head, body=False)
        hr = cursor.runs[0]
        hr.font.bold = True
        if head_ref is not None:
            hr.font.size = head_ref.size
            hr.font.name = head_ref.name
        cursor.paragraph_format.space_before = Pt(10)
        cursor.paragraph_format.space_after = Pt(4)
        for para in paras:
            cursor = clone_after(cursor, para, bold=False)

    # ---------------- 7 测试结果 ----------------
    t3 = d.tables[3]
    fill_cell(t3.cell(1, 1), "x86_64（Intel64 Family 6 Model 183）；纯 CPU，无 GPU 参与")
    fill_cell(t3.cell(2, 1), "Windows 11 10.0.26300；Python 3.14.4；Node v24.14.1；ffmpeg 在 PATH")
    fill_cell(t3.cell(3, 1), "scripts/gate.py、scripts/eval_metrics.py、book2real evaluate、scripts/package_submission.py")
    fill_cell(t3.cell(4, 1), "6 本官方绘本（120 源页）/ Query 音频 223 个 wav；合成用例 190 个")
    fill_cell(t3.cell(5, 1), "2026-11-16（gate 与测试为最近一次实测；metrics 为 2026-11-14 快照）")

    t4 = d.tables[4]
    metrics = [
        ("风格一致性", "未做专家盲评；成片取自原书页图，风格随原书", "专家盲评+指标", "缺口：无盲评材料"),
        ("页间连贯性", "cn01 13 段、en01 29 段逐页对齐，无掉页", "专家盲评+指标", "覆盖门 120/120"),
        ("图文内容准确度", "正文逐页呈现，OCR 与原文可核对", "专家盲评+指标", "cn01 182 行 / en01 110 行"),
        ("生成效率", "cn01 墙钟 354.8 s、en01 401.0 s（纯 CPU）", "系统实测", "成片 114.79 / 150.38 s"),
        ("语音问答正确率", "未测；检索 top1 页命中 cn01 0.857(6/7)、en01 0.545(6/11)", "验证集/隐藏测", "门槛≥0.60；分母仅 7/11"),
        ("视频内检索准确率", "两例实测均命中第 12 页并框出目标", "验证集/隐藏测", "tests/test_film.py 9 passed"),
        ("端到端延迟", "未测；分项：TTS 缓存首字节 63.9 ms、规则首响 P95 16.4 ms", "系统实测", "缺“话音结束→TTS 首音”"),
        ("实时性稳定性", "gate 5 门全绿；降级矩阵 6 条均不 500", "系统实测", "2026-11-16 实测"),
        ("识别准确率", "spots 进时间轴（cn01 31 / en01 45）；逐对象命中率未测", "验证集/隐藏测", "缺口：抽帧点击验收"),
        ("链路完整性/稳定性", "5 agent 链 + Critic 2/2 接受，人工只认领候选", "演示+代码", "book2real/cast/"),
    ]
    for r, (sub, result, how, note) in enumerate(metrics, start=1):
        fill_cell(t4.cell(r, 2), result)
        fill_cell(t4.cell(r, 3), how)
        fill_cell(t4.cell(r, 4), note)

    def strip_head(s: str) -> str:
        """去掉正文里与模板小标题重复的"（1）成功案例："式前缀。"""
        if s.startswith("（") and "：" in s:
            head, rest = s.split("：", 1)
            if len(head) <= 12:
                return rest.strip()
        return s

    set_text(P[90], strip_head(OLD(127)))
    set_text(P[92], strip_head(OLD(128)))

    # ---------------- 8 局限性与风险 ----------------
    limits = [
        "成片是程序化排版 + 运镜，不是文生视频：风格一致性与“生成感”依赖版式，无 Seedance 轨。",
        "SAPI zh-CN 转写差：CER 约 0.67，语音查询经 ASR 后可能直接失效（现另有 faster-whisper 本地后端）。",
        "英文缺语种识别器：en01 的 ASR 一律 asr_unavailable，只能测文本查询。",
        "云端三件套未验：key 未下发，不能声称“已调通”；官方型号与代码实配不一致。",
        "隐藏测试集未测：跨书泛化、隐藏 Query 准确率无数据。",
        "端到端首响未测：只有分项（TTS 63.9 ms、规则 16.4 ms）。",
        "检索受字面匹配限制：概念词不在页面文字里时会漏。",
        "6 本只出 2 本成片：另外 4 本只有 reader 包。",
    ]
    set_text(P[95], limits[0])
    cursor = P[95]
    for line in limits[1:]:
        cursor = clone_after(cursor, line)
    P[96]._element.getparent().remove(P[96]._element)
    P[97]._element.getparent().remove(P[97]._element)

    import re as _re
    para82 = OLD(134)
    para82 = _re.sub(r"复核责任人仍为\s*(?:____|【待填】)", "复核责任人尚未指定（见 §9.1）", para82)
    set_rich(P[99], para82)

    set_text(P[101], "短期（1–2 周）：拿到云端 key 后各跑一次 --llm/--image/--video 并留 ledger；"
                     f"落实内容安全复核责任人（现为 {BLANK}）；补 50 条 Query 标注与端到端首响表。",
            highlight=False)
    set_text(P[102], "中期：其余 4 本出片与交互录屏；英文 ASR 换可用后端；补跨语言问答与触控抽帧验收；"
                     "Seedance 试片接入旁路（不拼进交付片），成片仍单趟。")

    # ---------------- 9 规范合规 ----------------
    t5 = d.tables[5]
    checks = [
        ("仅使用组委会授权绘本样本", "是", "6 本官方 PDF，无其它输入"),
        ("模型/工具/数据来源已完整披露", "部分", "开源组件已列；三件套条款与额度待 key 下发后补【待确认】"),
        ("未使用隐藏测试集标准答案", "是", "仓内无隐藏集数据"),
        ("关键生成内容可定位、可复现", "是", "manifest + timeline + book.json sha256"),
        ("已指定内容安全复核责任人", "否", f"仍为 {BLANK}，提交前必须落实"),
        ("输出含年龄适配标注", "是", "book.json age_range=4-8；阅读页/成片页徽标"),
        ("理解并遵守生成内容公开使用边界", "是", "未上传任何公开平台"),
    ]
    for r, (item, yesno, note) in enumerate(checks, start=1):
        fill_cell(t5.cell(r, 1), yesno, highlight=("【待确认】" in note))
        fill_cell(t5.cell(r, 2), note, highlight=("【待确认】" in note or BLANK in note))

    # 增补一行：预训练与删除
    extra = copy.deepcopy(t5.rows[-1]._tr)
    t5._tbl.append(extra)
    fill_cell(t5.cell(8, 0), "未用于预训练/微调", )
    fill_cell(t5.cell(8, 1), "是")
    fill_cell(t5.cell(8, 2), "无训练脚本与自训权重")

    # ---------------- 附录 ----------------
    steps = [
        "1. 环境准备：pip install -r requirements.txt（另需 ffmpeg 在 PATH）；Python ≥3.10，无 GPU。",
        "2. 导入：python -m book2real import-pdf \"纸书智伴企业预选赛绘本素材/绘本素材/中文绘本01.pdf\" --id cn01",
        "3. 阅读包：python -m book2real analyze --book books/cn01",
        "4. 成片：python -m book2real film --book books/cn01 → runs/cn01/reader_film/<ts>/film.mp4（约 115 s）",
        "5. 服务：python -m book2real reader --books books --port 8770 --open → http://127.0.0.1:8770/ 与 /film?book=cn01",
        "6. 演示路径：读本 → 点字/点画 → 提问 → /film 检索 → 点选目标（四项检索能力逐项过）。",
        "7. 门禁与打包：python scripts/gate.py；python scripts/package_submission.py --name \"学校-院系-姓名\" --out dist",
        "故障备用：成片缺失时用 runs/cn01/reader_film/20261116-161824/film.mp4 与 "
        "runs/en01/reader_film/20261116-161136/film.mp4；检索页打不开时用图 1 讲解；云端未就绪不影响本地链路。",
    ]
    set_text(P[108], steps[0])
    cursor = P[108]
    for line in steps[1:]:
        cursor = clone_after(cursor, line)

    tree = (
        "Book2Real/\n"
        "├─ book2real/            # 代码包\n"
        "│  ├─ ingest.py          # PDF → books/<id>（页图/文本层/sha256）\n"
        "│  ├─ storybook.py       # OCR → 正文列 → 拼音/分词 → 清字 → reader 包\n"
        "│  ├─ storyfilm.py       # 单趟逐帧成片 + timeline.json\n"
        "│  ├─ reader_app.py      # HTTP 服务：阅读/检索/问答/TTS/导入/出片\n"
        "│  ├─ qa.py              # 页级问答（规则 / 可选 DeepSeek）\n"
        "│  ├─ cloud.py           # DeepSeek/Seedream/Seedance + 预算护栏 + ledger\n"
        "│  ├─ evaluate.py        # Query 评测（ASR/检索/问答/CER）\n"
        "│  ├─ cast/              # 人物链 5 agent\n"
        "│  └─ web/               # reader.* film.* qa.js voice.js tts.js sw.js\n"
        "├─ books/<id>/           # pages/ text/ book.json reader/（gitignore 页图与派生）\n"
        "├─ runs/<id>/            # reader_film/<ts>/、interactive/、eval/（gitignore）\n"
        "├─ scripts/              # gate.py、eval_metrics.py、package_submission.py\n"
        "└─ docs/                 # 本说明书、合规、评测、交接\n\n"
        "定位一次生成：runs/<id>/reader_film/<时间戳>/manifest.json（规格/时长/墙钟）与同目录 timeline.json"
        "（逐段 source_page/art_rect/t0/t1）；books/<id>/book.json 有源 PDF sha256。"
    )
    set_text(P[110], tree)

    set_text(P[112], "见第 4 章表 1（数据与素材）与表 2（模型与第三方服务）。可复现入口："
                     "docs/COMPLIANCE.md §2 给出逐项位置，docs/EVALUATION.md 给出评测口径与已知局限。")

    d.save(str(out))
    print(f"written: {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="docs/纸书智伴项目书.docx")
    ap.add_argument("--out", default="docs/项目说明书.docx")
    ap.add_argument("--old", default=str(OLD_DOC))
    args = ap.parse_args()
    build(Path(args.template), Path(args.out), Path(args.old))


if __name__ == "__main__":
    main()
