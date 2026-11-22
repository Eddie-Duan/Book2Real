# Skill 4 · 参考样例与本地校验

> 这些回答是**按当前 Skill 4 prompt 在对话里生成的参考输出**，用于检查“文风、长度、语言、事实”约束，不是 MiniMax 的线上实测结果。真实模型验证仍需在 `.env` 配好 MiniMax 后跑一遍。

校验器：`backend/app/skill4/core.py:validate_answer`

- 语音问答：1–2 句；中文 ≤45 字、英文 ≤30 词
- 点击物体：1 句；中文 ≤30 字、英文 ≤20 词
- 不允许“根据资料 / 作为一个 AI / 绘本里说 / 书上说”等套话
- 语言：语音跟提问语言；点击跟绘本语言

## 结果

**15 / 15 通过本地校验。**

## cn01《一起搭积木吧》

| 模式 | 语言 | 问题/点击 | 参考回答 |
|---|---|---|---|
| qa | zh | 为什么积木城堡倒了？ | 小满打了个大喷嚏，高高的积木城堡晃了三晃，一下就倒了。 |
| qa | zh | 花花为什么生气？ | 积木城堡倒了，花花以为是小满的错，就生气地说：“我不要和小满玩！” |
| qa | zh | 小满和花花为什么都不开心？ | 花花一个人玩积木，觉得不好玩。小满在草地上看小虫，也想和花花一起玩。 |
| qa | zh | 花花为什么站在窗前不开心？ | 今天是花花的生日，可是小满不在，她一点儿也不开心。 |
| qa | zh | 黄米团叼着卡片去哪儿了？ | 黄米团叼着小满画好的生日卡片，跑出去找花花了。 |
| qa | zh | 花花和小满最后和好了吗？ | 小满说了对不起，花花笑着说自己早就不生气了，两个人又一起玩了。 |
| qa | en | Why did the castle fall down? | Xiaoman sneezed, and the tall block castle wobbled three times—then down it went! |
| touch | zh | 点击“积木城堡” | 这是花花和小满一块一块搭起来的高积木城堡，屋顶尖尖的。 |
| touch | zh | 点击“黄米团” | 这是小满家的小黄狗黄米团，戴红项圈，正叼着生日卡片跑呢。 |

## en01《Going to the Cinema》

| 模式 | 语言 | 问题/点击 | 参考回答 |
|---|---|---|---|
| qa | en | Who is Kuan? | Kuan is the little boy with spiky red hair. He cries in the cinema, then gives a thumbs-up when he gets popcorn. |
| qa | en | Who is scared? | A little girl with a bow in her hair is scared. She squeezes her hands and shrinks into her seat. |
| qa | en | What is the robot? | It's a big round blue robot with an antenna and round mechanical eyes. Kuan brings it to the cinema. |
| qa | zh | 谁害怕？ | 戴蝴蝶结的小女孩有点害怕，她缩在座位里，紧紧攥着手。 |
| touch | en | Tap the robot | It's a big round blue robot with an antenna and round eyes, sitting beside Dad. |
| touch | en | Tap the popcorn | It's a tub of popcorn from the cinema snack stand, with POPCORN printed on the box. |

## 观察到的问题

1. **“和好啦！”会被句号/感叹号切成一句**，再加上后面的两句就超过 2 句；已把回答压成 1 句完整叙述。
2. **对白里的“我”不能用来判断叙述人称**；文风卡已排除引号内文本，cn01 修正为第三人称。
3. **抽取式兜底原来会加“绘本里说：”**，已改为去掉前缀并按同一长度限制裁剪；无 key 时也不会突然变成“搜索结果腔”。
4. **点击回答默认复用预存卡片**，因此样例回答更接近人工写的元素卡；如果开启 `STYLE_TOUCH_LLM=true`，需要重新测点击首响。

## 下一步建议

- 用 MiniMax 对同样 15 条跑一遍，人工盲评：
  - 文风一致性（1–5）
  - 事实正确性（对/错）
  - 语言是否正确
  - 长度是否合适
- 如果 MiniMax 的回答偏长，优先检查 system prompt 的第 2 条长度约束是否被后续上下文冲淡；必要时把长度规则放到 prompt 末尾再重复一次。
- 如果英文书的中文回答出现“翻译腔”，在 `style_prompt` 的 cross-language bridge 里补一句“用中文儿童口语，不要逐字翻译英文原句”。
