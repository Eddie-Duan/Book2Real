# Skill 4 · 绘本语气回答

> 目标：孩子**语音提问**或**点击画面物体**时，回答文字保持这本绘本自己的叙述语气，而不是通用 AI 助手的百科腔。

## 1. 语言策略

| 入口 | 回答语言 | 风格来源 |
| --- | --- | --- |
| 语音提问 | 跟提问语言（中文问中文答，英文问英文答） | 当前绘本 |
| 点击物体 | 绘本语言 | 当前绘本 |
| 跨语言 | 跟提问语言，人名保留原文 | 句长/语气/具体程度照绘本迁移 |

## 2. 实现位置

- `backend/app/skill4/core.py`
  - `build_style_card(book)`：从正文统计人称、平均句长、对话比例、拟声词、连接词，并挑 2–3 句原文样例。
  - `style_prompt(card, answer_lang, mode)`：把风格卡渲染成 system prompt 片段。
  - `style_touch_text(text, lang)`：点击卡片答案的零延迟清理（限长、去 AI 套话）。
  - `validate_answer(...)`：离线/探针用，检查长度、语言、AI 套话。
- `backend/app/pipeline/turn.py:build_messages`
  - 语音问答与点击共用；把风格卡拼进 system prompt。
  - `intent=intent.get("intent")` 为 `touch` 时使用“点击物体”规则。
- `backend/app/pipeline/turn.py:answer_task`
  - 点击默认复用预存元素卡 + `style_touch_text`，不增加模型延迟。
  - 设置 `STYLE_TOUCH_LLM=true` 后，点击也走同一套文风 prompt，失败回退预存卡。

## 3. Prompt 骨架

```text
你是一位温柔、有耐心的儿童绘本伴读老师，正在用这本书自己的声音和孩子说话。
当前绘本：《...》，书页语言：zh。

【这本书的叙述声音】
- 叙述人称：第三人称
- 语气：温暖、具体；多用动作和感官细节，少用抽象词。
- 平均句长：约 13.5 字/句。
- 常用连接词：后来、突然；拟声词：哗啦、阿嚏。
- 可模仿的原文句子（只学语气，不要照抄整句）：
  1. ...
- 这次是语音提问 / 点击物体：...
- 回答最多 2 句 / 点击最多 1 句；中文不超过 45 字 / 30 字，英文不超过 30 词 / 20 词。
- 回答语言与本书相同 / 不相同：...

规则：
1. 用孩子提问的语言回答 / 点击回答使用绘本语言。
2. 只答 1-2 句；点击回答最多 1 句。
3. 直接回答，不要重复问题，不要开场白，不要 markdown、列表、emoji。
4. 只依据绘本原文和本页画面；不新增人物、地点、数字、无关内容。
5. 不要使用“根据资料／作为一个 AI／总之／希望对你有帮助”这类套话。

绘本原文：
...
```

## 4. 风格卡字段

```json
{
  "title": "一起搭积木吧",
  "lang": "zh",
  "pov": "第三人称",
  "tone": "温暖、具体",
  "avg_sentence_len": 13.5,
  "unit": "字",
  "sentence_count": 42,
  "dialogue_ratio": 0.1,
  "onomatopoeia": ["哗啦", "阿嚏"],
  "connectors": ["后来", "突然"],
  "samples": ["小满打了个大喷嚏，积木城堡哗啦一下倒了。"]
}
```

## 5. 开关

```ini
# 默认 false：点击复用预存卡片，零模型延迟
STYLE_TOUCH_LLM=false
```

如果团队要求“每次点击也由模型重新生成文风回答”，改成 `true`，但要实测点击首响是否仍满足要求；失败会回退到预存卡片。

## 6. 测试

```powershell
cd backend
python -m pytest tests/test_style.py -q
# 5 passed
python -m pytest -q
# 23 passed
```

## 7. 真实 API 验证（尚未执行）

需要把 MiniMax 的 LLM 配置放进**本机** `.env`，不要把 key 发到聊天或提交：

```ini
LLM_BASE_URL=https://api.minimaxi.com/v1
LLM_API_KEY=<本机填写>
LLM_MODEL=MiniMax-M2.7-highspeed
```

然后用 5–10 条官方 Query 文本做小样本对比：

- 正确性：回答是否命中原文
- 文风：是否短、具体、像这本书
- 语言：英文问题是否英文回答
- 延迟：首 token / 首句是否仍在 2s 预算内

## 8. 已知边界

- 文风卡是规则统计，不是“风格模型”；它能约束句长、语气词和具体程度，但不能保证每一句都像原作者。
- `style_touch_text` 只做清理，不做生成；真正的点击文风生成需要 `STYLE_TOUCH_LLM=true` 或离线预生成元素卡。
- 当前没有 `data/books`，真实书上的效果要等 backend 入库后再验证。
