# 数据源与接口细节

## 总体策略

| 源 | 角色 | 覆盖 | 缺点 |
|---|---|---|---|
| **OpenAlex** | 通用主力 | 全学科 2.5 亿+ 作品，自带被引数与引文关系 | 最新预印本收录延迟；免费额度按 IP 共享 |
| **arXiv** | CS/物理前沿补充 | 预印本，提交当天可检索 | 无被引数、无同行评审 |
| **PubMed** | 医学主力 | MEDLINE 期刊、指南、系统综述 | 无被引数、无引文关系（需 OpenAlex 补） |
| **Crossref** | **仅 DOI 回验** | DOI 元数据核对 | 不用于文献发现 |

组合逻辑：OpenAlex 给广度和引文，arXiv 补最新预印本，PubMed 补医学权威记录，Crossref 只做回验。不要新增其他数据源。

## OpenAlex

### 端点

```
GET https://api.openalex.org/works
```

### 关键参数

| 参数 | 说明 | 本项目用法 |
|---|---|---|
| `search` | 全文模糊检索 | broad / core 变体 |
| `filter` | 结构化过滤 | 年份、类型、字段 |
| `sort` | 排序 | 奠基层用 cited_by_count |
| `per-page` | 每页条数，上限 200 | 50 |
| `select` | 只返回指定字段 | 含 referenced_works |
| `mailto` | 礼貌池标识 | `RESEARCH_CONTACT_EMAIL` |
| `api_key` | 独立额度 | `OPENALEX_API_KEY` |

### 常用 filter

```
from_publication_date:2012-01-01
to_publication_date:2026-12-31
type:article | review | preprint
is_retracted:false
has_doi:true
primary_topic.field.id:fields/17
title_and_abstract.search:"graph neural network"
```

### 字段映射

| OpenAlex | 本项目 | 注意 |
|---|---|---|
| `id` | `openalex_id` | `W2741809807` |
| `doi` | `doi` | 去掉 `https://doi.org/` 前缀 |
| `title` / `display_name` | `title` | 取非空者 |
| `publication_year` | `year` | 可能为 null |
| `cited_by_count` | `cited_by` | 预印本常 0 |
| `type` | `type` | article/review/preprint |
| `primary_location.source.display_name` | `venue` | 可能为空 |
| `authorships[].author.display_name` | `authors` | 只取前 8 位 |
| `abstract_inverted_index` | `abstract` | **倒排索引，必须重建** |
| `referenced_works` | `referenced_works` | 引文边，每条最多 300 个 |
| `topics[].display_name` | `topics` | 取前 5 |

### 摘要重建（必做）

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

- 无 key 时免费额度约 **$0.10/天，按 IP 共享**
- 脚本默认固定请求数并做磁盘缓存；**同一查询不要重复运行**
- 429 表示额度耗尽：**不要重试**，降级到 arXiv/PubMed，并在 warnings 里说明

### 降级

| 情况 | 处理 |
|---|---|
| 429 / 额度耗尽 | 只用其他源，报告写"本次未覆盖期刊文献，建议明日重跑" |
| 5xx / 超时 | 重试 1 次（2 秒后），仍失败降级 |
| `results` 为空 | 不降级，回 query-builder 改检索式 |

## arXiv

```
GET https://export.arxiv.org/api/query
```

| 参数 | 说明 |
|---|---|
| `search_query` | 支持 `all:` `ti:` `abs:` `cat:` 与 AND/OR/ANDNOT |
| `max_results` | 上限 100，本项目 30–50 |
| `sortBy` | relevance / submittedDate |
| `sortOrder` | descending |

解析要点：

- 命名空间 Atom `http://www.w3.org/2005/Atom`，扩展 `http://arxiv.org/schemas/atom`
- 标题/摘要常有换行，需 `" ".join(text.split())`
- `arxiv:doi` 可能为空，用 `id`（abs 链接）兜底
- **请求间隔至少 3 秒**

## PubMed

脚本用 E-utilities 两步：`esearch` 拿 PMID 列表，`efetch` 拿 XML。无需 key。

### 端点

```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi
```

### 参数

| 参数 | 说明 |
|---|---|
| `db` | 固定 `pubmed` |
| `term` | 检索式（见 query-builder） |
| `retmax` | 每轮条数 |
| `retmode` | esearch 用 `json`，efetch 用 `xml` |
| `sort` | `relevance` / `pub_date` |
| `datetype=pdat` + `mindate`/`maxdate` | 按出版日期限年 |
| `email` / `api_key` | 可选；无 key 限速 3 次/秒 |
| `NCBI_API_KEY` | 环境变量，有 key 可提到 10 次/秒 |

