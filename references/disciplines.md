# 学科差异

同一个 skill 不能对所有学科用一套标准。下面这张表决定"什么算必读"和"什么算综述"。

## 差异矩阵

| 维度 | 计算机 / AI | 医学 / 生医 | 材料 / 化学 / 物理 | 经管 / 社科 |
|---|---|---|---|---|
| 主库 | arXiv、DBLP、ACM DL、OpenAlex | PubMed、Embase、Cochrane、OpenAlex | Web of Science、Scopus、arXiv cond-mat | SSRN、RePEc、Web of Science |
| 成果形式 | **顶会为主**，期刊次之 | **期刊为主**，会议次之 | 期刊为主 | 期刊 + 工作论文 |
| "必读"标准 | 顶会（NeurIPS/ICML/ICLR/CVPR/ACL…）论文，或高被引奠基作 | RCT、Meta 分析、指南、大样本队列；影响因子重要 | 高被引原创论文 + 权威综述（Chem Rev / Adv Mater 类） | 高被引 + 顶刊（AER/QJE 类）+ 综述 |
| 综述形态 | survey / review，常在 arXiv，更新快 | systematic review / meta-analysis，PRISMA 规范 | review article，篇幅长、图多 | literature review / meta-analysis |
| 时效性 | 极强，2 年前的 SOTA 可能已过时 | 中等，经典结论可长期有效 | 中等偏强 | 弱，经典文献可几十年有效 |
| 常见陷阱 | 只搜期刊漏掉顶会；被 arXiv 预印本误导（未经评审） | 把预印本当定论；忽略样本量与试验设计 | 把会议摘要当完整论文 | 混淆相关性与因果；忽略识别策略 |
| 术语特点 | 缩写极多，同词异义严重 | 拉丁词根、疾病名与药名 | 材料牌号、晶系、表征手段 | 计量经济学术语、制度名 |

## 分学科操作要点

### 计算机 / AI

- **顶会优先**。一篇 NeurIPS 论文的引用数可能低于某期刊文章，但在圈内的地位更高。分层时如果 `venue` 命中顶会列表，给一个显式加成。
- arXiv 必须跑，否则会漏掉最近半年最重要的进展。
- 一定要区分"预印本"和"已发表"。报告里标出会议/期刊名，用户才知道该不该信。
- 综述通常叫 survey。搜 `survey` 比搜 `review` 命中更准。

### 医学 / 生医

- 检索质量高度依赖 MeSH 词。让用户提供 2–3 个 MeSH 词，或者用 PubMed 的 `[MeSH Terms]` 语法先探一次。
- "必读"里必须有指南或共识文件（如 WHO / NCCN / 中华医学会指南），而不是全是原始研究。
- 注意研究类型标注：RCT / 队列 / 病例对照 / 基础研究，类型不同证据等级不同。报告里要标出来。
- 预印本（medRxiv）单独标出，不要和已发表文献混排。

### 材料 / 化学 / 物理

- 综述价值极高，一篇 Chem Soc Rev 的综述能顶二十篇原始论文。综述层的配额可以放宽到 10 篇。
- 关注表征手段与合成路线这类"方法性"内容，用户往往更需要的是"怎么做出来"而不是"做出来什么"。
- arXiv 在物理覆盖好，在材料化学覆盖一般。

### 经管 / 社科

- 经典文献时效性弱，数十年前的奠基作仍然必读。`foundational` 层的年份阈值要放宽。
- 一定要区分"相关性"与"因果识别"。报告里如果引用实证结论，要标出识别策略（DID / IV / RDD / RCT）。
- 工作论文（working paper）在中国知网体系外，但对选题很重要，不能只搜期刊。

## 跨领域选题

用户如果是从 A 领域转到 B 领域，最有价值的选题通常是"A 的方法解决 B 的问题"。这时候要**两边都检索**：

- 在 A 领域搜该方法的最新进展（方法侧现状）
- 在 B 领域搜该问题的主流解法（问题侧现状）
- 两者的交集就是迁移机会，交集为空就是潜在空白

这类检索要做两轮，`--query` 给两次，分别存 `refs_a.jsonl` 和 `refs_b.jsonl`，分层时合并。报告里要明确写出"本报告基于两个领域的交叉检索"，否则用户会以为覆盖不全。

## 判定"这是不是综述 / 教程"

标题含以下词的，先标记为综述候选（仍需摘要确认）：

```
review, survey, overview, tutorial, state of the art, state-of-the-art,
advances in, progress in, recent developments, a comprehensive,
systematic review, meta-analysis, primer, perspective, roadmap,
taxonomy, open problems
```

摘要里出现这些句式的，即使标题没有上述词，也应标为综述：

- `this paper surveys ...` / `this article reviews ...`
- `we review ...` / `we survey ...`
- `we provide an overview of ...`
- `literature review` / `systematic review` / `meta-analysis`

反例——这些标题含 "review" 但不是综述：

- `A review of reviewer comments...`（研究审稿意见的实证论文）
- `Peer review in biomedical publishing`（研究同行评审制度）
- `Code review automation...`（研究代码审查工具）

所以标记只是候选，最终要打开摘要看第一段是不是在梳理已有工作。只有标题信号、摘要完全没有综述句式时，报告中要标注"按标题判定，摘要未确认"。

## 去噪：区分主题论文与关联论文

不要把"关键词出现过"当成"这篇论文研究这个主题"。分四类：

| scope | 例子（OTFS 领域） | 处理 |
|---|---|---|
| core | 标题 `OTFS Channel Estimation...` | 进必读 |
| mention | 6G 综述摘要里写了一句 "including OTFS" | 附录，不进必读 |
| related | 只写 `delay-Doppler processing`，没提 OTFS | 附录，不进必读 |
| off_topic | 完全无关 | 丢弃 |

报告必须写清去噪前后的数量和"仅提及/相邻领域"的条数，否则用户会以为清单覆盖了全部相关文献。