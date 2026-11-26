# 数据源与接口细节

## 总体策略

| 源 | 角色 | 覆盖 | 缺点 |
|---|---|---|---|
| **OpenAlex** | 主力 | 全学科、2.5 亿+ 作品、自带被引数与引文关系 | 最新预印本收录有延迟；免费额度按 IP 共享 |
| **arXiv** | 前沿补充 | 物理、数学、CS、生医预印本 | 只覆盖预印本，无被引数，无同行评审信息 |
| **Crossref** | 不使用 | 本项目限定只用 OpenAlex + arXiv | 不接入，避免越界 |

两者组合的逻辑：OpenAlex 给广度和权威度，arXiv 补最近 6–12 个月的新东西。不要新增其他数据源。

## OpenAlex

### 端点

```
GET https://api.openalex.org/works
```

### 关键参数

| 参数 | 说明 | 本项目用法 |
|---|---|---|
| `search` | 全文模糊检索，最宽松 | `broad` / `core` 变体用 |
| `filter` | 结构化过滤，精度高 | 年份、类型、字段 |
| `sort` | 排序 | `cited_by_count:desc` 取奠基文献，`relevance_score:desc` 取相关性 |
| `per-page` | 每页条数，上限 200 | 用 50，兼顾覆盖与成本 |
| `select` | 只返回指定字段，显著减小响应体 | 只取需要的字段，含 `referenced_works` |
| `mailto` | 礼貌池标识 | 取 `RESEARCH_CONTACT_EMAIL` |
| `api_key` | 独立额度 | 取 `OPENALEX_API_KEY`，可选 |

### 常用 filter

```
from_publication_date:2012-01-01
to_publication_date:2026-12-31
type:article | review | preprint
is_retracted:false
has_doi:true
primary_topic.field.id:fields/17          # 17 = Computer Science
title_and_abstract.search:"graph neural network"
```

`filter` 的多个条件用逗号连接，表示 AND。`|` 表示 OR。

### 字段映射

| OpenAlex 字段 | 本项目字段 | 注意 |
|---|---|---|
| `id` | `openalex_id` | 形如 `https://openalex.org/W2741809807` |
| `doi` | `doi` | 形如 `https://doi.org/10.1021/...`，需去掉前缀只留 `10.x/...` |
| `title` / `display_name` | `title` | 两者可能分别为空，取非空的那个 |
| `publication_year` | `year` | 可能为 null，需兜底 |
| `cited_by_count` | `cited_by` | 预印本常为 0 |
| `type` | `type` | `article` / `review` / `preprint` / `book-chapter` |
| `primary_location.source.display_name` | `venue` | 期刊/会议名，可能为 null |
| `primary_location.landing_page_url` | `url` | 兜底用 `id` |
| `authorships[].author.display_name` | `authors` | 只取前 8 位，避免报告臃肿 |
| `abstract_inverted_index` | `abstract` | **倒排索引**，必须重建（见下） |
| `referenced_works_count` | `ref_count` | 参考文献数，判断综述的一个弱信号 |
| `referenced_works` | `referenced_works` | 被引文献的 OpenAlex ID 列表，用于集合内引文边、演化顺序与共被引；每条最多保留 300 个 |
| `id` | `openalex_id` | 短 ID（W...），用于匹配引文边 |
| `topics[].display_name` | `topics` | 新版字段，取 score 前 5 |
| `concepts[].display_name` | `concepts` | 旧版字段，保留兼容；有 topics 时优先用 topics |

### 摘要重建（必做）

OpenAlex 不直接给摘要文本，而是给 `{词: [位置列表]}` 的倒排索引。必须重建，否则后续所有基于摘要的判断都失效：

```python
def rebuild(inv):
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))
```

### 成本与限流

- 单次请求成本约 **$0.001**，响应体会返回 `cost_usd` 字段
- 无 key 时免费额度 **$0.10/天，按 IP 共享**——同一台机器上的其他程序也会消耗
- 因此：**默认 5 次请求/次检索**（broad 1 + core 1 + review 1 + 引用排序 1 + 再补 1），并做磁盘缓存
- 遇到 429 说明额度耗尽，**不要重试**，直接降级到 arXiv 并在 warnings 里说明

### 降级

| 情况 | 处理 |
|---|---|
| 429 / 额度耗尽 | 只用 arXiv 结果，报告里明确写"本次未覆盖期刊文献，建议明日重跑" |
| 5xx / 超时 | 重试 1 次（间隔 2 秒），仍失败则降级 |
| `results` 为空 | 不降级，回到 query-builder 调整检索式 |

## arXiv

### 端点

```
GET http://export.arxiv.org/api/query
```

### 参数

| 参数 | 说明 |
|---|---|
| `search_query` | 支持 `all:` `ti:` `abs:` `cat:` 字段前缀与 `AND` / `OR` / `ANDNOT` |
| `start` | 起始位置 |
| `max_results` | 上限 100，本项目用 30–50 |
| `sortBy` | `relevance` / `submittedDate` / `lastUpdatedDate` |
| `sortOrder` | `descending` |

例：

```
search_query=all:"graph neural network" AND all:"drug discovery"
sortBy=relevance&max_results=50
```

### 解析要点

