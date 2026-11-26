# 检索式构造（阶段 2：把自然语言转成标准检索式）

## 三段式结构

把一个研究问题拆成三段，每段给一组同义词：

```
[研究对象] AND [方法/技术] AND [场景/任务]
```

例："用图神经网络做药物分子性质预测"

| 段 | 主词 | 同义词扩展 |
|---|---|---|
| 对象 | drug molecule | molecular graph, compound, chemical structure, ligand |
| 方法 | graph neural network | GNN, message passing, graph convolution, graph representation learning |
| 场景 | property prediction | ADMET prediction, QSAR, toxicity prediction, solubility prediction |

三段乘起来才是完整检索式。只给一段（比如只搜 "drug discovery"）会命中几十万篇，排序失去意义。

## 检索式文件（推荐）

不要直接把长检索式塞进命令行，Windows 下引号会被拆开。写 `work/query.json`：

```json
{
  "core": ["OTFS", "modulation", "wireless communication"],
  "focus": ["OTFS", "orthogonal time frequency space"],
  "broad": ["OTFS", "orthogonal time frequency space", "delay-Doppler", "delay Doppler"],
  "categories": ["eess.SP", "cs.IT"],
  "years": "2018-2026",
  "recent_days": 183,
  "year_priority": true,
  "per_source": 50,
  "sources": "openalex,arxiv"
}
```

字段含义：

| 字段 | 作用 | 注意 |
|---|---|---|
| `core` | 主检索式，多个词之间 AND | 这是"必须真的在做这个主题"的组合 |
| `focus` | 主题核心词的同义词簇，词之间 OR | **去噪的关键**。只命中 focus 才算主题论文；只命中 broad 算相邻领域 |
| `broad` | 放宽检索式，通常只保留对象段 | 精确检索命中过少时兜底 |
| `categories` | arXiv 分类码，词之间 OR | 目前脚本直接支持 arXiv `cat:`；其他学科分类码写进关键词 |
| `years` | 年份区间 | 新领域不要硬拉到 20 年前 |
| `recent_days` | "最新"窗口 | 默认 183 天；要看上个月就调到 60–90 |
| `year_priority` | 当年论文优先 | 默认 `true`；会单独拉当年整年，不看被引数 |
| `per_source` | 每源每轮条数 | 40–60 足够；再大只会增加噪声 |
| `sources` | 数据源 | 本技能只允许 `openalex,arxiv`，不要加别的源 |

## 三个检索变体

| 变体 | 构造方式 | 用途 |
|---|---|---|
| `broad` | 只用对象段 + 场景段 | 看领域规模，发现没想到的子方向 |
| `core` | 三段全给 | 主检索，结果进分层 |
| `review` | `core` + `review OR survey OR systematic review OR tutorial` | 建骨架，优先级最高 |

`fetch_refs.py` 会自动跑 core、broad、当年优先、同义词和 review 检索。综述检索命中为 0 时，**不要硬着头皮往下走**——去掉方法段，只搜 `"<对象> survey"` 重跑。

## 综述/教程识别

脚本不只匹配标题里的 `review`，而是三路信号合并：

1. OpenAlex `type:review`
2. 标题模式：`survey / review / overview / tutorial / state of the art / advances in / progress in / comprehensive review / roadmap / taxonomy / open problems`
3. 摘要模式：`this paper surveys`、`we review`、`we provide an overview`、`literature review`、`systematic review`、`meta-analysis`

同时排除反例：`peer review`、`reviewer`、`book review`、`code review`。标题带 `tutorial` 的优先当骨架，因为教程最适合入门。

## 去噪与主题中心度

同一个关键词会带出三类东西，必须分开：

| scope | 判定 | 处理 |
|---|---|---|
| `core` | 标题命中 focus，或摘要命中 focus ≥2 次 | 进分层与必读清单 |
| `mention` | 摘要只命中 focus 1 次 | 进附录"仅提及"，不进必读 |
| `related` | 只命中 broad 相邻词 | 进附录"相邻领域"，不进必读 |
| `off_topic` | 连相邻词都没有 | 检索阶段直接丢弃 |

