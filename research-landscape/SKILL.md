---
name: research-landscape
description: 输入一个陌生研究方向或几篇论文，输出可执行的"科研方向地图"——把模糊兴趣收敛成研究问题，再给出领域脉络、技术路线表、分层必读文献、专业术语表、疑似研究空白，并能一键导出开题素材包。面向刚起步的本科生、研究生、跨领域转行者和准备开题的人，解决"搜出一堆论文不知道从哪读、不知道这个领域在争什么、不知道还能做什么"。触发场景：科研方向、研究方向、找课题、开题、选题、毕设选题、论文选题、文献调研、文献综述、领域综述、必读文献、读什么论文、研究脉络、技术路线、研究空白、research gap、literature review、survey、这个领域现在做到哪一步了、我想做某个方向但不知道怎么入手、导师让我找方向、帮我看看这个领域值不值得做、入门某领域要看什么、给我几篇论文帮我理一理。只要用户流露出"想进入某个研究领域或为一个课题做准备"的意图，即使没有出现"skill""检索""搜索"这类词，也应使用本技能。
---

# 科研方向地图（Research Landscape）

## 这个技能解决什么

小白用户的真实困境不是"搜不到文献"，而是**搜出来几百篇，不知道从哪读起、不知道这个领域在争什么、不知道哪里还有缝**。所以本技能的核心动作不是检索，而是四步：

1. **收敛**——把"我想做 AI 医疗"压成一个可检索、可执行的研究问题
2. **排序**——把几百篇文献压成 20 来篇有明确阅读顺序的清单
3. **指缝**——用证据指出哪些方向还没被做透（标注为未验证）
4. **落地**——把以上内容组织成开题素材包，直接能改

只有用户**直接给论文**（而不是给方向）时，才走"论文入口"分支：先读这几篇的元数据与摘要 → 反推所属子领域与作者群 → 再回到正常流程做领域级检索。不要只总结这几篇，那等于没解决问题。

反模式（这些做法都会让输出变成废纸，务必避免）：

- 用户说了一句模糊兴趣就立刻开搜，最后输出一篇泛泛而谈的领域介绍
- 给用户列 80 篇文献，等同于没给
- 凭模型记忆写文献条目，DOI 一查就是编的
- 把"我觉得这里可以做"写成研究空白
- 只总结用户给的那几篇论文，不告诉他这个领域有多大、往哪走

## 三条证据规则（不可妥协）

**规则一：文献条目只能来自脚本返回。**
`fetch_refs.py` 从数据源拉回来的记录是唯一合法的文献来源。模型不得凭记忆补文献、不得"顺手加一篇经典"。用户点名要某篇时，用标题去检索；搜不到就明说"本次未检索到，建议自行核实"，并给标题关键词。

**规则二：每条文献必须可回溯。**
渲染前会做回验：有 DOI 的用 Crossref 核对标题/年份/第一作者；无 DOI 但有 arXiv/PubMed 链接的，按链接兜底（标 `link_only`，报告中注明"无 DOI，按链接回溯"）。两者都没有的条目一律剔除。一条查不到的文献不如没有。

**规则三：区分事实与推测。**
引用论文的内容是事实，可以直说；判断某方向"没人做过"是推测，必须写清依据（例如"在本次检索的 60 篇记录中未见该方法应用于 X"）。空口断言"这个方向是空白"会直接毁掉用户对整份报告的信任。

## 环境

需要 Python 3.9+ 和 requests（`pip install requests`）。数据源不需要密钥：

- **OpenAlex**：通用主力源，覆盖全学科，自带被引数与引文关系。免费额度按 IP 共享，脚本内置磁盘缓存，**同一查询不要重复运行**。
- **arXiv**：预印本补充，抓最近 6–12 个月前沿（OpenAlex 对最新预印本收录有延迟）。
- **PubMed**：医学模板启用（`--profile medicine`），走 NCBI E-utilities，无需 key（有 key 可提速）。
- **Crossref**：只用于 DOI 回验，**不作为检索源**，不参与文献发现。

