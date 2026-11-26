# 检索式构造

## 三段式结构

把一个研究问题拆成三段，每段给一组同义词：

```
[研究对象] AND [方法/技术] AND [场景/任务]
```

例："用图神经网络做药物分子性质预测"

| 段 | 主词 | 同义词扩展 |
|---|---|---|
| 对象 | drug molecule | molecular graph, compound, chemical structure, ligand |
| 方法 | graph neural network | GNN, message passing, graph convolution |
| 场景 | property prediction | ADMET, QSAR, toxicity prediction, solubility |

三段乘起来才是完整检索式。只搜 `drug discovery` 会命中几十万篇，排序失去意义。

## 检索式文件（推荐）

不要直接把长检索式塞进命令行，Windows 下引号会被拆开。写 `work/query.json`：

```json
{
  "profile": "general",
  "core": ["OTFS", "modulation", "wireless communication"],
  "focus": ["OTFS", "orthogonal time frequency space"],
  "broad": ["OTFS", "delay-Doppler", "delay Doppler"],
  "context": [],
  "categories": ["eess.SP", "cs.IT"],
  "mesh": [],
  "years": "2018-2026",
  "recent_days": 183,
  "year_priority": true,
  "per_source": 50,
  "sources": "openalex,arxiv"
}
```

| 字段 | 作用 | 注意 |
|---|---|---|
| `profile` | 学科模板 | general / cs / medicine；也可用 `--profile` |
| `core` | 主检索式，词间 AND | 必须真的在做这个主题 |
| `focus` | 主题核心词同义词簇，词间 OR | **去噪关键**。只命中 focus 才算主题论文 |
| `broad` | 放宽检索式 | 精确检索过少时兜底 |
| `context` | 领域上下文词 | 排除同名词污染（如 OTFS 在药学里的同名缩写） |
| `categories` | arXiv 分类码 | 仅对 arXiv 生效；医学留空 |
| `mesh` | MeSH 主题词 | 仅对 PubMed 生效；建议 2–3 个 |
| `years` | 年份区间 | 新领域不要硬拉到 20 年前 |
| `recent_days` | "最新"窗口 | 默认 183；医学建议 365 |
| `year_priority` | 当年论文优先 | 默认 true，**不要关** |
| `per_source` | 每源每轮条数 | 40–60 足够 |
| `sources` | 数据源 | general/cs：openalex,arxiv；medicine：pubmed,openalex |

## 三个检索变体

| 变体 | 构造 | 用途 |
|---|---|---|
| `broad` | 只用对象段 + 场景段 | 看领域规模，发现没想到的子方向 |
| `core` | 三段全给 | 主检索，结果进分层 |
| `review` | `core` + `review OR survey OR systematic review OR tutorial` | 建骨架，优先级最高 |

`fetch_refs.py` 自动跑 core、broad、当年优先、同义词和 review。综述命中为 0 时不要硬走：去掉方法段，只搜 `"<对象> survey"` / `"<对象> systematic review"` 重跑。

## 学科检索语法

### arXiv（CS / 物理）

```
all:"graph neural network" AND all:"drug discovery"
cat:cs.LG OR cat:cs.CV
```

分类码是提精度最有效的手段，直接锁定学术共同体。注意跨分类工作可能被漏检，报告里要说明。

### PubMed（医学）

脚本自动拼：

```
("diabetic retinopathy"[Title/Abstract] OR retinopathy[Title/Abstract])
AND (deep learning[Title/Abstract] AND diagnosis[Title/Abstract])
AND ("Diabetic Retinopathy"[MeSH Terms] OR "Deep Learning"[MeSH Terms])
AND ("2021"[PDAT] : "2026"[PDAT])
```

MeSH 词到 PubMed 页面右侧 "MeSH terms" 里复制。常见坑：

- MeSH 词是受控词，不要自己造（`AI` 不是 MeSH，`Artificial Intelligence` 才是）
- 疾病名与干预名分开写，不要合成一个短语
- 年份用 `[PDAT]`，不是 `[DP]`（后者是出版日期，行为不同）

### OpenAlex（通用）

```
filter=from_publication_date:2018-01-01,to_publication_date:2026-12-31,type:article
search="graph neural network"
sort=cited_by_count:desc
```

## 同义词怎么扩

优先从三处取：

1. 目标领域 2–3 篇高被引综述的关键词列表（作者用的词就是同行在用的词）
2. 领域标准分类码的名称（arXiv category、MeSH、JEL、CCS）
3. 论文标题里的名词短语

常见坑：

- 缩写与全称都要给（OTFS / orthogonal time frequency space 命中集合差异很大）
- 英美拼写（modeling / modelling）
- 新旧命名（knowledge distillation 早期叫 model compression）
- 同义词超过 6 组会稀释相关性，宁可少而准

## 去噪与主题中心度

| scope | 判定 | 处理 |
|---|---|---|
| `core` | 标题命中 focus，或摘要命中 focus ≥2 次且开头在讲主题 | 进分层与必读 |
| `mention` | 摘要只命中 focus 1 次 | 附录 A"仅提及" |
| `related` | 只命中 broad 相邻词 | 附录 A"相邻领域" |
| `off_topic` | 都没命中 | 丢弃 |

例：标题写 `OTFS channel estimation` 是真论文；6G 综述摘要里顺带一句 "including OTFS" 是 `mention`；只写 `delay-Doppler processing` 是 `related`。三者不能混在一张必读清单里。

## 2026 年新论文优先

三层机制，缺一不可：

1. **检索层**：`year_priority=true` 单独拉当年整年，按日期倒序，不看被引
2. **排序层**：`frontier` 层当年优先；`score` 对近 12 个月加权
3. **交付层**：`latest` 通道按日期倒序，当年标"★当年"

只做第 3 层不够——检索阶段没拿到，后面变不出来。报告头部必须写"当年论文 N 篇"，N=0 要说明原因。

## 检索式调试

| 现象 | 判断 | 处理 |
|---|---|---|
| 命中 < 50 | 关键词太窄或领域太新 | 去掉方法段；检查拼写；换上位词 |
| 命中 50–5000 | 健康区间 | 直接进分层 |
| 命中 > 50000 | 太泛 | 加分类码/MeSH；加方法段；加年份下限 |
| 前 50 篇明显不相关 | 关键词歧义 | 加引号强制短语；换下位词；检查 focus 是否太宽 |
| 综述命中为 0 | 领域太新或词错 | 去掉方法段重跑；搜 `<对象> survey` |
| 主题核心 < 8 篇 | 去噪后样本太小 | 放宽 focus 或年份；报告如实说明 |

## 给用户看的检索式

报告里要写出最终检索式，格式：

```
core:  ("OTFS" OR "orthogonal time frequency space") AND modulation AND "wireless communication"
broad: ("OTFS" OR "delay-Doppler")
review: core AND (review OR survey OR tutorial)
分类码：arXiv eess.SP, cs.IT
MeSH：Diabetic Retinopathy, Deep Learning（医学模板）
年份：2018–2026；当年优先：是
数据源：OpenAlex, arXiv
```