以 OTFS 为例：标题写 `OTFS channel estimation` 的是真论文；6G 综述摘要里顺带一句 "including OTFS" 的是 `mention`；只写 `delay-Doppler processing` 的是 `related`。三者不能混在一张必读清单里。

## 同义词怎么扩

优先从这三处取，比自己想靠谱：

1. **目标领域 2–3 篇高被引综述的关键词列表**（作者列出的 keywords 就是同行在用的词）
2. **领域标准分类码的名称**（见下节）
3. **论文标题里的名词短语**（看同行怎么称呼同一个东西）

常见坑：

- **缩写与全称都要给**：`OTFS` 和 `orthogonal time frequency space` 命中集合差异很大，都放进 `focus`
- **英美拼写**：`modeling` / `modelling`，`behaviour` / `behavior`
- **新旧命名**：领域改过名的要都保留（如 `knowledge distillation` 早期叫 `model compression`）
- **过度扩同义会稀释相关性**：同义词组超过 6 个，噪声会盖过信号。宁可少而准

## 学科分类码

分类码是提升精度的最有效手段，因为它直接锁定学术共同体，绕开跨领域的同名词污染。

| 学科 | 分类体系 | 用法 |
|---|---|---|
| 计算机 / 通信 | arXiv category（eess.SP / cs.IT / cs.LG / cs.CL / cs.CV / cs.AI）、ACM CCS | 脚本直接支持 arXiv `categories`；OpenAlex 用 `primary_topic.field` 过滤（当前未开放为 spec 字段，可写进关键词） |
| 医学 | MeSH terms、PubMed 的 `[MeSH Terms]` | 检索时加 `"Drug Discovery"[MeSH]` 可大幅提精度；本技能不直连 PubMed 时，把 MeSH 词当普通关键词写进 `core` |
| 材料 / 化学 | CAS、CoRE MOF 等专业本体；arXiv cond-mat.* | 材料领域更多依赖关键词而非分类码 |
| 经管社科 | JEL codes（如 JEL: G12） | RePEc / SSRN 支持；OpenAlex 不直接解析 JEL，写成关键词 |
| 生物 | GO terms、NCBI Taxonomy | 用于基因/蛋白相关检索 |

V2 的 `fetch_refs.py` 只直接支持 arXiv 的 `categories` 过滤；MeSH / ACM CCS / JEL 要么写进 `core` 作为附加关键词，要么在报告里说明"本轮未用分类码，精度有限"。

## 2026 年新论文优先

三层机制，缺一不可：

1. **检索层**：`year_priority=true` 单独拉当年整年论文，按 `publication_date:desc`，不看被引数
2. **排序层**：`frontier` 层排序时当年论文优先；`score` 对近 12 个月论文额外加权
3. **交付层**：`latest` 通道按日期倒序，当年论文排最前，并在报告中标注"★当年"

只做第 3 层不够——如果检索阶段根本没拿到 2026 年论文，后面无法凭空变出来。报告头部必须写"当年论文 N 篇"，N=0 时要说明原因。

## 检索式调试

拿到命中数后按这个表处理：

| 现象 | 判断 | 处理 |
|---|---|---|
| 命中 < 50 | 关键词太窄或领域太新 | 去掉方法段，只留对象+场景；检查拼写；换上位词 |
| 命中 50–5000 | 健康区间 | 直接进分层 |
| 命中 > 50000 | 太泛 | 加分类码；加方法段；加年份下限 |
| 命中很多但前 50 篇明显不相关 | 关键词歧义 | 加引号强制短语；换更具体的下位词；检查 `focus` 是否太宽 |
| 综述命中为 0 | 领域太新或关键词错 | 去掉方法段重跑；直接搜 `"<对象> survey"` |
| 主题核心 < 8 篇 | 去噪后样本太小 | 放宽 `focus` 或扩大年份；报告中如实说明分层仅供参考 |

## 给用户看的检索式

报告里要把最终检索式写出来。格式：

```
core:  ("OTFS" OR "orthogonal time frequency space") AND modulation AND "wireless communication"
broad: ("OTFS" OR "orthogonal time frequency space" OR "delay-Doppler")
review: core AND (review OR survey OR "systematic review" OR tutorial)
分类码：arXiv eess.SP, cs.IT
年份：2018–2026；当年优先：是
数据源：OpenAlex, arXiv
```