可选环境变量：`OPENALEX_API_KEY`、`RESEARCH_CONTACT_EMAIL`、`NCBI_API_KEY`、`CROSSREF_MAILTO`。

网络不可达时：脚本会降级并在 warnings 里说明，报告必须如实写"本次未覆盖 X"。**不要用模型记忆补数据。**

**命令执行位置**：下文所有 `python scripts/...` 命令都在 Skill 根目录执行。在项目目录里直接调用时，把 `scripts/` 换成该 Skill 的安装路径（例如 `<skills>/research-landscape/scripts/...`），否则会找不到脚本。

## 工作流程

### Phase 0 · 澄清（必做，未确认不进 Phase 1）

先读 `references/clarification.md`，按提问脚本问 **3–4 个结构化问题**（用选项，不要开放式）：阶段、可用资源、时间预算、偏好倾向。医学/CS 领域再追加一个学科特有项（MeSH 词或目标会议）。

拿到回答后输出**方向确认卡**：一句话研究问题 + 做什么/不做什么 + 中英文关键词簇 + 所属子领域。用户点头再进下一步。

用户直接给论文时的处理：读摘要与关键词 → 用这几篇的标题词构造 `focus` → 在方向卡里写明"起点论文"与"由它扩散出的子领域"。不要跳过方向确认。

论文入口的输入边界：支持标题、DOI、arXiv ID 与摘要文本；**不解析 PDF 全文**。用户发来 PDF 时，先让他给出标题或 DOI，再走检索——不要把 PDF 内容当作已核验的领域事实。

### Phase 1 · 选模板并检索

1. **选学科模板**（`assets/profiles/`）：`general` / `cs` / `medicine`。用户没明说时按方向判断；判断依据与差异见 `references/disciplines.md`。模板决定数据源、分类码、顶会列表、证据等级权重和覆盖边界话术。
2. 读 `references/query-builder.md`，把自然语言转成检索式，写入 `work/query.json`（**不要**直接塞命令行，Windows 下引号会被拆开）。医学检索补 `mesh` 字段，CS 补 `categories`：

```json
{
  "profile": "medicine",
  "core": ["deep learning", "diabetic retinopathy", "diagnosis"],
  "focus": ["diabetic retinopathy", "retinopathy"],
  "broad": ["diabetic retinopathy", "fundus image", "retinal image"],
  "mesh": ["Diabetic Retinopathy", "Deep Learning"],
  "years": "2019-2026",
  "recent_days": 365,
  "year_priority": true
}
```

3. 运行检索（profile 已在 spec 中时可不传 `--profile`）：

```bash
python scripts/fetch_refs.py --spec work/query.json --profile medicine --out work/refs.jsonl
```

4. **去噪先于排序**。结果按主题中心度分四类：`core`（标题命中或摘要多次命中 focus）才是主题论文；`mention`（摘要只提一次）和 `related`（只命中相邻词）只算关联文献；`off_topic` 丢弃。`core` 不足 8 篇时放宽 `focus` 或年份，不要硬撑着往下走。
5. **先看综述/教程**。脚本用"类型 + 标题 + 摘要"三路信号识别综述，不只匹配标题里的 `review`。综述命中为 0 时，去掉方法段单独搜 `review OR survey OR tutorial` 重跑。
6. 检查 warnings，并检查 `newest_date` 与当年论文数。当年为 0 或最新距今超过 2 个月时，说明数据源收录滞后：补 arXiv/PubMed 的日期检索，并在报告里写明，**不要拿旧论文冒充最新**。

### Phase 2 · 排序、去噪与分层

```bash
python scripts/rank_refs.py --in work/refs.jsonl --out work/refs_ranked.json \
  --topic "你的研究问题" --focus "diabetic retinopathy,retinopathy" \
  --profile medicine --recent-days 365 --latest-limit 10 --max-refs 25
```

脚本做七件事：按 DOI/标题作者去重并合并多源记录、按主题中心度分流、生成五层、用引文关系排演化、生成年度时间轴、从综述抽术语种子、切一条"最新"通道。`--profile` 会额外生效：CS 顶会加权、医学证据等级加权、 `foundational` 年份阈值放宽。

