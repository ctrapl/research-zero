#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Research Landscape V3 - 开题素材包导出。

输入：填好的 plan.json + 已回验的 refs JSON
输出：开题素材/ 目录（速览、背景、现状、路线、创新点、答辩预演、bib、README）

设计原则：
- 只引用 records 里真实存在且未被回验剔除的文献
- 推测性内容固定标注【待验证】，缺失信息固定留【待补：...】
- 不伪造导师姓名、单位、基金号、伦理批件号
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_report import (  # noqa: E402
    TIER_ORDER,
    TIER_TITLE,
    cite,
    ref_link,
    short_title,
    to_bibtex,
)

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

EVIDENCE_LABELS = {
    "guideline": "指南", "consensus": "专家共识", "meta_analysis": "Meta 分析",
    "systematic_review": "系统综述", "rct": "RCT", "clinical_trial": "临床试验",
    "cohort": "队列研究", "case_control": "病例对照", "review": "综述",
    "other": "未标注",
}

PROFILE_HINT = {
    "medicine": "医学模板：数据来源、伦理批件、样本量估算必须写清；指南/共识与原始研究分开陈述。",
    "cs": "CS 模板：写清算力预算、代码基线、与 SOTA 的对比协议；预印本结论标注未经同行评审。",
    "general": "通用模板：写清数据可得性与实验条件；没有条件验证的结论标注为待验证。",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def txt(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def ref_label(rec: dict) -> str:
    link = ref_link(rec)
    title = short_title(rec.get("title"), 90)
    return f"[{title}]({link})" if link else title


def cite_zh(rec: dict) -> str:
    return "（" + cite(rec).strip("()") + "）"


def fmt_ref(rec: dict) -> str:
    return f"{ref_label(rec)} {cite_zh(rec)}"


def weeks_from_budget(text: str) -> int:
    """把用户的时间预算折算成周数。

    支持范围（3–6 个月、1–2 年、3–5 年）与单值（半年、一年、3 个月），
    取范围中值；无法识别时按 24 周并提示复核。
    """
    text = (text or "").strip()
    if not text:
        return 24

    month_range = re.search(r"(\d+)\s*[-–~至]\s*(\d+)\s*个?月", text)
    if month_range:
        months = (int(month_range.group(1)) + int(month_range.group(2))) / 2
        return max(1, round(months * 4.345))

    year_range = re.search(r"(\d+)\s*[-–~至]\s*(\d+)\s*年", text)
    if year_range:
        years = (int(year_range.group(1)) + int(year_range.group(2))) / 2
        return max(1, round(years * 52))

    single_month = re.search(r"(\d+)\s*个?月", text)
    if single_month:
        return max(1, round(int(single_month.group(1)) * 4.345))

    single_year = re.search(r"(\d+)\s*年", text)
    if single_year:
        return max(1, round(int(single_year.group(1)) * 52))

    if "半年" in text:
        return 26
    if "一年" in text or "1 年" in text:
        return 52
    if "三年" in text:
        return 156
    return 24


def plan_records(plan: dict, refs: dict) -> tuple[dict, list, list]:
    """返回可用 records、提示信息与硬错误。

    引用集合分两类，口径与 build_report.validate 对齐：

    - **叙述引用**（landscape / routes / 有内容的 latest_notes）：缺链接或被回验
      剔除一律硬错误，必须中止导出。
    - **why_read**：init_plan 会为回验前的全部初选文献预填，回验剔除后残留的
      key 属于正常现象，静默忽略；只有"检索结果中根本不存在"才报错。
    """
    records = refs.get("records") or {}
    usable = {}
    for key, rec in records.items():
        if is_blocked(refs, rec):
            continue
        if not (rec.get("doi") or rec.get("url")):
            continue
        usable[key] = rec

    # 硬引用：validate 会检查 blocked 的字段（主线节点、路线代表工作）
    narrative = set()
    for line in plan.get("landscape") or []:
        for stage in line.get("stages") or []:
            narrative.add((stage.get("ref_key") or "").strip())
    for route in plan.get("routes") or []:
        narrative.update(k for k in (route.get("ref_keys") or []) if k)
    narrative.discard("")

    # 软引用：validate 只检查"是否存在于检索结果"，被剔除的残留会被渲染器自动跳过
    soft = {k for k, v in (plan.get("why_read") or {}).items() if txt(v)}
    soft.update(k for k, v in (plan.get("latest_notes") or {}).items() if txt(v))
    soft -= narrative

    problems = []
    hard = []

    def add(message: str) -> None:
        problems.append(message)
        hard.append(message)

    for key in sorted(narrative):
        if key in usable:
            continue
        rec = records.get(key)
        if rec is None:
            add(f"plan 叙述引用了检索结果中不存在的文献：{key}")
        elif is_blocked(refs, rec):
            add(f"plan 叙述引用了已被 DOI 回验剔除的文献：{key}")
        else:
            add(f"plan 叙述引用了缺少可回溯链接的文献：{key}")

    for key in sorted(soft):
        if key in usable:
            continue
        rec = records.get(key)
        if rec is None:
            add(f"why_read / latest_notes 引用了检索结果中不存在的文献：{key}")
        # 被回验剔除的软引用残留：静默忽略，报告渲染时本就不会出现

    return usable, problems, hard


ALLOWED_VERIFY = {"verified", "partial", "link_only"}


def is_blocked(refs: dict, rec: dict) -> bool:
    """回验未通过、或已被 build_report 标记剔除的记录。"""
    records = refs.get("records") or {}
    if rec.get("_blocked"):
        return True
    holder = records.get(rec.get("ref_key"), rec)
    info = holder.get("doi_verification") or rec.get("doi_verification")
    if isinstance(info, dict):
        status = info.get("status")
        if status and status not in ALLOWED_VERIFY:
            return True
    return False


def get_tier_records(refs: dict) -> list:
    tiers = refs.get("tiers") or {}
    out = []
    for tier in TIER_ORDER:
        for rec in tiers.get(tier, []):
            if is_blocked(refs, rec):
                continue
            out.append((tier, rec))
    return out


def stage_ref(records: dict, key: str) -> dict:
    return records.get((key or "").strip()) or {}


def build_overview(plan: dict, refs: dict, records: dict, usable) -> str:
    card = plan.get("direction_card") or {}
    meta = refs.get("meta") or {}
    tiers = get_tier_records(refs)
    lines = []
    add = lines.append

    add(f"# 开题速览：{txt(card.get('question'), txt(plan.get('topic'), '研究方向'))}")
    add("")
    add(f"> 面向：{txt(plan.get('audience'), '未填写')} ｜ "
        f"时间预算：{txt(plan.get('time_budget'), '未填写')} ｜ "
        f"生成：{datetime.now().strftime('%Y-%m-%d')}")
    add(f"> 文献依据：检索 {meta.get('raw_count', 0)} 篇 → 入选 {len(get_tier_records(refs))} 篇；"
        f"数据截止 {txt(meta.get('newest_date'), '未记录')}")
    add("")

    add("## 一句话")
    add("")
    add(txt(card.get("question"), "【待补：用一句话说明在什么场景下、解决什么问题、用什么思路】"))
    add("")

    add("## 为什么是现在")
    add("")
    reasons = []
    for line in plan.get("landscape") or []:
        stages = line.get("stages") or []
        if not stages:
            continue
        last = stages[-1]
        rec = stage_ref(records, last.get("ref_key"))
        if rec:
            reasons.append(f"{txt(line.get('line'), '主线')}：{txt(last.get('why'), '当前状态')}"
                           f"{cite_zh(rec)}")
        if len(reasons) >= 3:
            break
    if len(reasons) < 3:
        for key, reason in (plan.get("why_read") or {}).items():
            rec = records.get(key)
            if rec and txt(reason):
                reasons.append(f"{short_title(rec.get('title'), 60)}{cite_zh(rec)}：{txt(reason)}")
            if len(reasons) >= 3:
                break
    if reasons:
        for idx, reason in enumerate(reasons, start=1):
            add(f"{idx}. {reason}")
    else:
        add("1. 【待补：三条「为什么现在做」的依据，每条挂一篇文献】")
    add("")

    add("## 打算怎么做")
    add("")
    routes = plan.get("routes") or []
    if routes:
        first = routes[0]
        add(f"主线方法：{txt(first.get('family'), '【待填方法族】')}——"
            f"{txt(first.get('idea'), '【待填核心思想】')}")
        if plan.get("route_advice"):
            add("")
            add(txt(plan["route_advice"]))
    else:
        add("【待补：选 1–2 个方法族，说明为什么选它而不是竞争路线】")
    add("")

    add("## 最小可行结果")
    add("")
    weeks = weeks_from_budget(txt(plan.get("time_budget"), ""))
    if weeks <= 12:
        add("- 在现有数据上复现 1 个强基线，跑通评价流程")
        add("- 完成 1 个可解释的改进点，并在 ≥2 个指标上对比")
        add("- 产出可写进毕设/中期报告的结果表与失败分析")
    elif weeks <= 30:
        add("- 复现 2 个基线，搭建完整的训练/评测管线")
        add("- 完成 1–2 个创新点，做消融与鲁棒性分析")
        add("- 产出可投稿的初稿框架")
    else:
        add("- 建立完整的问题形式化与理论/系统框架")
        add("- 完成 2–3 个递进工作，含充分对比与消融")
        add("- 形成可持续的研究线，而非单点实验")
    add("")

    add("## 最大风险与退路")
    add("")
    gaps = plan.get("gaps") or []
    if gaps:
        add(f"- 风险：{txt(gaps[0].get('risk'), '【待补：最大风险】')}")
        add("- 退路：【待补：方法不收敛/数据不可得时的降级方案，不要写「再做实验看看」】")
    else:
        add("- 风险：【待补：数据不可得 / 方法不收敛 / 撞车】")
        add("- 退路：【待补：降级方案，不能写「再做实验看看」】")
    add("")

    add("## 待补清单")
    add("")
    pending = []
    if gaps:
        for gap in gaps:
            pending.append(f"验证空白候选：{txt(gap.get('gap'), '')}（{txt(gap.get('evidence'), '无依据')}）")
    for step in plan.get("next_steps") or []:
        if txt(step):
            pending.append(txt(step))
    if not pending:
        pending.append("【待补：至少列 3 条开题前必须完成的事】")
    for item in pending:
        add(f"- [ ] {item}")
    add("")
    add("---")
    add("*本页由检索结果与 plan.json 自动生成；【待验证】【待填】标记必须处理完再交给导师。*")
    return "\n".join(lines)


def build_background(plan: dict, refs: dict, records: dict) -> str:
    card = plan.get("direction_card") or {}
    lines = []
    add = lines.append

    add("# 一、研究背景与意义")
    add("")
    add("## 1.1 领域现状")
    add("")
    for line in plan.get("landscape") or []:
        stages = line.get("stages") or []
        if not stages:
            continue
        rec = stage_ref(records, stages[-1].get("ref_key"))
        add(f"- **{txt(line.get('line'), '主线')}**：{txt(line.get('status'), '当前状态待补')}"
            + (cite_zh(rec) if rec else ""))
    if not (plan.get("landscape") or []):
        add("【待填：3–5 条主线的当前状态，每条挂文献】")
    add("")

    add("## 1.2 尚未解决的问题")
    add("")
    gaps = plan.get("gaps") or []
    if gaps:
        for gap in gaps[:3]:
            add(f"- **{txt(gap.get('gap'), '待填')}**（【待验证】{txt(gap.get('evidence'), '无依据')}）")
    else:
        add("【待填：这个领域还没解决什么问题；必须带检索依据，不能写「尚属空白」】")
    add("")

    add("## 1.3 本研究的切入点与意义")
    add("")
    add(txt(card.get("question"), "【待填研究问题】"))
    add("")
    dont = [d for d in (card.get("dont") or []) if txt(d)]
    if dont:
        add("本研究明确不覆盖：")
        add("")
        for item in dont:
            add(f"- {item}")
        add("")
    add("> 引用说明：本节引用均来自本次检索，编号与《科研方向地图》第四节一致。")
    add("")
    return "\n".join(lines)


def build_status(plan: dict, refs: dict, records: dict) -> str:
    meta = refs.get("meta") or {}
    profile = meta.get("profile") or "general"
    lines = []
    add = lines.append

    add("# 二、国内外研究现状")
    add("")
    add("> 组织方式：按技术主线而非年份流水账。每条主线写清起点、转向原因、当前 SOTA 和未解决点。")
    add("")

    landscape = [l for l in (plan.get("landscape") or []) if txt(l.get("line"))]
    if not landscape:
        add("【待填：plan.landscape 为空，先补 3–5 条主线】")
        add("")
    for idx, line in enumerate(landscape, start=1):
        add(f"## 2.{idx} {txt(line.get('line'))}")
        add("")
        stages = line.get("stages") or []
        if not stages:
            add("- 【待补：起点 → 跃迁 → SOTA 三个节点】")
        for stage in stages:
            rec = stage_ref(records, stage.get("ref_key"))
            ref_text = cite_zh(rec) if rec else ""
            add(f"- **{txt(stage.get('label'), '节点')}**（{txt(stage.get('year'), '年份待补')}）："
                f"{txt(stage.get('why'), '为什么重要待补')}{ref_text}")
        add("")
        if txt(line.get("status")):
            add(f"**这条线现在的状态**：{txt(line.get('status'))}")
            add("")

    add("## 2.9 小结")
    add("")
    routes = plan.get("routes") or []
    if routes:
        families = "、".join(txt(r.get("family"), "方法族") for r in routes if txt(r.get("family")))
        add(f"当前主流方法族包括：{families}。")
        add("")
        add("【待补：这个领域的共识是什么、分歧在哪、你的切入点在分歧的哪一侧】")
    else:
        add("【待补：方法族与分歧】")
    add("")
    add("> 国别说明：本次检索未区分国内外来源；如需国内外对比，请用中文关键词补查 CNKI / 万方。")
    add("")

    tiers = get_tier_records(refs)
    if tiers:
        add("## 附：本报告采用的必读文献")
        add("")
        why_read = plan.get("why_read") or {}
        counter = 0
        for tier in TIER_ORDER:
            items = [(t, r) for t, r in tiers if t == tier]
            if not items:
                continue
            title, _hint = TIER_TITLE[tier]
            add(f"**{title}**")
            add("")
            for _tier, rec in items:
                counter += 1
                extra = ""
                if profile == "medicine" and rec.get("evidence_level"):
                    extra = f"（{EVIDENCE_LABELS.get(rec.get('evidence_level'), '未标注')}）"
                add(f"{counter}. {fmt_ref(rec)}{extra}——{txt(why_read.get(rec.get('ref_key')), '理由待补')}")
            add("")
    return "\n".join(lines)


def build_method(plan: dict, refs: dict, records: dict) -> str:
    meta = refs.get("meta") or {}
    profile = meta.get("profile") or "general"
    lines = []
    add = lines.append

    add("# 三、研究内容与技术路线")
    add("")
    add("## 3.1 研究内容")
    add("")
    routes = plan.get("routes") or []
    if not routes:
        add("【待填：plan.routes 为空】")
        add("")
    for idx, route in enumerate(routes, start=1):
        add(f"### 课题{idx}：{txt(route.get('family'), '方法族待填')}")
        add("")
        add(f"- 要解决的问题：{txt(route.get('idea'), '【待补】')}")
        add(f"- 适用条件：{txt(route.get('fits'), '【待补：数据/场景/样本量要求】')}")
        add(f"- 主要局限：{txt(route.get('limits'), '【待补】')}")
        refs_here = [records[k] for k in (route.get("ref_keys") or []) if k in records]
        if refs_here:
            add("- 代表工作：" + "；".join(fmt_ref(r) for r in refs_here))
        add("- 预期输出：【待补：可验收的交付物，不是「一个模型」】")
        add("")

    add("## 3.2 技术路线")
    add("")
    add("| 阶段 | 输入 | 方法 | 输出 | 验收标准 |")
    add("|---|---|---|---|---|")
    add("| P0 数据与基线 | 【待补：数据来源/规模】 | 复现 1–2 个强基线 | 可复现管线 | 基线指标落在文献报告区间内 |")
    if routes:
        add(f"| P1 方法实现 | 基线代码 + 【待补】 | {txt(routes[0].get('family'), '方法族')} | 改进版原型 | 在 ≥2 个指标上优于基线 |")
        if len(routes) > 1:
            add(f"| P2 对照路线 | 同一数据划分 | {txt(routes[1].get('family'), '方法族')} | 对照结果 | 公平比较协议可复现 |")
    else:
        add("| P1 方法实现 | 【待补】 | 【待补】 | 原型 | 【待补】 |")
    add("| P3 分析与写作 | 全部实验 | 消融/鲁棒性/失败分析 | 结论与初稿 | 每条结论都有实验支撑 |")
    add("")
    add(f"> 学科提示：{PROFILE_HINT.get(profile, PROFILE_HINT['general'])}")
    add("")

    add("## 3.3 阶段计划")
    add("")
    weeks = weeks_from_budget(txt(plan.get("time_budget"), ""))
    split = [max(1, round(weeks * ratio)) for ratio in (0.2, 0.35, 0.3, 0.15)]
    names = ["文献与数据准备", "基线复现", "方法实现与实验", "分析与写作"]
    add(f"按 {txt(plan.get('time_budget'), '未填写')} 折算约 {weeks} 周，用户需按实际开题节点复核。")
    add("")
    add("| 周次 | 任务 | 交付物 | 失败退路 |")
    add("|---|---|---|---|")
    start = 1
    for name, length in zip(names, split):
        end = start + length - 1
        add(f"| W{start}–W{end} | {name} | 【待补】 | 【待补】 |")
        start = end + 1
    add("")
    return "\n".join(lines)


def build_innovation(plan: dict, refs: dict, records: dict) -> str:
    lines = []
    add = lines.append
    add("# 四、创新点候选与风险")
    add("")
    add("> 以下全部是【待验证】候选，不是已成立的创新点。每条都必须做撞车检索后才能写进开题报告。")
    add("")
    gaps = plan.get("gaps") or []
    if not gaps:
        add("【待填：plan.gaps 为空。不要凭空写创新点；先回报告第七节选 2–3 条带证据的候选】")
        add("")
    for idx, gap in enumerate(gaps, start=1):
        add(f"## 候选{idx}：{txt(gap.get('gap'), '待填')}")
        add("")
        add(f"- **表述**：{txt(gap.get('gap'), '【待补】')}")
        add(f"- **支撑证据（【待验证】）**：{txt(gap.get('evidence'), '【待补：本次检索范围 + 未见什么】')}")
        add(f"- **为什么可能还没人做**：{txt(gap.get('why_not'), '【待补】')}")
        add(f"- **撞车风险**：{txt(gap.get('risk'), '【待补：最像的 2–3 篇工作】')}")
        add(f"- **验证方式**：{txt(gap.get('needs'), '【待补：需要什么条件才能确认】')}")
        add("")

    add("## 创新点的两种合法来源")
    add("")
    add("1. **空白候选**：本次检索范围内的缺席信号，必须附检索式与范围。")
    add("2. **方法迁移**：A 领域方法用于 B 领域问题，且两边都有文献支撑（需给出两侧代表工作）。")
    add("")
    add("其余写法（「首次提出」「填补空白」「国际领先」）没有证据支撑，不要出现在开题材料里。")
    add("")
    return "\n".join(lines)


def build_defense(plan: dict, refs: dict, records: dict) -> str:
    card = plan.get("direction_card") or {}
    meta = refs.get("meta") or {}
    profile = meta.get("profile") or "general"
    tiers = get_tier_records(refs)
    competitive = [r for t, r in tiers if t == "competitive"]
    gaps = plan.get("gaps") or []
    routes = plan.get("routes") or []
    terms = [t for t in (plan.get("terms") or []) if txt(t.get("zh"))]

    lines = []
    add = lines.append
    add("# 五、答辩预演（10 问）")
    add("")
    add("> 答案只用报告里已有的证据；没有证据的写【待补】，不要用漂亮话糊过去。")
    add("")

    add("### Q1 为什么选这个题？")
    add("")
    add(txt(card.get("question"), "【待补：一句话研究问题】"))
    add("")
    if plan.get("landscape"):
        first = plan["landscape"][0]
        add(f"依据：{txt(first.get('status'), '【待补主线状态】')}")
        add("")

    add("### Q2 这个方向已经有人做了，你的区别在哪？")
    add("")
    if gaps:
        add(f"候选差异点：{txt(gaps[0].get('gap'), '')}（【待验证】{txt(gaps[0].get('evidence'), '')}）")
    else:
        add("【待补：必须能说出与最接近的 2 篇工作的差异，否则会被认为重复】")
    add("")

    add("### Q3 为什么不用 XX 方法？")
    add("")
    if routes:
        add(f"选择 {txt(routes[0].get('family'), '方法族')} 的理由：{txt(routes[0].get('fits'), '【待补适用条件】')}")
        if len(routes) > 1:
            add(f"未选 {txt(routes[1].get('family'), '另一路线')} 的原因：{txt(routes[1].get('limits'), '【待补局限】')}")
    else:
        add("【待补：路线选择理由与对照方案】")
    add("")

    add("### Q4 数据从哪来？伦理/授权怎么办？")
    add("")
    if profile == "medicine":
        add("- 数据： 【待填：来源医院/公开队列/数据库，样本量与纳排标准】")
        add("- 伦理：【待填：伦理批件号与知情同意方式】")
        add("- 若用公开数据：说明数据使用协议与去标识化处理")
    else:
        add("- 数据：【待填：来源、规模、许可协议】")
        add("- 若用公开 benchmark：写清版本与划分，避免与基线不可比")
    add("")

    add("### Q5 你的创新点是什么？")
    add("")
    if gaps:
        for gap in gaps[:2]:
            add(f"- {txt(gap.get('gap'), '')}（【待验证】）")
    else:
        add("【待补：创新点必须来自空白候选或方法迁移，且附证据】")
    add("")

    add("### Q6 工作量够不够？时间怎么排？")
    add("")
    weeks = weeks_from_budget(txt(plan.get("time_budget"), ""))
    add(f"按 {txt(plan.get('time_budget'), '未填写')} 折算约 {weeks} 周；阶段计划见 03_研究内容与技术路线.md。")
    add("")
    add("【待补：每个阶段的里程碑与验收物，不要只说「按计划推进」】")
    add("")

    add("### Q7 评价指标是什么？")
    add("")
    metrics = [t for t in terms if t.get("layer") == "指标"]
    if metrics:
        add("本次检索到的指标类术语：")
        add("")
        for term in metrics[:6]:
            add(f"- {txt(term.get('zh'))}（{txt(term.get('en'))}）：{txt(term.get('def'))}")
    else:
        add("【待补：主指标、辅指标、统计检验方式】")
    add("")

    add("### Q8 方法不 work 怎么办？")
    add("")
    if routes:
        limits = [txt(r.get("limits")) for r in routes if txt(r.get("limits"))]
        if limits:
            add("已知局限：" + "；".join(limits))
            add("")
    add("【待补：降级路线，例如换更弱的假设、缩小场景、改用可解释基线】")
    add("")

    add("### Q9 你和最接近的工作有什么本质区别？")
    add("")
    if competitive:
        add("报告识别到的竞争/冲突路线：")
        add("")
        for rec in competitive[:3]:
            add(f"- {fmt_ref(rec)}")
    else:
        add("【待补：报告未识别到竞争层，需人工从综述争议段落找最接近的 2 篇工作】")
    add("")

    add("### Q10 结果不显著怎么办？")
    add("")
    add("- 预先定义「不显著」的判定标准与可接受的效应量")
    add("- 报告必须包含失败分析与消融，而不是只挑显著结果")
    add("- 【待补：备用的次优假设与替代实验】")
    add("")
    return "\n".join(lines)


def build_readme(plan: dict, refs: dict, problems: list) -> str:
    lines = []
    add = lines.append
    add("# 开题素材包使用说明")
    add("")
    add("1. 先读 `00_开题速览.md`，确认方向与导师预期一致，再往下改。")
    add("2. `02_国内外研究现状.md` 的每条主线都能回溯到《科研方向地图》的对应章节。")
    add("3. 所有【待验证】【待补】【待填】标记必须处理完再提交；它们代表「证据不足」或「信息缺失」。")
    add("4. `06_参考文献.bib` 导入 Zotero 后，先核对年份与作者，再用于正式文档。")
    add("5. 不要把本素材包直接当开题报告提交：它是骨架，不是定稿。")
    add("")
    add("## 本次导出信息")
    add("")
    meta = refs.get("meta") or {}
    add(f"- 研究问题：{txt((plan.get('direction_card') or {}).get('question'), '未填写')}")
    add(f"- 学科模板：{txt(meta.get('profile_label') or meta.get('profile'), 'general')}")
    add(f"- 数据截止：{txt(meta.get('newest_date'), '未记录')}")
    add(f"- 必读文献：{len(get_tier_records(refs))} 篇")
    verify = refs.get("_verify_stats")
    if not verify:
        counts = {}
        for rec in (refs.get("records") or {}).values():
            status = (rec.get("doi_verification") or {}).get("status")
            if status:
                counts[status] = counts.get(status, 0) + 1
        if counts:
            verify = {
                "applied": True,
                "verified": counts.get("verified", 0),
                "partial": counts.get("partial", 0),
                "link_only": counts.get("link_only", 0),
                "blocked": sum(counts.get(k, 0) for k in
                               ("mismatch", "not_found", "no_doi", "error")),
            }
        else:
            verify = {}
    if verify.get("applied"):
        add(f"- DOI 回验：通过 {verify.get('verified', 0)} / 部分一致 {verify.get('partial', 0)} / "
            f"链接回溯 {verify.get('link_only', 0)} / 剔除 {verify.get('blocked', 0)}")
    else:
        add("- DOI 回验：未执行（正式材料前必须补做）")
    add("")
    if problems:
        add("## 导出时发现的问题")
        add("")
        for item in problems:
            add(f"- {item}")
        add("")
    add("## 待补项统计")
    add("")
    add("【待补】与【待填】的具体位置可用编辑器全局搜索，逐条处理。")
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="从 plan.json 导出一套开题素材")
    parser.add_argument("--plan", required=True, help="填写完成的 plan.json")
    parser.add_argument("--refs", required=True, help="已回验的 refs（refs_verified.json）")
    parser.add_argument("--report", default="", help="科研方向地图.md 路径（写进 README）")
    parser.add_argument("--out-dir", required=True, help="输出目录，如 开题素材")
    parser.add_argument("--force", action="store_true",
                        help="跳过 plan 校验与不可用文献门禁（不推荐）")
    args = parser.parse_args()

    plan = load_json(Path(args.plan))
    refs = load_json(Path(args.refs))

    # 先按回验结果过滤 tiers/latest/peripheral，再校验 plan，保证门禁口径一致
    records_all = refs.get("records") or {}
    if any((rec or {}).get("doi_verification") for rec in records_all.values()):
        from build_report import apply_verification
        refs, _blocked, _stats = apply_verification(refs)

    records, problems, hard_errors = plan_records(plan, refs)

    if problems:
        print("导出前检查：", file=sys.stderr)
        for item in problems:
            print("  - " + item, file=sys.stderr)
        if hard_errors:
            print("\nplan 引用了不可用文献（被回验剔除/不存在/无链接），先修 plan 再导出。",
                  file=sys.stderr)
            print("确认要强制导出时加 --force。", file=sys.stderr)
            if not args.force:
                return 1

    if not args.force:
        from build_report import validate
        errors = validate(plan, refs)
        if errors:
            print("plan 未通过渲染校验，已中止导出：", file=sys.stderr)
            for err in errors:
                print("  - " + err, file=sys.stderr)
            print("\n修正 plan.json 后重试；确实要带缺陷导出时加 --force。", file=sys.stderr)
            return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "00_开题速览.md": build_overview(plan, refs, records, problems),
        "01_研究背景与意义.md": build_background(plan, refs, records),
        "02_国内外研究现状.md": build_status(plan, refs, records),
        "03_研究内容与技术路线.md": build_method(plan, refs, records),
        "04_创新点候选与风险.md": build_innovation(plan, refs, records),
        "05_答辩预演.md": build_defense(plan, refs, records),
        "README.md": build_readme(plan, refs, problems),
    }
    for name, content in files.items():
        (out_dir / name).write_text(content + "\n", encoding="utf-8", newline="\n")

    bib = to_bibtex(refs)
    (out_dir / "06_参考文献.bib").write_text(bib, encoding="utf-8", newline="\n")

    if args.report:
        report_path = Path(args.report)
        if report_path.exists():
            note = out_dir / "00_地图入口.md"
            import os
            href = os.path.relpath(str(report_path.resolve()), str(out_dir.resolve()))
            href = href.replace("\\", "/")
            note.write_text(
                f"# 配套报告\n\n主报告：[{report_path.name}]({href})\n",
                encoding="utf-8", newline="\n")

    pending_count = 0
    for content in files.values():
        pending_count += content.count("【待补") + content.count("【待填") + content.count("【待验证")
    print(f"已生成开题素材包: {out_dir}")
    fixed_count = len(files) + 1  # 7 个 md + 1 个 bib
    print(f"文件数: {fixed_count} 个固定文件"
          + (" + 00_地图入口.md（因 --report 生成）" if args.report else "")
          + f"；待处理标记约 {pending_count} 处")
    print("  - 00_开题速览.md     先看这一页")
    print("  - 02_国内外研究现状.md 按主线组织，可回溯")
    print("  - 06_参考文献.bib     Zotero / EndNote 导入")
    if problems:
        print(f"警告 {len(problems)} 条，详见 README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())