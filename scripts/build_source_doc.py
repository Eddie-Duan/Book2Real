"""生成《素材来源清单及知识产权承诺书》.docx

对应官方赛题材料提交第 d 项（docs/纸书智伴.pdf 第 4 页 5.d）：
    "数据、模型、工具、图片、文本、音频等素材来源清单及知识产权承诺书"

官方未提供单独模板（答疑 Q31：知识产权承诺书、数据使用承诺没有单独模板），
因此本文件自行编制，版式与 docs/项目说明书.docx 保持一致（A4、宋体 10.5pt、标题 Arial）。

所有 "【待填】" 标签都会加黄色高亮，表示"需要人确认/填写"；
                            标签是给人删的 —— 删掉标签再输入，就不会继承黄色。

用法：
    python scripts/build_source_doc.py                # 写 docs/素材来源清单及知识产权承诺书.docx
    python scripts/build_source_doc.py --out somewhere.docx
"""

from __future__ import annotations

import argparse
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HEAD_COLOR = RGBColor(0x1F, 0x1B, 0x16)
BLANK_TEXT = "【待填】"
CONFIRM_TEXT = "【待确认】"

# 与《项目说明书》封面保持一致的已知信息（改了这里重跑即可，两处不会漂）
TEAM = "纸间生灵"
SCHOOL = "中国科学院大学"
MEMBERS = "段柏旸 程浩珩 柯文轩 王一诺 袁汉林"
SUPERVISOR = "刘慧"


# --------------------------------------------------------------------------- #
# 基础排版
# --------------------------------------------------------------------------- #
def _set_run_font(run, *, size: float = 10.5, bold: bool = False, ascii_font: str = "Times New Roman",
                  east_asia: str = "宋体", color: RGBColor | None = None) -> None:
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = ascii_font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ascii_font)
    rfonts.set(qn("w:hAnsi"), ascii_font)
    rfonts.set(qn("w:eastAsia"), east_asia)
    if color is not None:
        run.font.color.rgb = color


def _style_font(style, *, ascii_font: str, east_asia: str, size: float, bold: bool | None = None,
                color: RGBColor | None = None) -> None:
    style.font.name = ascii_font
    style.font.size = Pt(size)
    if bold is not None:
        style.font.bold = bold
    if color is not None:
        style.font.color.rgb = color
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ascii_font)
    rfonts.set(qn("w:hAnsi"), ascii_font)
    rfonts.set(qn("w:eastAsia"), east_asia)