脚本跑两轮：相关性一轮 + 按发表日期一轮（补最新），间隔 0.4 秒。

### 字段映射

| PubMed XML | 本项目 | 注意 |
|---|---|---|
| `MedlineCitation/PMID` | `pmid` | 无 DOI 时作 ref_key 兜底 |
| `ArticleTitle` | `title` | 可能含 HTML 实体，需清洗 |
| `Abstract/AbstractText` | `abstract` | 多段带 Label，拼成一段 |
| `Journal/Title` | `venue` | 期刊全名 |
| `AuthorList/Author` | `authors` | LastName + ForeName，前 8 位 |
| `PubDate` | `year` / `pub_date` | 处理 MedlineDate（如 `2023 Jan-Feb`） |
| `ArticleIdList/ArticleId[@IdType=doi]` | `doi` | 优先 DOI |
| `ELocationID[@EIdType=doi]` | `doi` | DOI 兜底 |
| `PublicationTypeList` | `publication_types` | 用于证据等级 |
| `MeshHeadingList` | `mesh_terms` / `topics` | 主题标签 |
| `KeywordList` | `topics` | MeSH 不足时补充 |

### 证据等级

`evidence_level` 由 `PublicationType` 映射：

| PublicationType 含 | evidence_level |
|---|---|
| Practice Guideline / Guideline | guideline |
| Consensus Development Conference | consensus |
| Meta-Analysis | meta_analysis |
| Systematic Review | systematic_review |
| Randomized Controlled Trial | rct |
| Clinical Trial | clinical_trial |
| Observational Study / Cohort | cohort |
| Case-Control | case_control |
| Review | review |
| 其他 | other |

`rank_refs.py --profile medicine` 按 `assets/profiles/medicine.json` 的 `evidence_weights` 加权。

### 限制

- PubMed 不提供被引数：`cited_by=0`，靠 OpenAlex 合并同 DOI 记录补被引
- 不提供 `referenced_works`：医学模板的引文覆盖会偏低，报告要如实写"引文关系不足"
- 指南、共识常有独立网站版本，PubMed 可能只收录摘要，需人工补原文

## Crossref：只做回验

`verify_doi.py` 用 Crossref 核对 DOI 元数据：

```
GET https://api.crossref.org/works/<doi>
```

判定口径：

| 状态 | 条件 | 处理 |
|---|---|---|
| `verified` | 标题相似度 ≥0.92 且年份/第一作者基本一致 | 放行 |
| `partial` | 有字段不一致但不严重 | 放行并标记 |
| `link_only` | 无 DOI，但有 arXiv/PubMed/landing page | 放行，标"按链接回溯" |
| `mismatch` | 标题相似度 <0.92 或年份/作者冲突 | 剔除 |
| `not_found` | Crossref 404 | 剔除 |
| `no_doi` | 无 DOI 且无链接 | 剔除 |
| `error` | 网络异常 | 剔除（宁可少一篇） |

**不要**用 Crossref 做检索或补文献。它只负责"这条记录是不是真的"。

## 时效性：为什么"最新"需要单独通道

| 源 | 从发表到可检索 | 说明 |
|---|---|---|
| arXiv | 当天–1 天 | 预印本，未经评审 |
| PubMed | 1–4 周 | 医学相对快 |
| OpenAlex | 2 周–3 个月 | 依赖出版方回传 |

两个工程后果：

1. **按引用排序天然抹掉最新论文**（被引 0），所以单独切 `latest` 通道，按日期倒序
2. **数据截止日必须写进报告**，否则用户以为看到的是"今天的状态"

用户要"最新到上个月"时：`recent_days` 调到 60–90，加大 arXiv/PubMed 日期检索条数，并明确告知本月刚见刊的论文可能还没被索引。

## 缓存

`fetch_refs.py` 默认把响应按 `sha1(url+params)` 缓存到系统临时目录 `research-landscape-cache/`，有效期 30 天。

意义：省额度（OpenAlex 按 IP 共享）、让调试可复现。`--no-cache` 强制刷新，只在确认数据源已更新时用。

## 数据源覆盖边界（必须告知用户）

- 不覆盖中文文献（CNKI / 万方），中文综述需自行补充
- 不覆盖未被 OpenAlex / PubMed 收录的期刊与会议
- CS 顶会在 OpenAlex 覆盖率低于期刊，可能漏检
- 医学指南/共识的权威版本常在机构网站，PubMed 可能只有摘要
- 最新 3 个月的期刊论文可能尚未建索引，靠 arXiv/PubMed 日期检索兜底