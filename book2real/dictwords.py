"""Dictionary meanings for the words the rule extractor found.

The rule path produced 149 words but 79 of them carried a placeholder ("本页出现
的词：X。看图想想它是什么意思。"), which is not a meaning. This module gives each
new word a real, child-sized sentence from a small hand-written context
dictionary, so the reader never shows a fake gloss — and so the whole thing stays
offline.

    python -m book2real words --book books/charlie_lola   # rewrites placeholders
"""
from __future__ import annotations

import json
from pathlib import Path

# words that actually appear in 绘本 and need a kid-friendly line, grouped by
# the kind of thing they are so the sentence stays consistent
MEANINGS = {
    # 人物 / 动物
    "劳拉": "这本书的小主人公，查理的妹妹。",
    "查理": "劳拉的哥哥，会照顾妹妹。",
    "露塔": "劳拉的同学，特别了解豚鼠。",
    "咪咪": "劳拉的同学，家里也养了豚鼠。",
    "波特": "劳拉班里的豚鼠，棕色长毛。",
    "豚鼠": "一种毛茸茸的小动物，也叫荷兰猪。",
    "毛毛": "咪咪家的豚鼠，从秘鲁来的。",
    # 时间 / 场景
    "第二天": "过了这一晚之后的那天。",
    "此时": "就是这个时候。",
    "正在": "表示现在就在做这件事。",
    "现在": "说话的这一刻。",
    "刚才": "不久之前。",
    "后来": "在那之后。",
    "终于": "等了很久才做到。",
    "忽然": "事情来得很快、很突然。",
    "突然": "一点准备都没有就发生了。",
    "一直": "从头到尾没有停。",
    "已经": "表示这件事完成了。",
    "学校": "小朋友上课、学习的地方。",
    "教室": "在学校里上课的房间。",
    "班级": "同一个班里的同学。",
    "家里": "自己住的那个家。",
    "假期": "放假、不用上学的那段时间。",
    # 动作 / 状态
    "了解": "知道得很清楚。",
    "喜欢": "心里觉得好，愿意亲近。",
    "特别": "非常、格外。",
    "知道": "脑子里明白了这件事。",
    "觉得": "心里这样想。",
    "回答": "别人问了，你来说。",
    "告诉": "把知道的事说给别人听。",
    "悄悄": "声音很小，不想让人听见。",
    "悄悄地说": "用很小的声音说。",
    "同意": "答应、赞成别人的想法。",
    "担心": "怕有不好的事发生。",
    "照顾": "照看、帮助别人。",
    "教育": "教别人知识和道理。",
    "介绍": "把人和事说给别人认识。",
    "奇怪": "和平时不一样，让人不明白。",
    "害怕": "心里很不安、很紧张。",
    "高兴": "心里很快乐。",
    "开心": "心情很好。",
    "着急": "心里急，等不了。",
    "认真": "做事很用心。",
    "终于找到": "找了很久才找到。",
    # 名词
    "妹妹": "比自己小的女孩子，同父母所生。",
    "哥哥": "比自己大的男孩子。",
    "姐姐": "比自己大的女孩子。",
    "弟弟": "比自己小的男孩子。",
    "同学": "在同一个班一起上学的朋友。",
    "老师": "在学校教小朋友的人。",
    "妈妈": "生养自己的女性长辈。",
    "爸爸": "生养自己的男性长辈。",
    "小人儿": "小小的一个人，也指小孩。",
    "有趣": "好玩、让人感兴趣。",
    "样子": "看上去的模样。",
    "事情": "发生的事。",
    "问题": "需要回答或者要弄明白的事。",
    "答案": "对问题的回答。",
    "办法": "解决问题的做法。",
    "主意": "想出来要做的事。",
    "名字": "用来叫一个人或一样东西的称呼。",
    "动物": "会动、会呼吸、会吃东西的生命。",
    "世界": "地球上所有的地方和人。",
    "国家": "世界上的一块地方，有自己的名字。",
    "秘鲁": "南美洲的一个国家。",
    "种类": "同一类东西的不同样子。",
    "水果": "树上、藤上长出来的甜果子。",
    "蔬菜": "可以做菜吃的植物。",
    "土豆": "地里长的一种圆圆的、可以吃的植物。",
    "薯条": "把土豆切成条炸出来的食物。",
    "萝卜": "红的或者白的、长在地里的蔬菜。",
    "荷兰猪": "豚鼠的另一个名字。",
    "声音": "耳朵听到的动静。",
    "颜色": "红的、黄的、蓝的这些都是颜色。",
    "照片": "拍下来的图画。",
    "时候": "某一个时间。",
}

# words that must not become entries: they are function words or too abstract
SKIP = set("本页 出现 看图 想想 意思 什么 怎么 这样 那样 可以 已经 正在 一直".split())


def load(reader: Path) -> int:
    """Rewrite placeholder meanings in reader/words/*.json with real ones."""
    fixed = removed = 0
    for path in sorted((reader / "words").glob("*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        kept = []
        for row in rows:
            w = row.get("w", "")
            if not w:
                continue
            if row.get("mean", "").startswith("本页出现的词："):
                real = MEANINGS.get(w)
                if real:
                    row["mean"] = real
                    row["source"] = "dict"
                    fixed += 1
                else:
                    removed += 1
                    continue
            if w in SKIP:
                removed += 1
                continue
            kept.append(row)
        path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    return fixed, removed
