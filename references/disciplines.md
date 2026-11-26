# 学科模板与差异

同一个技能不能对所有学科用一套标准。`assets/profiles/` 下有三套模板，决定数据源、分类码、加权规则和覆盖边界话术。

## 三套模板

| 维度 | general | cs | medicine |
|---|---|---|---|
| 数据源 | OpenAlex + arXiv | OpenAlex + arXiv | **PubMed** + OpenAlex |
| 分类码 | 无 | arXiv 分类码（cs.LG / cs.CL / cs.IT…） | 无 |
| 主题词 | 关键词簇 | 分类码 + 关键词 | **MeSH Terms** + 关键词 |
| 加权 | 无 | 顶会/顶刊 venue +2.0 | 证据等级 +0~3.0 |
| 奠基阈值 | 5 年 | 5 年 | 8 年（经典结论有效期长） |
| 综述偏好 | survey/review | survey/tutorial | guideline / systematic review / meta-analysis |
| 默认最新窗口 | 183 天 | 183 天 | 365 天 |

用法：

```bash
python scripts/fetch_refs.py --spec work/query.json --profile medicine --out work/refs.jsonl
python scripts/rank_refs.py --in work/refs.jsonl --out work/refs_ranked.json --profile medicine ...
```

也可以在 `query.json` 里写 `"profile": "medicine"`，两个脚本都会读。

## 怎么选模板

| 用户方向 | 模板 | 判断依据 |
|---|---|---|
| 计算机、AI、通信、软件工程 | `cs` | 成果以顶会为主，arXiv 是前沿主渠道 |
| 临床医学、公卫、护理、生医工 | `medicine` | 需要指南/共识，检索依赖 MeSH |
| 材料、化学、物理、数学、经管、社科 | `general` | 以期刊为主，分类码不统一 |
| 跨领域（A 方法 → B 问题） | 两边各跑一次 | 见下文"跨领域选题" |

模板选错不会报错，但会明显降低质量：用 general 跑医学，会漏掉 PubMed 收录的指南；用 medicine 跑 CS，会拿不到 arXiv 最新预印本。

## CS 模板要点

- **顶会优先**。NeurIPS/ICML/ICLR/CVPR/ACL/KDD 等论文被引可能低于期刊，但圈内地位更高。`rank_refs.py --profile cs` 对 venue 命中顶会列表的记录加 2.0 分。
- **arXiv 必跑**。否则漏掉最近半年 SOTA。arXiv 记录 `type=preprint`，报告里标注"预印本（arXiv）"。
- **分类码**。`spec.categories` 写 arXiv 分类码（如 `cs.LG`、`cs.CV`、`eess.SP`），可显著降低跨领域同名词污染。CCS 分类（如 `CCS → Information systems → ...`）没有通用 API，通常作为关键词写进 `core`。
- **OpenAlex 顶会覆盖有限**。CS 顶会在 OpenAlex 的元数据覆盖率低于期刊，报告必须写明"可能漏掉部分顶会，建议用 DBLP 补查"。

## 医学模板要点

- **MeSH 是检索质量的核心**。让用户给 2–3 个 MeSH 主题词（PubMed 页面的 "MeSH terms" 区域），写进 `spec.mesh`。脚本会拼成 `"Diabetic Retinopathy"[MeSH Terms]`。
- **指南与共识必须单列**。必读清单如果全是原始研究，说明检索不完整；指南需要到 WHO / NCCN / 中华医学会等机构网站人工补查，脚本不保证覆盖。
- **证据等级**。PubMed 的 `PublicationType` 映射为 guideline / consensus / meta_analysis / systematic_review / rct / clinical_trial / cohort / case_control / review / other，`rank_refs.py --profile medicine` 按 `evidence_weights` 加权。报告里 `evidence_level=other` 的条目显示"未标注"，不要假装它是 RCT。
- **预印本分离**。medRxiv 不在 PubMed 主库，OpenAlex 可能收录但类型不是预印本；报告里要把预印本结论与已发表结论分开。
- **基础研究 vs 临床研究**。两者混在一张表里会让导师认为你没读懂领域，分层时按期刊/标题粗分并在正文说明。

## general 模板要点

- 材料/化学：综述价值极高，review 层配额可放宽；关注合成路线与表征手段。
- 经管/社科：经典文献时效性弱，foundational 层年份阈值放宽到 10 年以上；区分相关性与因果识别（DID/IV/RDD/RCT）。
- 数学/物理：arXiv 覆盖好，但会议论文集（如 NeurIPS 之外的纯数学会议）覆盖差，需人工补。

## 跨领域选题

用户从 A 领域转到 B 领域时，最有价值的选题通常是"A 的方法解决 B 的问题"。要做两轮检索：

1. 在 A 领域搜该方法的最新进展（方法侧现状）
2. 在 B 领域搜该问题的主流解法（问题侧现状）
3. 两者的交集是迁移机会，交集为空是潜在空白（仍需验证）

两次检索分别存 `refs_a.jsonl` / `refs_b.jsonl`，合并后跑一次 `rank_refs.py`。报告里必须写"本报告基于两个领域的交叉检索"，否则用户会以为覆盖不全。

## 判定"这是不是综述"

标题候选词：

```
review, survey, overview, tutorial, state of the art, advances in,
progress in, recent developments, comprehensive, systematic review,
meta-analysis, primer, perspective, roadmap, taxonomy, open problems
```

摘要句式：`this paper surveys`、`we review`、`we provide an overview`、`literature review`、`systematic review`、`meta-analysis`。

反例（标题含 review 但不是综述）：`peer review`、`reviewer`、`book review`、`code review`。

医学额外信号：PubMed `PublicationType = Review / Systematic Review / Meta-Analysis / Practice Guideline`。指南（guideline）也可以当骨架读，但它回答的是"应该怎么做"，不是"领域是怎么演化的"，两者不能互相替代。

## 去噪：主题论文 vs 关联文献

| scope | 例子（医学影像） | 处理 |
|---|---|---|
| core | 标题含 `diabetic retinopathy` 且做检测/分割 | 进必读 |
| mention | 糖尿病综述里提了一句"AI 视网膜筛查" | 附录 A |
| related | 只写 `fundus image`，未提糖尿病 | 附录 A |
| off_topic | 完全无关 | 丢弃 |

报告必须写清去噪前后的数量和"仅提及/相邻领域"的条数，否则用户会以为清单覆盖了全部相关文献。