五层含义：

| 层 | 含义 | 给用户的作用 |
|---|---|---|
| `review` | 骨架：综述 / survey / tutorial / 指南 | 先读，快速建立领域地图 |
| `foundational` | 奠基性：阈值以上高被引 | 理解问题是怎么被提出的 |
| `frontier` | 前沿：近 2 年，当年优先 | 知道 SOTA 在哪、谁在领跑 |
| `competitive` | 竞争：结论冲突或路线相反 | 知道这个领域在争什么 |
| `core` | 主干补充 | 补完前四层之间的缝隙 |

配额与入选数必须对得上。脚本按权重分配目标配额，某层候选不足时回填并在 `meta.quota_target / quota_actual / quota_shortfall` 和 warnings 里写清。报告要原样展示差异，**不允许手写"共 25 篇"而实际只有 18 篇**。

**分层结果是机器规则给的，需要你复核**。规则会漏掉"引用不高但方法很关键"的论文，也会误判标题带 review 的普通文章。复核结论写进报告，不要静默改分层——用户需要知道这是算法结果还是人的判断。

### Phase 3 · 写脉络、技术路线、术语与空白

这是模型真正出力的地方，脚本帮不上忙。

```bash
python scripts/build_report.py --init-plan work/plan.json --refs work/refs_ranked.json
```

脚本生成 `plan.json` 骨架，已填好文献 key、五层分层、`_citation`（引文演化）、`_term_seeds`、`_peripheral`、`_quota`。你填叙述部分：

- **领域脉络**：按 `citation_analysis.layers` 的引文顺序（不是年份流水账）梳理 3–5 条主线。每条写"起点 → 关键跃迁 → 当前 SOTA"，节点都挂 `ref_key`，并写清**为什么转向**（数据变了、算力够了、旧方法撞墙了）。
- **技术路线**：方法族表格，每行必须挂真实论文，`routes[].ref_keys` 里的 key 必须存在。不能把一篇论文塞进五条路线充数。
- **术语表**：优先从综述摘要抽，五类标注（入门/核心/方法/指标/易混），中英对照 + ≤30 字解释。OpenAlex/PubMed 只给摘要时，来源标"综述摘要"，**不要假装读了 Introduction**。
- **研究空白**：3–5 条候选，每条带证据链，默认全部按"三级置信度（推测）"写，明确标注未验证。
- **为什么必读**：每条理由要具体（"理解问题起源"合格，"很重要"不合格）。

### Phase 4 · 回验、渲染与开题素材

```bash
# 一键：回验 → 校验 → 渲染 → 导出素材包
python scripts/run_pipeline.py render --work-dir work \
  --report 科研方向地图.md --bib 参考文献.bib --proposal-dir 开题素材
```

分步执行也可以：

```bash
python scripts/verify_doi.py --in work/refs_ranked.json --out work/refs_verified.json
python scripts/build_report.py --plan work/plan.json --refs work/refs_verified.json \
  --out 科研方向地图.md --bib 参考文献.bib --verify
python scripts/export_proposal.py --plan work/plan.json --refs work/refs_verified.json \
  --report 科研方向地图.md --out-dir 开题素材
```

回验口径：

- `verified`：标题相似度 ≥0.92，年份与第一作者基本一致 → 放行
- `partial`：部分字段不一致 → 放行，报告标注
- `link_only`：无 DOI，但有 arXiv/PubMed 链接 → 放行，标注"按链接回溯"
- `mismatch` / `not_found` / `no_doi`（且无链接）/ `error` → **不进报告**，终端逐条列出

这是硬门禁：带 `--verify` 但找不到回验结果时，渲染器拒绝渲染。`run_pipeline.py render --skip-verify` 等价于 `build_report.py --no-verify`，只用于 Crossref 不可达的应急场景；此时报告会自动写"本次未做 DOI 回验"，交付时也要口头说明。

