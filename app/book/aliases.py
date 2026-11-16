"""中英绘本关键词别名：让中文提问能检索英文书，英文提问能检索中文书。

只做“检索扩展”，不做整句翻译；最终答案仍由 LLM 生成。
"""
from __future__ import annotations

import re


def _has_en(text: str, phrase: str) -> bool:
    """英文按词边界匹配，避免 scared 里命中 car。"""
    return re.search(r"(?<![a-z])" + re.escape(phrase.lower()) + r"(?![a-z])", text) is not None

ZH_EN: dict[str, list[str]] = {
    "小满": ["xiaoman", "xiao man"],
    "花花": ["huahua", "hua hua"],
    "黄米团": ["huangmituan", "huang mi tuan", "dog", "puppy"],
    "积木城堡": ["castle", "block castle", "blocks"],
    "城堡": ["castle"],
    "积木": ["blocks", "block"],
    "生日卡片": ["birthday card", "card"],
    "生日": ["birthday"],
    "番茄": ["tomato", "tomatoes"],
    "黄瓜": ["cucumber", "cucumbers"],
    "辣椒": ["pepper", "peppers", "chili"],
    "土豆": ["potato", "potatoes"],
    "南瓜": ["pumpkin", "pumpkins"],
    "豆角": ["bean", "beans"],
    "刺猬": ["hedgehog"],
    "茄子": ["eggplant", "aubergine"],
    "兔子": ["rabbit", "bunny"],
    "大象": ["elephant"],
    "河马": ["hippo", "hippopotamus"],
    "大熊": ["bear"],
    "企鹅": ["penguin"],
    "猴子": ["monkey"],
    "天鹅": ["swan"],
    "蝙蝠": ["bat", "bats"],
    "熊猫": ["panda", "pandas"],
    "骆驼": ["camel", "camels"],
    "章鱼": ["octopus"],
    "青蛙": ["frog", "frogs"],
    "蝴蝶": ["butterfly", "butterflies"],
    "恐龙": ["dinosaur", "dinosaurs"],
    "红丝带": ["ribbon", "red ribbon"],
    "鸭子": ["duck"],
    "油污": ["oil"],
    "旧书": ["old book", "book", "recycle"],
    "地球": ["earth", "globe"],
    "小鸟": ["bird", "birds"],
    "河流": ["river"],
    "垃圾桶": ["garbage", "trash", "bin"],
    "电影海报": ["film poster", "poster", "cinema"],
    "机器人": ["robot"],
    "游戏机": ["arcade", "game machine", "game"],
    "爆米花": ["popcorn"],
    "汽车": ["car", "cars"],
    "歌舞片": ["musical", "musicals", "dancing", "singing"],
    "害怕": ["scared", "afraid", "frightened", "scary"],
    "零食区": ["snack", "snacks", "food", "popcorn"],
    "街机": ["arcade", "game machine"],
    "拥抱": ["hug"],
    "吵架": ["angry", "fight", "quarrel"],
    "妈妈": ["mom", "mother", "mum"],
    "爸爸": ["dad", "father"],
}

ZH_QUESTION = {
    "where": "哪里",
    "who": "谁",
    "why": "为什么",
    "how many": "多少 几个",
    "what color": "什么颜色",
    "color": "颜色",
    "big": "大",
    "small": "小",
    "angry": "生气",
    "sad": "难过",
    "happy": "开心",
    "scared": "害怕",
    "jump": "跳",
    "run": "跑",
    "eat": "吃",
    "live": "住",
    "fly": "飞",
}

EN_SYNONYMS = {
    "scared": ["afraid", "frightened", "scary"],
    "angry": ["mad", "cross"],
    "happy": ["glad", "cheerful"],
    "sad": ["unhappy", "cry"],
    "movie": ["film", "cinema"],
    "film": ["movie", "cinema"],
    "snack": ["food", "popcorn"],
    "dad": ["father"],
    "mom": ["mother", "mum"],
    "garbage": ["trash", "rubbish"],
    "trash": ["garbage", "rubbish"],
    "sick": ["ill"],
    "big": ["large"],
    "small": ["little", "tiny"],
    "car": ["cars", "vehicle"],
    "dog": ["puppy"],
}

EN_PHRASES = {
    "what happened": ["发生了什么", "一倒", "倒塌"],
    "why are they angry": ["为什么", "生气", "都怪你"],
    "what is the dog carrying": ["小狗", "叼着", "黄米团"],
    "which film": ["哪部电影", "电影海报", "kung fu"],
    "which movie": ["哪部电影", "电影海报", "kung fu"],
    "what food": ["什么食物", "零食"],
    "who is scared": ["谁", "害怕", "afraid"],
    "what color": ["什么颜色"],
    "how many": ["多少", "几个"],
    "where is": ["在哪里"],
    "who is": ["谁"],
    "what is": ["是什么"],
}


ZH_SYNONYMS: dict[str, list[str]] = {
    "独处": ["一个人", "独自", "自己"],
    "重逢": ["见面", "见了面", "和好", "对不起"],
    "吵架": ["生气", "都怪你", "不要和小满玩"],
    "拥抱": ["抱住", "抱一抱", "开心", "和好"],
    "make up": ["和好"],
    "结尾": ["最后", "结束"],
    "开头": ["一开始", "封面"],
    "倒下": ["一倒", "倒了", "倒塌"],
    "晃动": ["晃三晃", "动了动"],
    "生日": ["过生日", "生日快乐"],
}


def expand_query(query: str, book_lang: str = "zh") -> str:
    query = query or ""
    low = query.lower()
    extra: list[str] = []
    for zh, ens in ZH_EN.items():
        if any(_has_en(low, en) for en in ens) and zh not in query:
            extra.append(zh)
        if book_lang == "en" and zh in query:
            extra.extend(en for en in ens if not _has_en(low, en))
    for zh, syns in ZH_SYNONYMS.items():
        if zh in query:
            extra.extend(s for s in syns if s not in query)
    for en, syns in EN_SYNONYMS.items():
        if _has_en(low, en):
            extra.extend(s for s in syns if not _has_en(low, s))
    for phrase, zhs in EN_PHRASES.items():
        if _has_en(low, phrase):
            extra.extend(zhs)
    if book_lang == "zh":
        for en, zh in ZH_QUESTION.items():
            if _has_en(low, en):
                extra.append(zh)
    if not extra:
        return query
    seen: set[str] = set()
    deduped = []
    for item in extra:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return query + " " + " ".join(deduped)
