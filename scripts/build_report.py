#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Research Landscape V1 - 计划骨架生成与报告渲染。

两种模式：

1) 生成计划骨架（模型在此填叙述部分）
    python scripts/build_report.py --init-plan work/plan.json --refs work/refs_ranked.json

2) 校验并渲染报告
    python scripts/build_report.py --plan work/plan.json --refs work/refs_ranked.json \
        --out 科研方向地图.md --bib 参考文献.bib

渲染前会做硬校验：
- plan 中引用的每个 ref_key 必须真实存在于检索结果
- 入选清单的每篇文献必须有"为什么必读"
- 术语条数与单条解释长度不得超限
校验不过直接报错退出，不产出半成品——半成品的危害大于没有报告。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    # 不强制 utf-8：Windows 控制台默认 GBK，强制 utf-8 会输出乱码
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

TIER_ORDER = ["review", "foundational", "frontier", "competitive", "core"]
TIER_TITLE = {
    "review": ("第一梯队：骨架（综述/教程）", "先读综述与教程，用最短时间拿到领域骨架"),
    "foundational": ("第二梯队：奠基性", "看清这个问题最初是怎么被提出的"),
    "frontier": ("第三梯队：当前前沿", "知道现在的 SOTA 在哪、2026 年谁在领跑"),
    "competitive": ("第四梯队：竞争路线", "同一问题存在相反结论或互斥路线，读完才知道该信谁"),
    "core": ("第五梯队：补充主干", "把前四个梯队之间的缝隙补上"),
}
TERM_LAYERS = ("入门", "核心", "方法", "指标", "易混")
MAX_TERMS = 30
MAX_TERM_DEF = 30
MAX_GAPS = 5


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def esc(text) -> str:
    return str(text or "").replace("|", "/").replace("\n", " ").strip()