导出素材包前会再跑一次 `build_report.validate`：plan 没填完、或引用了被回验剔除的文献时，导出直接中止。确认要强制导出才加 `--force`（不推荐）。

渲染器校验（不过就中止，**不要绕过校验改脚本**）：

- 方向卡有研究问题；至少 1 条主线，每条至少 1 个节点
- 至少 1 条方法族，每条的 `ref_keys` 非空，且 key 真实存在、未被回验剔除
- 每篇必读都有"为什么必读"（≥6 字）
- 术语表非空、≤30 条、单条解释 ≤30 字、层级合法
- 疑似空白非空、≤5 条，证据必须含检索范围或"本次检索未见"限定词
- 叙述引用的每篇文献都有可回溯链接；被回验剔除的文献不得出现在主线或路线里

产物：

- `科研方向地图.md`：主报告，可直接给导师看
- `参考文献.bib`：导入 Zotero / EndNote
- `开题素材/`：8 个固定文件（速览、背景、现状、路线、创新点、答辩预演、bib、README）；传 `--report` 时会额外生成 `00_地图入口.md`。结构见 `references/proposal-export.md`

交付时明确告诉用户"这周该读哪 3 篇"，不要只丢清单。

## 三条入口的自然处理

| 用户输入 | 处理 |
|---|---|
| 一个方向名（"我想做 AI 医疗"） | 正常流程：澄清 → 选模板 → 检索 |
| 几篇论文（"帮我理一理这几篇"） | 论文入口：读元数据/摘要 → 反推子领域 → 用标题词做 `focus` → 领域级检索，并在方向卡里写明起点论文 |
| 已有开题材料（"帮我看这个方向值不值得做"） | 先抽取其研究问题与关键词 → 补检索 → 重点输出竞争层与空白候选 |

## 产物契约

报告固定包含这些块，顺序不变。模板见 `assets/report-template.md`。

| 块 | 内容 | 验收标准 |
|---|---|---|
| 方向确认卡 | 一句话问题、范围、关键词簇、检索式 | 用户能拿这句话去跟导师汇报 |
| 领域脉络 | 引文顺序 + 3–5 条主线 | 能回答"这领域发生了什么、为什么转向" |
| 技术路线 | 方法族表格，每行挂代表论文 | 每行落到具体 `ref_key` |
| 必读文献 | 五层清单，≤25 篇，带配额目标/实际与理由 | 可回溯、有阅读顺序、配额差异有原因 |
| 近期进展 | 近 6–12 个月，当年优先 | 最新能追到当月，当年标"★当年" |
| 术语表 | 五类标注，≤30 条，带来源 | 来源写"综述摘要"就如实写 |
| 研究空白 | 3–5 条候选，带证据链 | 只输出"疑似信号"，明确未验证 |
| 附录 A | 仅提及/相邻领域文献 | 与必读分开，写明判定依据 |

医学模板额外要求：标出研究类型（指南 / 系统综述 / RCT / 队列 / 病例对照）、指南与共识单列、预印本与已发表分离。CS 模板额外要求：标出顶会/期刊/预印本，备注代码与数据是否公开（摘要里没有就写"未从摘要确认"）。

## 交付前自检

- [ ] 每条文献都有 DOI 或可访问链接，回验未通过的已剔除
- [ ] 报告头部有"数据截止"，与 `newest_date` 一致
- [ ] 头部有去噪统计：原始 → 去重 → 主题核心 → 仅提及/相邻
- [ ] 必读只含 `core` 主题论文；`mention` / `related` 在附录 A
- [ ] 配额目标与实际一致；不一致时写清哪层缺、缺口给了谁
- [ ] 当年论文数已写出；为 0 时说明原因
- [ ] 引文演化不可用时明确写"引文边不足，按年份排序"
- [ ] 近期进展不为空；为空必须写原因
- [ ] 每篇文献的"为什么必读"是具体的
- [ ] 技术路线表每行都有真实论文
- [ ] 术语 ≤30 条，每条 ≤30 字，有来源
- [ ] 所有推测都带依据或"本次检索未见"的限定
- [ ] 明确告知检索范围、数据源和覆盖边界（含未覆盖中文文献）
- [ ] 开题素材包里的【待验证】【待填】没有被悄悄删除