def _shade(cell, hex_fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def _cell_text(cell, text: str, *, size: float = 9.0, bold: bool = False,
               highlight: bool = False, align: WD_ALIGN_PARAGRAPH | None = None) -> None:
    cell.text = ""
    para = cell.paragraphs[0]
    para.paragraph_format.space_before = Pt(1.5)
    para.paragraph_format.space_after = Pt(1.5)
    para.paragraph_format.line_spacing = 1.05
    if align is not None:
        para.alignment = align
    run = para.add_run(text)
    _set_run_font(run, size=size, bold=bold)
    if highlight:
        run.font.highlight_color = WD_COLOR_INDEX.YELLOW


def _para(doc, text: str, *, size: float = 10.5, bold: bool = False, first_indent: bool = True,
          space_after: float = 4, align=None, highlight: bool = False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(space_after)
    pf.line_spacing = 1.36
    if first_indent:
        pf.first_line_indent = Pt(size * 2)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    _set_run_font(run, size=size, bold=bold)
    if highlight:
        run.font.highlight_color = WD_COLOR_INDEX.YELLOW
    return p


def _heading(doc, text: str, level: int = 1) -> None:
    style = f"Heading {level}"
    h = doc.add_heading(level=level)
    h.paragraph_format.space_before = Pt(10 if level == 1 else 7)
    h.paragraph_format.space_after = Pt(4)
    run = h.add_run(text)
    _set_run_font(run, size=15 if level == 1 else 12, bold=True, ascii_font="Arial",
                  east_asia="黑体", color=HEAD_COLOR)


def _table(doc, rows: list[list[str]], widths_cm: list[float] | None = None,
           header: bool = True, font_size: float = 8.6,
           highlighted: set[tuple[int, int]] | None = None):
    highlighted = highlighted or set()
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    # 表头跨页重复 + 行不跨页断开
    for r_idx, row in enumerate(table.rows):
        tr_pr = row._tr.get_or_add_trPr()
        tr_pr.append(OxmlElement("w:cantSplit"))
        if header and r_idx == 0:
            tr_pr.append(OxmlElement("w:tblHeader"))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            is_head = header and r == 0
            _cell_text(cell, value, size=font_size, bold=is_head,
                       highlight=(r, c) in highlighted)
            if is_head:
                _shade(cell, "EDEAE4")
                for para in cell.paragraphs:
                    para.paragraph_format.keep_with_next = True
            if widths_cm:
                cell.width = Cm(widths_cm[c])
    return table


def _caption(doc, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    _set_run_font(run, size=9, bold=True)


def _note(doc, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.25
    run = p.add_run(text)
    _set_run_font(run, size=9)
    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)


def _add_page_number(paragraph) -> None:
    run = paragraph.add_run()
    for tag, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag is None:
            instr = OxmlElement("w:instrText")
            instr.set(qn("xml:space"), "preserve")
            instr.text = text
            run._element.append(instr)
        else:
            fld = OxmlElement("w:fldChar")
            fld.set(qn("w:fldCharType"), tag)
            run._element.append(fld)
    _set_run_font(run, size=9)


# --------------------------------------------------------------------------- #
# 文档内容
# --------------------------------------------------------------------------- #
def build(out_path: Path) -> Document:
    doc = Document()

    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.left_margin, section.right_margin = Cm(2.4), Cm(2.2)
    section.top_margin, section.bottom_margin = Cm(2.3), Cm(2.2)

    _style_font(doc.styles["Normal"], ascii_font="Times New Roman", east_asia="宋体", size=10.5)
    for lvl, size in ((1, 15), (2, 12), (3, 10.5)):
        _style_font(doc.styles[f"Heading {lvl}"], ascii_font="Arial", east_asia="黑体",
                    size=size, bold=True, color=HEAD_COLOR)

    # 页脚页码
    footer_p = section.footer.paragraphs[0]
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _add_page_number(footer_p)

    # ---------------- 封面区 ----------------
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(24)
    run = p.add_run("第二届北京市大学生“人工智能+”创新大赛")
    _set_run_font(run, size=13, bold=True, ascii_font="Arial", east_asia="黑体")

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("企业赛题 · 素材来源清单及知识产权承诺书")
    _set_run_font(run, size=18, bold=True, ascii_font="Arial", east_asia="黑体", color=HEAD_COLOR)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(12)
    run = p.add_run("纸书智伴 —— 纸质图书多模态智能升级与智能伴读")
    _set_run_font(run, size=11)

    info_rows = [
        ["作品名称", "纸书智伴"],
        ["队伍名称", TEAM],
        ["学　　校", SCHOOL],
        ["院　　系", "计算机" + CONFIRM_TEXT],
        ["队　　员", MEMBERS],
        ["指导教师", SUPERVISOR],
        ["内容安全复核责任人", BLANK_TEXT],
        ["联系方式", BLANK_TEXT],
        ["提交日期", BLANK_TEXT],
    ]
    info = doc.add_table(rows=len(info_rows), cols=2)
    info.style = "Table Grid"
    info.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r, (key, value) in enumerate(info_rows):
        _cell_text(info.cell(r, 0), key, size=9.5, bold=True)
        _shade(info.cell(r, 0), "F4F2EE")
        _cell_text(info.cell(r, 1), value, size=9.5, highlight=value == BLANK_TEXT or CONFIRM_TEXT in value)
        info.cell(r, 0).width = Cm(4.6)
        info.cell(r, 1).width = Cm(9.6)

    _note(doc, "说明：黄色高亮处为需要本队确认或填写的信息。本文件数字与状态均为 2026-11-20 工作区实测；"
               "未验证项按实际标注，不以目标值代替实测值。")

    # ---------------- 依据 ----------------
    _heading(doc, "〇、本文件的依据与定位")
    _para(doc, "本文件对应官方赛题《纸书智伴》材料提交第 d 项：“数据、模型、工具、图片、文本、音频等素材来源清单及知识产权承诺书”"
               "（docs/纸书智伴.pdf 第 4 页 5.d）。官方未随发该件的单独模板，答疑 Q31 也明确“知识产权承诺书、数据使用承诺没有单独模板”，"
               "因此本队按赛事要求自行编制，并与《项目说明书》第 4 章、第 9 章同源，不重复编造第二套口径。", first_indent=True)
    _para(doc, "提交形态：本文件独立成册随提交包提供；其中第 3 节承诺条款同时以摘要形式写入《项目说明书》第 9.3 节，"
               "两者冲突时以本文件（含签署页）为准。", first_indent=True)

    # ---------------- 一、数据与素材 ----------------
    _heading(doc, "一、数据与素材来源清单")
    _caption(doc, "表 1-1　组委会提供的输入素材（唯一输入，不外采、不转用）")
    _table(doc, [
        ["名称", "规模 / 状态", "用途", "授权 / 许可", "存放位置", "随包分发"],
        ["官方纸质绘本 PDF（3 中 3 英）", "6 本 / 284.8 MiB", "唯一输入（OCR、排版、成片）", "赛事授权，仅本赛事使用",
         "纸书智伴企业预选赛绘本素材/绘本素材/*.pdf", "否（.gitignore:32）"],
        ["Query 测试音频", "223 个 wav（要求 6×50=300，实收不足，见 §六）", "语音问答与检索评测", "赛事授权",
         "同上，中文绘本0N/、英文绘本0N/", "否"],
        ["交互录制脚本", "6 个 TXT（含【必选】动作序列）", "录制交互演示视频", "赛事授权", "同上", "否"],
        ["附件使用说明", "readme.txt", "交付口径", "—", "同上", "否"],
    ], widths_cm=[3.0, 3.4, 3.0, 2.6, 3.6, 2.2])

    _caption(doc, "表 1-2　由输入派生及本队产出的数据")
    _table(doc, [
        ["名称", "来源", "用途", "权利归属", "存放位置", "随包分发"],
        ["已导入页图与 reader 包", "由官方 PDF 经 import-pdf / analyze 派生", "阅读、成片、检索", "赛事授权（派生物）",
         "books/<id>/（pages、art、ocr）", "否；仅 book.json / curation.json 等元数据进仓库"],
        ["成片与时间轴", "本项目 film 链路单趟生成", "交付与演示", "本队作品",
         "runs/<id>/reader_film/<ts>/", "是（进提交包，不进 git）"],
        ["交互演示录屏", "本队浏览器实录，未二次剪辑", "交付", "本队作品", "runs/<id>/interactive/", "是"],
        ["人物切件与动作 beat", "cast 链路由页图派生", "互动阅读", "赛事授权（派生物）", "books/<id>/reader/", "否"],
        ["自建增强数据 / 微调数据", "无", "—", "—", "—", "—（本队未做微调）"],
    ], widths_cm=[2.9, 3.2, 2.6, 2.4, 3.4, 3.3])
    _note(doc, "说明：绘本页图、OCR 产物、成片均在 .gitignore 中（.gitignore:2,10–12,32），不进公开仓库；"
               "提交包内只放交付必需的成片、录屏与文档，页图与 OCR 原文不随源码包分发。")

    # ---------------- 二、模型 ----------------
    _heading(doc, "二、模型与第三方服务清单")
    _caption(doc, "表 2-1　官方三件套（企业主体赛提供，代码已接）")
    _table(doc, [
        ["服务", "型号（代码实配）", "调用范围", "额度 / 条款", "状态"],
        ["DeepSeek（文本、故事、问答增强）", "deepseek-chat（cloud.py:33）", "仅推理，不用于训练；每次调用经预算护栏",
         "总额度 ≤500 元、并发 ≤20（Q42）；条款" + CONFIRM_TEXT, "已接，key 未下发 → 未真实调用"],
        ["豆包 Seedream（文生图）", "doubao-seedream-3-0-t2i-250415（cloud.py:35）", "补图演示，cloud --image",
         "同上；条款" + CONFIRM_TEXT, "已接，key 未下发 → 未真实调用"],
        ["豆包 Seedance（文生视频）", "doubao-seedance-1-0-lite-t2v-250428（cloud.py:36）", "试片，cloud --video",
         "同上；条款" + CONFIRM_TEXT, "已接，key 未下发 → 未真实调用"],
    ], widths_cm=[3.4, 4.0, 3.4, 3.0, 3.0])
    _note(doc, "口径提醒：官方答疑 Q16 写作“DeepSeek-V4.1 Flash、豆包文生图基础版、Seedance 文生视频基础版”，"
               "与代码实配型号不同；以 11.30 官方下发的账号与型号为准，本表先记实配值，不写成“已调通”。"
               "调用流水逐笔写在 runs/cloud/ledger.jsonl。")

    _caption(doc, "表 2-2　本地开源组件（离线运行，不向外发数据）")
    _table(doc, [
        ["组件", "版本 / 来源", "用途", "许可证"],
        ["RapidOCR（PP-OCR, onnxruntime）", "rapidocr_onnxruntime ≥1.3", "本地 OCR 全量 6 本", "Apache-2.0"],
        ["pypinyin", "0.55.0（vendor/ 内置）", "拼音标注", "MIT"],
        ["jieba", "vendor/jieba", "分词与换行", "MIT"],
        ["OpenCV（Telea inpaint）", "opencv-python-headless ≥4.8", "清字修补", "Apache-2.0"],
        ["ffmpeg + libx264", "系统安装", "视频编码与封装", "LGPL / GPL"],
        ["edge-tts", "7.3.1（vendor/）", "旁白合成（晓晓 / Aria）", "GPL-3.0，仅演示与评测；产品化需替换"],
        ["faster-whisper", "≥1.0（ASR_PROVIDER=local）", "评测用本地语音识别", "MIT"],
        ["浏览器 SpeechRecognition", "Chrome 内置", "现场语音输入（音频不出本机服务）", "浏览器条款" + CONFIRM_TEXT],
    ], widths_cm=[3.6, 3.9, 4.6, 3.6])

    _caption(doc, "表 2-3　可选本地模型权重（默认不启用，启用时须补许可证）")
    _table(doc, [
        ["权重", "用途", "来源", "状态"],
        ["facebook/sam-vit-base", "人物轮廓分割（可选）", "Hugging Face", "许可证" + CONFIRM_TEXT + "；未随包"],
        ["depth-anything/Depth-Anything-V2-Small-hf", "单目深度（可选）", "Hugging Face", "许可证" + CONFIRM_TEXT + "；未随包"],
        ["stable-diffusion-v1-5/stable-diffusion-inpainting", "局部修补（可选）", "Hugging Face", "许可证" + CONFIRM_TEXT + "；未随包"],
    ], widths_cm=[5.0, 3.8, 3.0, 4.0])
    _note(doc, "交付版成片若不启用上述权重，则本表整表可标注“未启用”；一旦启用，必须补许可证与调用范围，否则触发“工具或数据来源披露不完整”风险。")

    # ---------------- 三、承诺书 ----------------
    _heading(doc, "三、知识产权与数据使用承诺书")
    _para(doc, "本队（队伍名称：" + TEAM + "；学校：" + SCHOOL + "；院系：计算机" + CONFIRM_TEXT +
               "；成员：" + MEMBERS + "）参加第二届北京市大学生“人工智能+”创新大赛“纸书智伴”赛题，"
               "就数据与知识产权作出如下承诺：", first_indent=True)

    promises = [
        ("素材来源合法", "本队仅使用组委会提供的授权/脱敏纸质绘本样本（6 本）、配套 Query 与交互音频及录制脚本，"
                   "以及本队自行合法取得的其他素材；未使用任何未授权图书、扫描件或其他品类读物作为输入。"),
        ("授权边界清晰", "上述绘本、图片、音频、资料及标注的权利归原权利人或授权提供方所有，组委会提供仅限本赛事开发、评测与现场演示，"
                   "不构成权利转让；本队不在赛事范围外使用、复制、转发、公开、出售或再授权。"),
        ("不用于预训练", "本队未将组委会提供的绘本样本、音频及其派生页图用于任何模型预训练、微调或赛事外训练；"
                   "调用第三方闭源模型服务仅用于本赛事推理；如后续微调，仅使用合法合规、来源可披露的自建或公开数据，"
                   "并保证微调后模型部署于自带设备、可提供权重。"),
        ("第三方组件披露", "本队使用 RapidOCR（Apache-2.0）、pypinyin（MIT）、jieba（MIT）、OpenCV（Apache-2.0）、"
                    "faster-whisper（MIT）、ffmpeg（LGPL/GPL）、edge-tts（GPL-3.0，仅用于演示与评测）等开源组件，"
                    "以及 DeepSeek / 豆包 Seedream / 豆包 Seedance 等闭源服务；全部组件已在第 2 节与《项目说明书》第 4 章列明来源、版本、许可证与调用范围。"
                    "若产品化版本继续使用 GPL-3.0 组件，本队将替换为具备商业授权的同类方案。"),
        ("生成内容边界", "互动视频、问答记录与演示材料仅用于赛事评审、复盘与组委会授权的非商业展示；"
                   "未经组委会与原绘本权利人书面同意，不公开传播完整绘本衍生内容、不上传公开平台用于引流或商业推广、不二次授权第三方使用。"),
        ("数据最小化", "样本页图、OCR 产物、成片与日志仅存于本队设备与私有仓库（已通过 .gitignore 排除 books/*/pages、reader、text 与 runs/ 及官方素材目录），"
                  "不进入公开仓库或提交包之外的任何渠道；交互日志如含语音/图像，脱敏存储且仅用于本队调试与评测。"),
        ("成果权利", "本队对自主完成的代码、方案与原创表达保留署名权及依法享有的知识产权，同时授权组委会与命题企业为赛事评审、宣传、教学案例"
                 "和非商业展示的目的无偿使用。"),
        ("可复现与不冒充", "提交的成片、图像与文本由本项目流程单趟生成，未使用赛前预先固定的结果冒充现场实时生成；"
                    "关键生成内容可按 manifest.json、timeline.json 与 book.json（sha256）逐段定位复现。"),
        ("合规处理与删除", "如涉及成果孵化、商业应用、专利申请或源代码使用，另行协商并签署书面协议；本队理解并接受违规导致的扣分、单项不得分直至取消成绩的处置；"
                    "比赛结束后按组委会通知删除受限数据及本地副本（含 books/*/pages、reader、text、runs/ 内绘本衍生图片与音频），并保留删除记录备查。"
                    "删除执行人：" + BLANK_TEXT + "。"),
    ]
    for i, (title, body) in enumerate(promises, start=1):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.4
        p.paragraph_format.first_line_indent = Pt(21)
        run = p.add_run(f"{i}. {title}：")
        _set_run_font(run, size=10.5, bold=True)
        run = p.add_run(body)
        _set_run_font(run, size=10.5)

    _heading(doc, "数据使用承诺（简版）", level=2)
    _para(doc, "本队承诺：不转发、不公开、不出售赛事数据；不采集真实儿童隐私数据；交互日志中如含语音/图像，脱敏存储且仅用于本队调试与评测；"
               "不将任何赛事数据用于赛事以外的模型训练或商业用途。", first_indent=True)

    # ---------------- 四、内容安全 ----------------
    _heading(doc, "四、内容安全复核机制与责任人")
    _table(doc, [
        ["项", "内容"],
        ["内容安全复核责任人", BLANK_TEXT + "（须为队内固定成员，并写入《项目说明书》封面字段）"],
        ["备份复核人", BLANK_TEXT],
        ["联系方式", BLANK_TEXT],
        ["复核触发点", "① 每本成片导出后　② 每次现场演示前　③ 任何生成文本/语音/图像进入交付包前"],
        ["复核内容", "不适读、低俗、暴力、歧视、恐怖元素；年龄适配（4–8 岁）；史实与常识错误；音画与原文一致性"],
        ["复核方式", "全片 1.0× 过一遍 + 关键页逐页截图核对，填写复核记录表（时间 / 版本 / 结论 / 签字）；不通过则退回重生成或降级，严重项不上交"],
        ["年龄适配标注", "book.json 记 age_range=4-8、ai_generated=true；阅读页与成片页均显示“AI 生成 · 适龄 4–8 岁”"],
        ["依据", "官方 PDF 第 6 页（三级违规处置）、第 8 页（儿童内容规范、人工复核、标注不确定性）；答疑 Q30"],
    ], widths_cm=[3.4, 11.0])
    _note(doc, "未指定内容安全复核责任人属于官方 PDF 第 6 页明确列出的扣分项，是提交前成本最低、收益最高的一项。")

    # ---------------- 五、待确认 ----------------
    _heading(doc, "五、提交前待确认事项（本文件内高亮项汇总）")
    _table(doc, [
        ["#", "待确认项", "为什么必须问 / 怎么办"],
        ["1", "队伍名称、院系全称、队员、指导教师、联系方式、提交日期", "封面与签署页必填；队伍名称须与学校推荐表一致"],
        ["2", "内容安全复核责任人（姓名 + 联系方式）与备份复核人", "官方扣分项；须为队内固定成员"],
        ["3", "三家云端服务的数据条款与额度口径", "先看 11.30 下发的账号条款；额度按 Q42 ≤500 元、并发 ≤20 记录"],
        ["4", "浏览器 SpeechRecognition 的条款与数据流向", "现场语音识别走浏览器内置服务；如需“不出本机”，改用 faster-whisper 本地识别"],
        ["5", "可选模型权重是否启用", "默认不启用；一旦启用须补许可证与调用范围"],
        ["6", "Query 音频实收数量（223 / 要求 300）", "按“分批发放”推进，并在评测章节写明分母；缺 77 条须向组委会索取"],
        ["7", "官方三件套真实调用验证", "key 下发后各跑一次 --llm / --image / --video，留 ledger；未跑前不得写“已调通”"],
    ], widths_cm=[1.0, 6.4, 7.0])
    _note(doc, "第 6、7 项不影响本文件的法律效力，但会写进《项目说明书》的测试结果与局限章节；请在提交前同步更新两处口径。")

    # ---------------- 六、签署 ----------------
    _note(doc, "本文件与《项目说明书》第 4 章（数据与模型来源）、第 9 章（规范合规与伦理）同源；"
               "如两处表述不一致，以本文件签署版为准。")
    doc.add_page_break()
    _heading(doc, "六、签署")
    _para(doc, "本队已阅读并理解上述全部条款，承诺如实提交。", first_indent=True)
    _note(doc, "（本页为签署页：打印后由队长、指导教师、内容安全复核责任人分别手写签名；"
               "亦可签名后扫描为 PDF 替换本页。日期须与提交日期一致。）")
    table = doc.add_table(rows=4, cols=2)
    table.style = "Table Grid"
    sign_rows = [
        ("队长（承诺人）签字", BLANK_TEXT),
        ("指导教师签字", BLANK_TEXT),
        ("内容安全复核责任人签字", BLANK_TEXT),
        ("签署日期", BLANK_TEXT),
    ]
    for r, (key, value) in enumerate(sign_rows):
        _cell_text(table.cell(r, 0), key, size=10, bold=True)
        _shade(table.cell(r, 0), "F4F2EE")
        _cell_text(table.cell(r, 1), value, size=10, highlight=True)
        table.cell(r, 0).width = Cm(5.0)
        table.cell(r, 1).width = Cm(9.4)
        table.rows[r].height = Cm(1.6)
    _para(doc, "（队伍公章，如有；无公章可仅签字）", size=9, first_indent=False)
    _note(doc, "对照检查：封面信息表、第 3 节队伍名称、第 4 节责任人、第 6 节签字四处姓名与日期必须一致；"
               "任何一处留空都会在“规范合规”项扣分。")

    doc.save(out_path)
    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description="生成《素材来源清单及知识产权承诺书》")
    parser.add_argument("--out", default="docs/素材来源清单及知识产权承诺书.docx")
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = build(out)
    print(f"written: {out}  paragraphs={len(doc.paragraphs)} tables={len(doc.tables)}  bytes={out.stat().st_size}")


if __name__ == "__main__":
    main()