def short_title(title: str, limit: int = 88) -> str:
    text = (title or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def cite(rec: dict) -> str:
    authors = rec.get("authors") or []
    year = rec.get("year") or "n.d."
    if not authors:
        return f"(Anon., {year})"
    first = authors[0]
    surname = first.split()[-1] if " " in first else first
    if len(authors) >= 2:
        return f"({surname} et al., {year})"
    return f"({surname}, {year})"


def ref_link(rec: dict) -> str:
    doi = (rec.get("doi") or "").strip()
    if doi:
        return "https://doi.org/" + doi
    return rec.get("url") or ""


# ---------------------------------------------------------------- init plan


def init_plan(refs: dict, out_path: Path) -> int:
    tiers = refs.get("tiers", {})
    why_read = {}
    order = []
    for tier in TIER_ORDER:
        for rec in tiers.get(tier, []):
            why_read[rec["ref_key"]] = ""
            order.append({
                "ref_key": rec["ref_key"],
                "tier": tier,
                "title": rec.get("title"),
                "year": rec.get("year"),
                "cited_by": rec.get("cited_by"),
            })

    skeleton = {
        "topic": refs.get("meta", {}).get("topic", ""),
        "audience": "",
        "time_budget": "",
        "direction_card": {
            "question": "",
            "do": ["", "", ""],
            "dont": ["", ""],
            "subfield": "",
            "keywords_en": [""],
            "keywords_zh": [""],
        },
        "queries": {"core": "", "broad": "", "review": ""},
        "landscape": [
            {
                "line": "",
                "status": "",
                "stages": [
                    {"year": 0, "label": "", "why": "", "ref_key": ""}
                ],
            }
        ],
        "routes": [
            {"family": "", "idea": "", "ref_keys": [""], "fits": "", "limits": ""}
        ],
        "why_read": why_read,
        "latest_notes": {rec["ref_key"]: "" for rec in (refs.get("latest") or [])},
        "terms": [
            {"zh": "", "en": "", "layer": "入门", "def": "", "source": "综述摘要"}
        ],
        "gaps": [],
        "next_steps": ["", "", ""],
        "boundary_notes": "",
        "_order": order,
        "_term_seeds": refs.get("top_terms", []),
        "_review_terms": refs.get("review_terms", []),
        "_timeline": refs.get("timeline", []),
        "_citation": refs.get("citation_analysis", {}),
        "_peripheral": refs.get("peripheral", []),
        "_quota": {
            "target": refs.get("meta", {}).get("quota_target", {}),
            "actual": refs.get("meta", {}).get("quota_actual", {}),
            "consistent": refs.get("meta", {}).get("quota_consistent", True),
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已生成计划骨架: {out_path}")
    print("待填：")
    print("  - direction_card  方向确认卡")
    print(f"  - why_read        {len(order)} 篇文献的'为什么必读'")
    print("  - landscape       3-5 条主线（每节点挂一个 ref_key）")
    print("  - routes          方法族与代表工作")
    print("  - terms           术语表（_term_seeds 里是脚本抽的高频概念）")
    print("  - next_steps / boundary_notes")
    print("可用 ref_key 见 _order；不要新增不在 _order 中的文献。")
    return 0


# ---------------------------------------------------------------- validate


def validate(plan: dict, refs: dict) -> list:
    errors = []
    records = refs.get("records", {})
    tiers = refs.get("tiers", {})

    used_keys = set()

    for line in plan.get("landscape") or []:
        if not (line.get("line") or "").strip():
            errors.append("landscape: 存在没有名称的主线")
        for stage in line.get("stages") or []:
            key = (stage.get("ref_key") or "").strip()
            if not key:
                errors.append(f"landscape/{line.get('line')}: 节点缺少 ref_key")
                continue
            if key not in records:
                errors.append(f"landscape: ref_key 不存在于检索结果 -> {key}")
            used_keys.add(key)

    for route in plan.get("routes") or []:
        family = route.get("family") or "(未命名方法族)"
        if not (route.get("idea") or "").strip():
            errors.append(f"routes/{family}: 缺少核心思想")
        if not route.get("ref_keys"):
            errors.append(f"routes/{family}: 缺少代表工作")
        for key in route.get("ref_keys") or []:
            key = (key or "").strip()
            if not key:
                continue
            if key not in records:
                errors.append(f"routes/{family}: ref_key 不存在于检索结果 -> {key}")
            used_keys.add(key)

    why_read = plan.get("why_read") or {}
    selected = [rec for tier in TIER_ORDER for rec in tiers.get(tier, [])]
    for rec in selected:
        key = rec["ref_key"]
        reason = (why_read.get(key) or "").strip()
        if not reason:
            errors.append(f"why_read: 文献缺少'为什么必读' -> {short_title(rec.get('title'), 50)}")
        elif len(reason) < 6:
            errors.append(f"why_read: 理由过于简略（{reason}） -> {short_title(rec.get('title'), 40)}")

    for key in why_read:
        if (why_read.get(key) or "").strip() and key not in records:
            errors.append(f"why_read: 引用了检索结果之外的文献 -> {key}")

    for key, note in (plan.get("latest_notes") or {}).items():
        if (note or "").strip() and key not in records:
            errors.append(f"latest_notes: 引用了检索结果之外的文献 -> {key}")

    for key in used_keys:
        if key and key not in why_read:
            # 允许引用未入选清单的检索结果，但要求其有链接
            if not (records.get(key, {}).get("doi") or records.get(key, {}).get("url")):
                errors.append(f"引用缺少可回溯链接 -> {key}")

    terms = plan.get("terms") or []
    terms = [t for t in terms if (t.get("zh") or "").strip()]
    if len(terms) > MAX_TERMS:
        errors.append(f"terms: {len(terms)} 条超过上限 {MAX_TERMS}")
    for term in terms:
        if term.get("layer") not in TERM_LAYERS:
            errors.append(f"terms/{term.get('zh')}: layer 必须是 {TERM_LAYERS} 之一")
        definition = (term.get("def") or "").strip()
        if not definition:
            errors.append(f"terms/{term.get('zh')}: 缺少解释")
        elif len(definition) > MAX_TERM_DEF:
            errors.append(f"terms/{term.get('zh')}: 解释 {len(definition)} 字，超过 {MAX_TERM_DEF} 字")

    gaps = plan.get("gaps") or []
    if len(gaps) > MAX_GAPS:
        errors.append(f"gaps: {len(gaps)} 条超过上限 {MAX_GAPS}")
    for gap in gaps:
        if not (gap.get("evidence") or "").strip():
            errors.append(f"gaps/{gap.get('gap')}: 缺少证据（禁止无依据的空白断言）")

    card = plan.get("direction_card") or {}
    if not (card.get("question") or "").strip():
        errors.append("direction_card: 研究问题为空")

    return errors


# ---------------------------------------------------------------- render


def render(plan: dict, refs: dict) -> str:
    meta = refs.get("meta", {})
    tiers = refs.get("tiers", {})
    records = refs.get("records", {})
    card = plan.get("direction_card") or {}
    queries = plan.get("queries") or {}

    lines = []
    add = lines.append

    topic = plan.get("topic") or meta.get("topic") or "研究方向"
    add(f"# {topic}：领域地图与入门路径")
    add("")
    if plan.get("audience"):
        add(f"> 面向：{plan['audience']}")
    if plan.get("time_budget"):
        add(f"> 时间预算：{plan['time_budget']}")
    sources = "、".join(
        sorted({"openalex": "OpenAlex", "arxiv": "arXiv"}.get(s.lower(), s)
               for s in {s for r in records.values() for s in (r.get("sources") or [r.get("source")])})
    )
    year_range = ""
    years = [r.get("year") for r in records.values() if r.get("year")]
    if years:
        year_range = f"{min(years)}–{max(years)}"
    add(f"> 检索范围：{sources or 'OpenAlex / arXiv'}，{year_range or '不限年份'}")
    newest = (meta.get("newest_date") or "").strip()
    if newest:
        add(f"> 数据截止：{newest}")
    scope_counts = meta.get("scope_counts") or {}
    if scope_counts:
        add(f"> 去噪：原始 {meta.get('raw_count', 0)} 篇 → 去重 {meta.get('deduped_count', 0)} 篇"
            f" → 主题核心 {scope_counts.get('core', 0)} 篇；"
            f"仅提及/相邻领域 {meta.get('mention_only_count', 0)} 条单列，不进必读")
    current_year = meta.get("current_year_count")
    if current_year:
        add(f"> 当年优先：{meta.get('year_now') or ''} 年论文 {current_year} 篇已进入候选，前沿层当年优先")
    add(f"> 生成日期：{datetime.now().strftime('%Y-%m-%d')}")
    add("")

    # 一、方向确认
    add("## 一、方向确认")
    add("")
    add("**研究问题**")
    add("")
    add(card.get("question") or "")
    add("")
    add("**范围边界**")
    add("")
    do_items = [i for i in (card.get("do") or []) if (i or "").strip()]
    dont_items = [i for i in (card.get("dont") or []) if (i or "").strip()]
    if do_items:
        add("**做什么**")
        add("")
        for item in do_items:
            add(f"- {item}")
        add("")
    if dont_items:
        add("**不做什么**")
        add("")
        for item in dont_items:
            add(f"- {item}")
        add("")
    if card.get("subfield"):
        add(f"**所属子领域**：{card['subfield']}")
        add("")

    kw_en = [k for k in (card.get("keywords_en") or []) if (k or "").strip()]
    kw_zh = [k for k in (card.get("keywords_zh") or []) if (k or "").strip()]
    if kw_en or kw_zh:
        add("**关键词簇**")
        add("")
        add("| 语言 | 关键词 |")
        add("|---|---|")
        if kw_en:
            add("| 英文 | " + esc(" / ".join(kw_en)) + " |")
        if kw_zh:
            add("| 中文 | " + esc(" / ".join(kw_zh)) + " |")
        add("")

    if any((queries.get(k) or "").strip() for k in ("core", "broad", "review")):
        add("**检索式**（可复用）")
        add("")
        add("```")
        for key in ("core", "broad", "review"):
            if (queries.get(key) or "").strip():
                add(f"{key}: {queries[key]}")
        add("```")
        add("")

    # 二、领域脉络
    add("## 二、领域脉络")
    add("")
    add("> 读法：先用第一梯队综述建立骨架，再看下面每条主线的演化。每条主线回答的是\"为什么会转向\"，不是\"哪年发生了什么\"。")
    add("")

    citation = refs.get("citation_analysis") or {}
    if citation.get("available"):
        add("### 引文关系：谁被引用最多、谁常被一起比较")
        add("")
        add("> 依据集合内 `referenced_works` 统计（覆盖 {:.0%}）。"
            "这里展示领域内被引核心与共被引对，不把它冒充严格时间线；"
            "演化顺序见下面各主线的“起点 → 跃迁 → SOTA”。".format(
                citation.get("coverage", 0)))
        add("")
        top = citation.get("top_cited") or []
        if top:
            add("| # | 文献 | 年份 | 集合内被引 |")
            add("|---|---|---|---|")
            for idx, item in enumerate(top[:10], start=1):
                add("| {} | {} | {} | {} |".format(
                    idx, esc(short_title(item.get("title"), 70)),
                    esc(item.get("year") or ""), item.get("in_set_cited", 0)))
            add("")
        co = citation.get("co_cited") or []
        if co:
            add("**共被引对**（被同一批论文同时引用，说明经常被放在一起比较）")
            add("")
            add("| 文献 A | 文献 B | 共同引用数 |")
            add("|---|---|---|")
            for pair in co[:5]:
                add("| {} | {} | {} |".format(
                    esc(short_title(pair.get("a_title"), 45)),
                    esc(short_title(pair.get("b_title"), 45)),
                    pair.get("count", 0),
                ))
            add("")

    landscape = [l for l in (plan.get("landscape") or []) if (l.get("line") or "").strip()]
    for idx, line_item in enumerate(landscape, start=1):
        add(f"### 主线 {idx}：{line_item['line']}")
        add("")
        add("| 年份 | 节点 | 为什么重要 | 代表文献 |")
        add("|---|---|---|---|")
        stages = [s for s in (line_item.get("stages") or []) if (s.get("label") or "").strip()]
        for stage in sorted(stages, key=lambda s: (s.get("year") or 0)):
            rec = records.get((stage.get("ref_key") or "").strip(), {})
            add("| {} | {} | {} | {} |".format(
                esc(stage.get("year") or ""),
                esc(stage.get("label")),
                esc(stage.get("why")),
                esc(cite(rec) if rec else "—"),
            ))
        add("")
        if line_item.get("status"):
            add(f"**这条线现在的状态**：{line_item['status']}")
            add("")

    # 三、技术路线
    routes = [r for r in (plan.get("routes") or []) if (r.get("family") or "").strip()]
    if routes:
        add("## 三、技术路线")
        add("")
        add("> 每一行都必须落到具体论文；写不出代表工作的路线不要放进表里。")
        add("")
        add("| 方法族 | 核心思想 | 代表工作 | 适用条件 | 主要局限 |")
        add("|---|---|---|---|---|")
        for route in routes:
            cite_list = []
            for key in route.get("ref_keys") or []:
                rec = records.get((key or "").strip())
                if rec:
                    cite_list.append(f"{cite(rec)} {short_title(rec.get('title'), 45)}")
            add("| {} | {} | {} | {} | {} |".format(
                esc(route.get("family")),
                esc(route.get("idea")),
                esc("；".join(cite_list) or "—"),
                esc(route.get("fits") or "—"),
                esc(route.get("limits") or "—"),
            ))
        add("")
        if plan.get("route_advice"):
            add(f"**路线选择建议**：{plan['route_advice']}")
            add("")

    # 四、必读文献
    add("## 四、必读文献")
    add("")
    total = sum(len(tiers.get(t, [])) for t in TIER_ORDER)
    weeks = max(1, round(total / 3 + 0.49))
    target = meta.get("target_count") or total
    add(f"> 编号即阅读顺序。共 {total} 篇，按每周 3 篇约需 {weeks} 周。")
    if target and target != total:
        add(f"> 配额目标 {target} 篇，实际入选 {total} 篇；"
            "缺口原因是去噪后符合条件的候选不足，未用仅提及主题的关联文献凑数。")
    elif target:
        add(f"> 配额目标 {target} 篇，实际入选 {total} 篇，数量一致。")
    add("")
    counter = 0
    why_read = plan.get("why_read") or {}
    quota_target = meta.get("quota_target") or {}
    quota_actual = meta.get("quota_actual") or {}
    for tier in TIER_ORDER:
        items = tiers.get(tier, [])
        if not items:
            continue
        title, hint = TIER_TITLE[tier]
        tgt = quota_target.get(tier)
        act = quota_actual.get(tier, len(items))
        if tgt is not None and tgt != act:
            add(f"### {title}（目标 {tgt} / 实际 {act}）")
        else:
            add(f"### {title}（{len(items)} 篇）")
        add("")
        if hint:
            add(f"> {hint}")
            add("")
        add("| # | 文献 | 年份 | 为什么必读 |")
        add("|---|---|---|---|")
        for rec in items:
            counter += 1
            link = ref_link(rec)
            label = f"[{short_title(rec.get('title'), 80)}]({link})" if link else short_title(rec.get("title"), 80)
            add("| {} | {} | {} | {} |".format(
                counter, esc(label), esc(rec.get("year") or ""),
                esc(why_read.get(rec["ref_key"]) or ""),
            ))
        add("")

    if counter >= 3:
        first_three = []
        idx = 0
        for tier in TIER_ORDER:
            for rec in tiers.get(tier, []):
                idx += 1
                if idx <= 3:
                    first_three.append(str(idx))
        add(f"**如果只有一周**：读 {'、'.join(first_three)} 号。")
        add("")

    # 五、近期进展
    latest = refs.get("latest") or []
    year_now = meta.get("year_now") or datetime.now().year
    add(f"## 五、近期进展（近 6 个月，{year_now} 年优先）")
    add("")
    if latest:
        add("> 这一节不看被引数——刚上线的论文引用必然是 0，按引用排序永远进不了必读清单。"
            f"{year_now} 年论文排在近月论文之前。")
        add("")
        add("| # | 日期 | 文献 | 看点 |")
        add("|---|---|---|---|")
        latest_notes = plan.get("latest_notes") or {}
        for idx, rec in enumerate(latest, start=1):
            link = ref_link(rec)
            label = f"[{short_title(rec.get('title'), 80)}]({link})" if link else short_title(rec.get("title"), 80)
            flag = " ★当年" if (rec.get("year") or 0) >= year_now else ""
            add("| {} | {}{} | {} | {} |".format(
                idx, esc(rec.get("pub_date") or ""), flag, esc(label),
                esc(latest_notes.get(rec["ref_key"]) or "—"),
            ))
        add("")
    else:
        add(f"> 本次未命中近 6 个月的新文献。可能是检索式过窄，或数据源收录延迟，"
            f"建议补充 arXiv 的 submittedDate 检索后重跑。")
        add("")

    # 六、术语表
    terms = [t for t in (plan.get("terms") or []) if (t.get("zh") or "").strip()]
    if terms:
        add("## 六、术语表")
        add("")
        add("> 术语种子优先取自综述/教程；OpenAlex/arXiv 只给摘要，"
            "未能读取 Introduction 与 Related Work 全文，来源已如实标注。")
        add("")
        add("| 术语 | 英文 | 层 | 来源 | 一句话解释 |")
        add("|---|---|---|---|---|")
        for term in terms:
            add("| {} | {} | {} | {} | {} |".format(
                esc(term.get("zh")), esc(term.get("en")),
                esc(term.get("layer")), esc(term.get("source") or "综述摘要"),
                esc(term.get("def")),
            ))
        add("")

    # 七、疑似研究空白
    gaps = plan.get("gaps") or []
    add("## 七、疑似研究空白（未验证）")
    add("")
    if gaps:
        add("> 本节所有判断都是\"基于检索结果的缺席推断\"，未经查证。用作选题灵感，不要直接当结论。")
        add("")
        add("| # | 疑似空白 | 依据 | 为什么可能还没人做 | 需要什么条件 | 风险 |")
        add("|---|---|---|---|---|---|")
        for idx, gap in enumerate(gaps, start=1):
            add("| {} | {} | {} | {} | {} | {} |".format(
                idx, esc(gap.get("gap")), esc(gap.get("evidence")),
                esc(gap.get("why_not")), esc(gap.get("needs")), esc(gap.get("risk")),
            ))
        add("")
    else:
        add("> 本版本未对空白做证据验证。以下为机器可识别的\"信号\"，仅供进一步查证。")
        add("")
        signals = conflict_signals(refs)
        if signals:
            add("| # | 信号强度 | 文献 | 年份 | 可能的争议点 |")
            add("|---|---|---|---|---|")
            for idx, sig in enumerate(signals, start=1):
                link = ref_link(sig)
                label = (f"[{short_title(sig.get('title'), 76)}]({link})"
                         if link else short_title(sig.get("title"), 76))
                add("| {} | {} | {} | {} | {} |".format(
                    idx, esc(sig.get("score")), esc(label), esc(sig.get("year") or ""),
                    esc("表述与同主题其他文献存在冲突，需核对原文"),
                ))
            add("")
            add("_这些只是检索信号，不构成研究空白结论；立项前需逐篇核对原文，并确认无人已做。_")
        else:
            add("_本次检索未命中冲突信号，建议用关键词组合到 CNKI / Google Scholar 人工复核。_")
        add("")

    # 七、覆盖边界
    add("## 八、覆盖边界与下一步")
    add("")
    add("**本次覆盖**")
    add("")
    add(f"- 数据源：{sources or 'OpenAlex / arXiv'}")
    add(f"- 年份：{year_range or '不限'}")
    add(f"- 文献总量：检索 {meta.get('raw_count', 0)} 篇，去重后 {meta.get('deduped_count', 0)} 篇，"
        f"入选清单 {total} 篇")
    verify_stats = refs.get("_verify_stats") or {}
    if verify_stats.get("applied"):
        add(f"- DOI 回验：通过 {verify_stats.get('verified', 0)} 篇，"
            f"部分一致 {verify_stats.get('partial', 0)} 篇，"
            f"未通过剔除 {verify_stats.get('blocked', 0)} 篇")
    else:
        add("- DOI 回验：本次未执行（仅做过格式校验，未向 Crossref 核对）")
    scope_counts = meta.get("scope_counts") or {}
    if scope_counts:
        add(f"- 去噪分流：主题核心 {scope_counts.get('core', 0)} 篇；"
            f"仅提及/相邻领域 {meta.get('mention_only_count', 0)} 条单列在附录；"
            f"同名词记录剔除 {meta.get('homonym_count', 0)} 条")
    quota_target = meta.get("quota_target") or {}
    if quota_target:
        detail = "，".join(
            f"{TIER_TITLE[k][0].split('：')[-1]} {quota_target.get(k, 0)}/{sum(len(tiers.get(k, [])) for k in [k])}"
            for k in TIER_ORDER)
        add(f"- 配额一致性：目标 {meta.get('target_count', total)} 篇 / 实际 {total} 篇；"
            f"各层目标/实际 = {detail}")
    add(f"- 当年优先：{meta.get('year_now') or ''} 年论文 {meta.get('current_year_count', 0)} 篇；"
        f"近 12 个月 {meta.get('recent_priority_count', 0)} 篇")
    citation_meta = refs.get("citation_analysis") or {}
    if citation_meta.get("available"):
        add(f"- 引文关系：可用（集合内边 {citation_meta.get('edge_count', 0)} 条，"
            f"覆盖 {citation_meta.get('coverage', 0):.0%}），用于识别被引核心与共被引；"
            "主线演化按引文关系与年份综合判断")
    else:
        add("- 引文关系：不足，演化顺序退回按年份；需在 OpenAlex 补齐 referenced_works 后重跑")
    add("- 术语来源：综述/教程摘要；未读取 Introduction 与 Related Work 全文，"
        "这是数据源限制，不是术语已经过全文核验")
    if plan.get("boundary_notes"):
        add(f"- 覆盖限制：{plan['boundary_notes']}")
    else:
        add("- 覆盖限制：未覆盖中文文献（CNKI / 万方），中文综述建议自行补充")
    add("")

    steps = [s for s in (plan.get("next_steps") or []) if (s or "").strip()]
    if steps:
        add("**建议的下一步**")
        add("")
        for idx, step in enumerate(steps, start=1):
            add(f"{idx}. {step}")
        add("")

    warnings = refs.get("warnings") or []
    if warnings:
        add("**本次检索的提示**")
        add("")
        for warning in warnings:
            add(f"- {warning}")
        add("")

    peripheral = refs.get("peripheral") or []
    if peripheral:
        add("## 附录 A、仅提及/相邻领域文献（不入必读）")
        add("")
        add("> 这些论文不是真正做本主题：有的只在摘要里提了一句，"
            "有的只命中相邻概念。列出来是为了让你知道边界在哪，不要把它们当核心文献读。")
        add("")
        add("| # | 文献 | 年份 | 判定 | 依据 |")
        add("|---|---|---|---|---|")
        for idx, rec in enumerate(peripheral, start=1):
            link = ref_link(rec)
            label = f"[{short_title(rec.get('title'), 75)}]({link})" if link else short_title(rec.get("title"), 75)
            verdict = "仅提及" if rec.get("scope") == "mention" else "相邻领域"
            add("| {} | {} | {} | {} | {} |".format(
                idx, esc(label), esc(rec.get("year") or ""),
                verdict, esc(rec.get("reason") or ""),
            ))
        add("")

    add("---")
    add("*本报告由文献检索结果自动生成，文献元数据均来自 OpenAlex / arXiv。"
        "所有文献条目可回溯，猜测性判断已标注。*")
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------- bibtex


def to_bibtex(refs: dict) -> str:
    tiers = refs.get("tiers", {})
    entries, seen = [], set()
    for tier in TIER_ORDER:
        for rec in tiers.get(tier, []):
            key = rec["ref_key"]
            if key in seen:
                continue
            seen.add(key)
            authors = rec.get("authors") or []
            surname = (authors[0].split()[-1] if authors and " " in authors[0] else (authors[0] if authors else "anon"))
            first_word = re.sub(r"[^A-Za-z0-9]", "", (rec.get("title") or "untitled").split()[0] if (rec.get("title") or "").strip() else "untitled")
            bib_key = f"{surname}{rec.get('year') or 'nd'}{first_word}"[:60]
            entry_type = "article" if rec.get("type") not in ("preprint", "book") else "misc"
            fields = [
                f"  title = {{{rec.get('title') or ''}}}",
                f"  author = {{{' and '.join(authors) if authors else 'Unknown'}}}",
                f"  year = {{{rec.get('year') or ''}}}",
            ]
            if rec.get("venue"):
                field_name = "journal" if entry_type == "article" else "howpublished"
                fields.append(f"  {field_name} = {{{rec['venue']}}}")
            if rec.get("doi"):
                fields.append(f"  doi = {{{rec['doi']}}}")
            if rec.get("url"):
                fields.append(f"  url = {{{rec['url']}}}")
            entries.append(f"@{entry_type}{{{bib_key},\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries) + "\n"


# ---------------------------------------------------------------- main


def apply_verification(refs: dict) -> tuple[dict, list, dict]:
    """按 DOI 回验结果剔除未通过的文献。

    规则：verified 放行；partial 放行但标记；其余（missing / not_found /
    mismatch / error / no_doi）一律不进报告。
    宁可少几篇，也不要让一篇查不到的文献混进必读清单。
    """
    records = refs.get("records") or {}
    blocked: list = []
    stats = {"applied": True, "verified": 0, "partial": 0, "blocked": 0}

    def status_of(rec: dict) -> str:
        key = rec.get("ref_key")
        holder = records.get(key) or rec
        info = holder.get("doi_verification") or rec.get("doi_verification")
        if not isinstance(info, dict):
            return "missing"
        return info.get("status") or "missing"

    for tier, items in (refs.get("tiers") or {}).items():
        kept = []
        for rec in items:
            status = status_of(rec)
            if status == "verified":
                stats["verified"] += 1
                kept.append(rec)
            elif status == "partial":
                stats["partial"] += 1
                rec["doi_verify_note"] = "部分字段与 Crossref 不一致"
                kept.append(rec)
            else:
                stats["blocked"] += 1
                blocked.append({
                    "ref_key": rec.get("ref_key"),
                    "title": rec.get("title"),
                    "status": status,
                })
        refs["tiers"][tier] = kept

    for lane in ("latest", "peripheral"):
        items = refs.get(lane) or []
        if items:
            refs[lane] = [r for r in items if status_of(r) in ("verified", "partial")]

    refs["_verify_stats"] = stats
    return refs, blocked, stats


def conflict_signals(refs: dict, limit: int = 6) -> list:
    """从记录的 conflict_signal 字段抽出“结论可能冲突”的候选。

    这是第七节在没有人工填写 gaps 时的兑底数据源：
    机器只能给出“这两篇可能互相对着干”的线索，不能替你下结论。
    """
    records = refs.get("records") or {}
    hits = []
    for key, rec in records.items():
        score = rec.get("conflict_signal") or 0
        if score > 0 and (rec.get("doi") or rec.get("url")):
            hits.append((score, rec))
    hits.sort(key=lambda kv: -kv[0])
    return [
        {
            "score": score,
            "title": rec.get("title"),
            "year": rec.get("year"),
            "pub_date": rec.get("pub_date"),
            "ref_key": rec.get("ref_key"),
            "venue": rec.get("venue"),
            "doi": rec.get("doi"),
            "url": rec.get("url"),
        }
        for score, rec in hits[:limit]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成计划骨架或渲染报告")
    parser.add_argument("--refs", required=True, help="rank_refs.py 输出的 JSON")
    parser.add_argument("--init-plan", help="生成计划骨架到该路径")
    parser.add_argument("--plan", help="填写完成的 plan.json")
    parser.add_argument("--out", help="输出 Markdown 路径")
    parser.add_argument("--bib", help="输出 BibTeX 路径")
    parser.add_argument("--verify", action="store_true",
                        help="强制要求 DOI 回验结果；缺失则拒绝渲染")
    parser.add_argument("--no-verify", action="store_true",
                        help="跳过回验门禁（仅在数据源无法访问 Crossref 时使用）")
    args = parser.parse_args()

    refs = load_json(Path(args.refs))

    if args.init_plan:
        return init_plan(refs, Path(args.init_plan))

    if not args.plan or not args.out:
        parser.error("渲染模式需要同时提供 --plan 与 --out")

    plan = load_json(Path(args.plan))
    errors = validate(plan, refs)
    if errors:
        print("校验未通过，已中止渲染：", file=sys.stderr)
        for err in errors:
            print("  - " + err, file=sys.stderr)
        print(f"\n共 {len(errors)} 处问题。修正 plan.json 后重试；"
              f"不要为了通过校验而删规则。", file=sys.stderr)
        return 1

    # DOI 回验门禁：有回验结果就自动生效，显式 --verify 则强制要求
    if not args.no_verify:
        has_verify = any(
            (rec or {}).get("doi_verification")
            for rec in (refs.get("records") or {}).values()
        )
        if args.verify and not has_verify:
            print("未找到 DOI 回验结果。请先运行：", file=sys.stderr)
            print("  python scripts/verify_doi.py --in <refs_ranked.json> --out <refs_ranked.json>",
                  file=sys.stderr)
            return 1
        if has_verify:
            refs, blocked, stats = apply_verification(refs)
            if blocked:
                print(f"DOI 回验：剔除 {len(blocked)} 篇未通过文献")
                for item in blocked:
                    print(f"  - [{item['status']}] {item['title'][:60] if item['title'] else item['ref_key']}")
            else:
                print(f"DOI 回验：{stats['verified']} 篇通过，无剔除")

    report = render(plan, refs)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8", newline="\n")
    print(f"已生成报告: {out_path}")

    if args.bib:
        bib_path = Path(args.bib)
        bib_path.parent.mkdir(parents=True, exist_ok=True)
        bib_path.write_text(to_bibtex(refs), encoding="utf-8", newline="\n")
        print(f"已生成 BibTeX: {bib_path}")

    total = sum(len(refs.get("tiers", {}).get(t, [])) for t in TIER_ORDER)
    print(f"入选文献 {total} 篇，报告约 {len(report.splitlines())} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())