最后一条最容易跳过，但它是用户判断这份地图能不能信的唯一依据。宁可写清"本次只覆盖 OpenAlex/PubMed 收录的英文文献，中文综述建议自行补充"，也不要让用户以为这是全量的。

## 常见失败模式

| 症状 | 原因 | 处理 |
|---|---|---|
| 文献全是 2016–2019 年的 | 只按引用数排序 | 检查 frontier 检索、arXiv/PubMed 日期检索是否成功 |
| 综述一条没有 | 关键词太窄或只匹配标题 | 放宽检索式；搜 `<对象> survey` / `systematic review` 重跑 |
| 必读混进只提一句的综述 | 没做主题中心度去噪 | 检查 `focus` 与 `scope_counts`；mention/related 移到附录 |
| 写"共 25 篇"实际 18 篇 | 配额未回填或候选不足 | 看 `meta.quota_target / quota_actual`，如实写缺口 |
| 回验后 arXiv 全被剔除 | 把"无 DOI"当"不可回溯" | 检查是否为 `link_only`；无链接才剔除 |
| 2026 年论文一条没有 | 只按被引排序，当年被引 0 | 打开 `year_priority`，补日期检索，检查 `current_year_count` |
| 演化顺序像编年史 | 只读 timeline，没用引文 | 读 `citation_analysis.layers`；引文不足时明确说明 |
| 医学清单全是原始研究，没有指南 | 未启用 medicine 模板 | `--profile medicine`，补 MeSH 词，指南需人工补权威机构网站 |
| 用户说"这跟我导师说的完全不一样" | 方向没收敛就开搜 | 回 Phase 0 重新确认 |
| 报告像百科词条 | 缺"为什么" | 每段问自己：这句话能帮用户做决策吗 |

## 参考文档

按需读取，不要一次全读：

- `references/clarification.md` — Phase 0 提问脚本、方向卡模板、学科追问
- `references/query-builder.md` — 检索式构造、focus 同义词、MeSH/分类码、去噪与当年优先
- `references/disciplines.md` — general / cs / medicine 三套模板的检索与判定差异
- `references/source-apis.md` — OpenAlex / arXiv / PubMed 字段映射、引文分析、降级策略
- `references/evidence-rules.md` — 证据规则判例、回验口径、自检流程
- `references/proposal-export.md` — 开题素材包结构、写作硬规则、待补项写法
- `assets/report-template.md` — 报告结构与排版模板
- `assets/proposal-template.md` — 开题素材包输出契约
- `assets/profiles/` — 学科模板参数（general / cs / medicine）

## 版本边界

已实现（V1+V2+V3 核心）：

- 澄清与方向卡、标准检索式（focus 同义词 + 分类码 + MeSH）、去噪与主题中心度分流
- 综述/教程识别、五层分层、配额一致性、引文关系与共被引、当年优先
- 学科模板：general / cs（顶会加权、arXiv）/ medicine（PubMed + MeSH + 证据等级）
- Markdown 报告 + BibTeX + 开题素材包导出

未实现（不要在输出中假装做了）：

- 综述 Introduction / Related Work 全文解析：数据源只给摘要，术语来源只能标"综述摘要"
- 经实验验证的研究空白：只输出"疑似信号 + 待验证清单"
- 中文文献（CNKI / 万方）、medRxiv 系统收录
- ACM CCS 分类树：CS 模板用 arXiv 分类码 + 顶会列表；CCS 分类只能当关键词写进 `core`，脚本不解析
- 持续追踪与自动推送（V4）：只能给用户一份可重跑的 query.json，不能承诺每周自动推送
- 技术路线全自动生成：脚本只给论文，路线划分与"起点 → 跃迁 → SOTA"由模型写

遇到用户要求这些能力时，明确说明边界并给可替代的手工方案——比如研究空白，退一步输出"疑似信号 + 待验证清单"，让用户自己查证，而不是编一个结论。