- 命名空间：Atom `http://www.w3.org/2005/Atom`，arXiv 扩展 `http://arxiv.org/schemas/atom`
- 标题与摘要字段常有换行和多余空格，需 `" ".join(text.split())` 规整
- `arxiv:doi` 可能为空——这时用 `id`（`http://arxiv.org/abs/2401.xxxxx`）作为链接
- `arxiv:primary_category@term` 给分类码（如 `cs.LG`），可作为主题标签
- **请求间隔至少 3 秒**，连续快跑会被限流

## 不接入 Crossref

本项目只允许 OpenAlex + arXiv。需要回验 DOI 时，用浏览器打开 `https://doi.org/<doi>` 人工确认，不要在脚本里新增 Crossref 或其他数据源请求。`scripts/verify_doi.py` 不属于主流程，不应被自动调用。

## 综述/教程识别与去噪

`fetch_refs.py` 不是只搜 `review` 关键词，而是合并三路信号：

| 信号 | 强度 | 说明 |
|---|---|---|
| OpenAlex `type:review` | 强 | 出版方已标注，但覆盖不全 |
| 标题模式 | 强 | `survey / review / overview / tutorial / state of the art / advances in / progress in / comprehensive review / roadmap / taxonomy / open problems` |
| 摘要模式 | 中 | `this paper surveys`、`we review`、`we provide an overview`、`literature review`、`systematic review`、`meta-analysis` |

标题反例 `peer review` / `reviewer` / `book review` / `code review` 会先排除。标题带 `tutorial` 的单独标记，作为最优先的骨架。

去噪不只看"关键词出现过"，而是算主题中心度：

| scope | 判定 | 去向 |
|---|---|---|
| `core` | 标题命中 focus，或摘要命中 focus ≥2 次 | 主分层 |
| `mention` | 摘要只命中 focus 1 次 | 附录（仅提及） |
| `related` | 只命中 broad 相邻词 | 附录（相邻领域） |
| `off_topic` | 什么都未命中 | 检索阶段丢弃 |

这样 "6G 综述里顺带提到 OTFS" 不会和 "OTFS 检测算法" 混进同一张必读清单。

## 引文关系与演化顺序

`rank_refs.py` 用 `referenced_works` 构建集合内引文图：

- 边方向：被引文献 → 引用文献（时间流）
- 深度：入度为 0 的节点为 D0，引用 D0 的为 D1，依此类推
- 深度相同再按年份排，不按时间流水账
- 共被引：两篇文献被同一批集合内论文同时引用，说明常被放在一起比较
- 若集合内边少于 3 条，标记"引文关系不足"并退回按年份排序；不得假装有演化顺序

注意：OpenAlex 的 `referenced_works` 对预印本和新论文可能为空，覆盖率会写进 `citation_analysis.coverage`。覆盖率低时，演化顺序只能作参考。

## 2026 年新论文优先

三层机制：

1. 检索层：`year_priority=true` 单独拉当年整年，按 `publication_date:desc`
2. 排序层：`frontier` 当年优先，`score` 对近 12 个月加权
3. 交付层：`latest` 当年排最前，报告标 "★当年"

只靠"按被引数排序"会天然淘汰当年论文（被引 0）。只靠 `latest` 通道也不够——如果检索阶段没拿到，后面变不出来。

## 时效性：为什么"最新"需要单独一条通道

两个数据源的收录节奏完全不同：

| 源 | 从发表到可检索 | 说明 |
|---|---|---|
| arXiv | 当天～1 天 | 预印本，提交即上线，但未经同行评审 |
| OpenAlex | 2 周～3 个月 | 依赖出版方回传元数据，期刊越慢它越慢 |
| PubMed | 1～4 周 | 医学领域相对快 |

这带来两个工程后果：

1. **按引用数排序天然抹掉最新论文**。刚上线的论文被引为 0，在任何"按影响力排序"的体系里都排不进去。所以脚本单独切一条 `latest` 通道，按发表日期倒序，不看引用。
2. **数据截止日必须写进报告**。用户以为看到的是"今天的状态"，实际上可能停在三个月前。报告头部的"数据截止"就是这个提示。

若用户要求"最新到上个月"：把 `recent_days` 调到 60–90，并把 arXiv 的 `submittedDate` 检索条数加大；同时接受一个现实——本月刚见刊的论文可能还没被索引，应当直接告知用户，而不是用旧论文凑数。

## 缓存

`fetch_refs.py` 默认把每个响应按 `sha1(url+params)` 缓存到 `.cache/refs/`，有效期 30 天。

意义有两点：

1. 省额度（OpenAlex 按 IP 共享，重复检索很浪费）
2. 让调试可复现——改排序规则时不该重新拉数据

`--no-cache` 强制刷新，只在确认数据源已更新时用。

## 数据源覆盖边界（必须告知用户）

报告里要写清这几条，否则用户会误以为拿到了全量：

- 不覆盖中文文献（CNKI / 万方），中文综述建议用户自行补充
- 不覆盖未被 OpenAlex 收录的期刊（部分中文核心、部分会议）
- 会议论文在 OpenAlex 的覆盖率低于期刊，CS 领域尤其要注意顶会论文可能漏
- 最新 3 个月的预印本可能尚未收录进 OpenAlex，需靠 arXiv 补