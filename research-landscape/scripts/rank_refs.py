#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Research Landscape V2 - 去噪、分层、引文演化与术语种子。

输入：fetch_refs.py 产出的 JSONL
输出：一个 JSON，含五层文献、引文演化顺序、年度时间轴、综述术语、warning

分层是机器规则，结果必须由人复核。脚本只保证：
1. 同一篇文献不会重复出现
2. 只有真正属于该主题的文献进必读清单；仅提及该主题的关联文献单独列出
3. 综述/教程优先，竞争路线单列
4. 配额总和、每层实际入选数与目标一致，差多少就写清原因
5. 清单总数不超过上限（给 80 篇等于没给）

用法：
    python scripts/rank_refs.py --in work/refs.jsonl --out work/refs_ranked.json \
        --topic "研究问题" --focus "OTFS,orthogonal time frequency space"
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

TIER_ORDER = ("review", "foundational", "frontier", "competitive", "core")
PROFILE_DIR = Path(__file__).resolve().parent.parent / "assets" / "profiles"
TIER_LABEL = {
    "review": "骨架（综述/教程）",
    "foundational": "奠基性（早期高被引）",
    "frontier": "前沿（近 2 年）",
    "competitive": "竞争（结论冲突/路线相反）",
    "core": "主干补充",
}
# 权重只决定目标配额，不影响实际能否取到；取不到会回填并记录 shortfall
TIER_WEIGHTS = {
    "review": 1.0,
    "foundational": 1.1,
    "frontier": 1.5,
    "competitive": 1.0,
    "core": 1.3,
}
RECENT_DAYS = 183
MIN_CORE_FOR_TIERS = 8

CONFLICT_PATTERN = re.compile(
    r"\b(contradict|conflict|versus|vs\.?|fair\s+comparison|outperform|"
    r"superior|inferior|refut|counter-?example|myth|overstated|"
    r"not\s+necessary|no\s+(gain|improvement|benefit)|"
    r"does\s+not\s+(improve|help|work)|worse\s+than|revisit|re-?examin)\b",
    re.I,
)

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that",
    "this", "these", "those", "is", "are", "was", "were", "be", "been",
    "being", "to", "of", "in", "on", "for", "with", "by", "from", "as",
    "at", "into", "over", "under", "between", "among", "we", "our", "it",
    "its", "their", "they", "can", "could", "may", "might", "will", "would",
    "should", "has", "have", "had", "do", "does", "did", "not", "no",
    "also", "such", "which", "who", "where", "when", "how", "what", "why",
    "based", "using", "used", "use", "paper", "article", "work", "study",
    "propose", "proposed", "present", "presented", "show", "shown",
    "results", "result", "method", "methods", "approach", "approaches",
    "performance", "different", "various", "several", "however", "therefore",
    "thus", "more", "most", "less", "very", "both", "each", "other",
    "review", "survey", "overview", "tutorial", "introduction",
}


def load_profile(name: str) -> dict:
    if not name:
        return {}
    path = PROFILE_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in PROFILE_DIR.glob("*.json")))
        raise SystemExit(f"学科模板不存在：{name}；可用：{available}")
    return json.loads(path.read_text(encoding="utf-8"))


def norm_title(title: str) -> str:
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", (title or "").lower())
    return " ".join(text.split())


def clean_doi(value) -> str:
    doi = str(value or "").strip().lower()
    prefixes = (
        "https://doi.org/", "http://doi.org/",
        "https://dx.doi.org/", "http://dx.doi.org/", "doi:",
    )
    for prefix in prefixes:
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi.strip().rstrip(" .;,")


def load(path: Path) -> list:
    records = []
    with path.open("r", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def merge_group(group: list) -> dict:
    """合并同一篇论文的多个来源记录，并保留来源信息。"""
    scope_rank = {"off_topic": 0, "related": 1, "mention": 2, "core": 3}
    group = sorted(
        group,
        key=lambda r: (
            0 if r.get("source") == "openalex" else 1,
            -scope_rank.get(r.get("scope"), 0),
            -(r.get("cited_by") or 0),
        ),
    )
    base = dict(group[0])

    base["cited_by"] = max((r.get("cited_by") or 0) for r in group)
    base["sources"] = sorted({r.get("source") for r in group if r.get("source")})

    citation_counts = {}
    for r in group:
        source = r.get("source")
        if source:
            citation_counts[source] = max(
                citation_counts.get(source, 0), r.get("cited_by") or 0)
    base["citation_counts"] = citation_counts

    fields = (
        "title", "abstract", "authors", "venue", "doi", "url", "year",
        "type", "topics", "pub_date", "openalex_id", "scope",
        "pmid", "evidence_level", "mesh_terms", "publication_types",
    )
    field_sources = {}
    for field in fields:
        if base.get(field):
            field_sources[field] = base.get("source")
            continue
        for r in group:
            if r.get(field):
                base[field] = r[field]
                field_sources[field] = r.get("source")
                break
    base["field_sources"] = field_sources

    # 主题中心度、近作优先、竞争信号取最强值；综述信号取并集
    strongest = max(group, key=lambda r: scope_rank.get(r.get("scope"), -1))
    if "scope" not in base or scope_rank.get(strongest.get("scope"), -1) >= scope_rank.get(base.get("scope"), -1):
        base["scope"] = strongest.get("scope")
        base["scope_reason"] = strongest.get("scope_reason") or base.get("scope_reason")
    base["relevance"] = max(float(r.get("relevance") or 0) for r in group)
    base["conflict_signal"] = max(int(r.get("conflict_signal") or 0) for r in group)
    base["recent_priority"] = any(bool(r.get("recent_priority")) for r in group)
    base["is_review"] = any(bool(r.get("is_review")) for r in group)
    base["focus_hits_title"] = max(int(r.get("focus_hits_title") or 0) for r in group)
    base["focus_hits_abstract"] = max(int(r.get("focus_hits_abstract") or 0) for r in group)

    for field in ("matched_terms", "review_signals", "referenced_works"):
        merged = []
        for r in group:
            for item in r.get(field) or []:
                if item not in merged:
                    merged.append(item)
        if merged:
            base[field] = merged[:300]

    base["source_records"] = [dict(r) for r in group]

    topics = []
    for r in group:
        for t in r.get("topics") or []:
            if t not in topics:
                topics.append(t)
    base["topics"] = topics[:8]

    if any(r.get("type") == "preprint" for r in group) and base.get("source") == "openalex":
        base["type"] = base.get("type") or "preprint"

    return base


def dedupe(records: list) -> tuple[list, int]:
    """通过 DOI / OpenAlex ID / 标题作者识别同一研究成果，保留所有原始记录。"""
    parent = list(range(len(records)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def get_year(rec):
        try:
            return int(str(rec.get("year") or "")[:4])
        except (TypeError, ValueError):
            return 0

    def get_author_key(rec):
        authors = rec.get("authors") or []
        if isinstance(authors, str):
            authors = [authors]
        normalized = []
        for name in authors:
            name = unicodedata.normalize("NFKD", str(name).lower())
            name = name.replace("ı", "i")
            name = "".join(ch for ch in name if not unicodedata.combining(ch))
            name = name.encode("ascii", "ignore").decode("ascii")
            name = norm_title(name)
            if name:
                normalized.append(name)
        return tuple(sorted(set(normalized)))

    doi_owner = {}
    title_author_groups = {}
    title_groups = {}

    for i, rec in enumerate(records):
        doi = clean_doi(rec.get("doi"))
        if doi:
            if doi in doi_owner:
                union(i, doi_owner[doi])
            else:
                doi_owner[doi] = i

        oa = (rec.get("openalex_id") or "").strip().upper()
        if oa:
            key_oa = "openalex:" + oa
            if key_oa in doi_owner:
                union(i, doi_owner[key_oa])
            else:
                doi_owner[key_oa] = i

        title_key = norm_title(rec.get("title") or "")

        # 同一篇工作的预印本与正式版：作者列表可能不同，但标题高度一致。
        # 只对足够长的标题做标题级合并，避免短标题误伤。
        if len(title_key) >= 20:
            title_bucket = title_groups.setdefault(title_key, [])
            current_year = get_year(rec)
            for j in title_bucket:
                other_year = get_year(records[j])
                if (not current_year or not other_year
                        or abs(current_year - other_year) <= 2):
                    union(i, j)
            title_bucket.append(i)

        author_key = get_author_key(rec)
        if title_key and author_key:
            key = (title_key, author_key)
            bucket = title_author_groups.setdefault(key, [])
            current_year = get_year(rec)
            for j in bucket:
                other_year = get_year(records[j])
                if not current_year or not other_year or abs(current_year - other_year) <= 1:
                    union(i, j)
            bucket.append(i)

    groups = {}
    for i, rec in enumerate(records):
        groups.setdefault(find(i), []).append(rec)

    merged = [merge_group(group) for group in groups.values()]
    merged.sort(key=lambda r: (-(r.get("cited_by") or 0), -(r.get("year") or 0)))
    return merged, len(records) - len(merged)


def assign_ref_keys(records: list) -> None:
    for rec in records:
        if not rec.get("ref_key"):
            doi = clean_doi(rec.get("doi"))
            if doi:
                rec["ref_key"] = "doi:" + doi
            else:
                rec["ref_key"] = "title:" + norm_title(rec.get("title"))[:80]


def infer_scope(rec: dict, focus_terms: list) -> str:
    """旧数据没有 scope 字段时，按 focus 词补一次判断。"""
    if rec.get("scope") in ("core", "mention", "related", "off_topic"):
        return rec["scope"]
    title = (rec.get("title") or "").lower()
    abstract = (rec.get("abstract") or "").lower()
    for term in focus_terms:
        t = term.lower()
        if t and t in title:
            return "core"
    hits = sum(abstract.count(t.lower()) for t in focus_terms if t)
    if hits >= 2:
        return "core"
    if hits == 1:
        return "mention"
    return "related"


def split_scopes(records: list, focus_terms: list) -> tuple[list, list, list]:
    core, related, mention = [], [], []
    for rec in records:
        scope = infer_scope(rec, focus_terms)
        rec["scope"] = scope
        if scope == "core":
            core.append(rec)
        elif scope == "mention":
            mention.append(rec)
        else:
            related.append(rec)
    return core, related, mention


def score(rec: dict, year_now: int, profile: dict = None) -> float:
    profile = profile or {}
    year = rec.get("year") or year_now - 10
    age = max(0, year_now - year)
    authority = math.log1p(rec.get("cited_by") or 0)
    recency = math.exp(-age / 6.0)
    review_bonus = 2.0 if rec.get("is_review") else 0.0
    focus_bonus = 1.0 if rec.get("scope") == "core" else 0.0
    year_bonus = 2.5 if rec.get("recent_priority") else 0.0
    conflict_bonus = 0.4 if (rec.get("conflict_signal") or 0) >= 3 else 0.0
    return (1.2 * authority + 3.0 * recency + review_bonus +
            focus_bonus + year_bonus + conflict_bonus +
            venue_bonus(rec, profile) + evidence_bonus(rec, profile))


def venue_bonus(rec: dict, profile: dict) -> float:
    """CS 模板：顶会/顶刊命中给显式加成。

    顶会论文的被引数常低于期刊，不加成会被"按引用排序"挤掉。
    """
    venues = profile.get("top_venues") or []
    venue = (rec.get("venue") or "").lower()
    if not venues or not venue:
        return 0.0
    for name in venues:
        needle = name.lower()
        if needle and needle in venue:
            return 2.0
    return 0.0


def evidence_bonus(rec: dict, profile: dict) -> float:
    """医学模板：指南/系统综述/RCT 等证据等级加权。"""
    weights = profile.get("evidence_weights") or {}
    level = (rec.get("evidence_level") or "").lower()
    if not level or level == "other":
        return 0.0
    try:
        return float(weights.get(level, 0.0))
    except (TypeError, ValueError):
        return 0.0


def review_priority(rec: dict, profile: dict) -> int:
    """综述层的学科偏好排序：指南/系统综述/meta 优先于普通 review。"""
    prefer = profile.get("review_prefer") or []
    if not prefer:
        return 0
    text = ((rec.get("title") or "") + " " + (rec.get("abstract") or "")).lower()
    for idx, needle in enumerate(prefer):
        if needle.lower() in text:
            return idx
    return len(prefer)


def allocate_targets(max_refs: int) -> dict:
    """按权重分配目标配额，保证总和恰好等于 max_refs（用最大余数法）。"""
    total_w = sum(TIER_WEIGHTS.values())
    raw = {k: max_refs * TIER_WEIGHTS[k] / total_w for k in TIER_ORDER}
    base = {k: int(math.floor(raw[k])) for k in TIER_ORDER}
    remainder = max_refs - sum(base.values())
    order = sorted(TIER_ORDER, key=lambda k: (raw[k] - base[k], TIER_WEIGHTS[k]), reverse=True)
    for i in range(remainder):
        base[order[i % len(order)]] += 1
    return base


def take(pool: list, count: int, name: str, used: set, year_now: int,
         profile: dict = None) -> list:
    if count <= 0:
        return []
    profile = profile or {}

    def key(rec):
        if name == "review":
            return (review_priority(rec, profile),
                    -(rec.get("cited_by") or 0), -(rec.get("year") or 0))
        if name == "foundational":
            return (-(rec.get("cited_by") or 0), rec.get("year") or 9999)
        if name == "frontier":
            return (0 if rec.get("recent_priority") else 1,
                    -(rec.get("year") or 0), -(rec.get("cited_by") or 0))
        if name == "competitive":
            return (-(rec.get("conflict_signal") or 0), -(rec.get("cited_by") or 0))
        return (-score(rec, year_now, profile), -(rec.get("year") or 0))

    out = []
    for rec in sorted(pool, key=key):
        if rec["ref_key"] in used:
            continue
        used.add(rec["ref_key"])
        rec["tier"] = name
        out.append(rec)
        if len(out) >= count:
            break
    return out


def assign_tiers(records: list, year_now: int, max_refs: int,
                 profile: dict = None) -> tuple[dict, dict, dict, list]:
    """返回 tiers / 目标配额 / 实际入选 / warnings。"""
    profile = profile or {}
    warnings = []
    used = set()
    tiers = {name: [] for name in TIER_ORDER}
    foundational_age = int(profile.get("foundational_age") or 5)

    reviews = [r for r in records if r.get("is_review")]
    non_reviews = [r for r in records if not r.get("is_review")]
    foundational_pool = [
        r for r in non_reviews if (r.get("year") or year_now) <= year_now - foundational_age]
    frontier_pool = [
        r for r in non_reviews if (r.get("year") or 0) >= year_now - 2]
    competitive_pool = [
        r for r in non_reviews
        if (r.get("conflict_signal") or 0) >= 3
        and not re.search(r"\b(challenges?\s+and\s+(opportunities|open\s+problems))\b",
                          r.get("title") or "", re.I)
    ]
    core_pool = list(non_reviews)

    pools = {
        "review": reviews,
        "foundational": foundational_pool,
        "frontier": frontier_pool,
        "competitive": competitive_pool,
        "core": core_pool,
    }

    effective_max = min(max_refs, len(records))
    targets = allocate_targets(effective_max)
    actual = {name: 0 for name in TIER_ORDER}

    for name in TIER_ORDER:
        got = take(pools[name], targets[name], name, used, year_now, profile)
        tiers[name] = got
        actual[name] = len(got)

    if not tiers["review"]:
        warnings.append("未命中任何综述/教程；骨架缺失，建议去掉方法段单独检索综述。")

    if not tiers["foundational"]:
        early_pool = [r for r in records if r["ref_key"] not in used]
        early_pool.sort(key=lambda r: (r.get("year") or year_now, -(r.get("cited_by") or 0)))
        got = take(early_pool[: targets["foundational"] * 2], targets["foundational"],
                   "foundational", used, year_now, profile)
        tiers["foundational"] = got
        actual["foundational"] = len(got)
        if got:
            warnings.append("领域较新，foundational 层由最早文献兜底，代表性有限。")

    if not tiers["frontier"]:
        latest_pool = [r for r in records if r["ref_key"] not in used]
        latest_pool.sort(key=lambda r: (-(r.get("year") or 0), -(r.get("cited_by") or 0)))
        got = take(latest_pool[: targets["frontier"] * 2], targets["frontier"],
                   "frontier", used, year_now, profile)
        tiers["frontier"] = got
        actual["frontier"] = len(got)
        if got:
            warnings.append("近 2 年文献不足，frontier 层由最新文献兜底，前沿判断可能滞后。")

    if not tiers["competitive"]:
        warnings.append(
            "未识别到竞争层（结论冲突/路线相反）；这不代表领域没有争论，"
            "只说明标题摘要里没有可用信号，需人工从综述的争议段落补充。")

    # 配额回填：某层候选不足时，把名额给还有存货的层，尽量填满上限
    total = sum(actual.values())
    fill_order = ["frontier", "foundational", "competitive", "core", "review"]
    while total < effective_max:
        progressed = False
        for name in fill_order:
            if total >= effective_max:
                break
            got = take(pools[name], 1, name, used, year_now, profile)
            if got:
                tiers[name].extend(got)
                actual[name] += 1
                total += 1
                progressed = True
        if not progressed:
            break

    for name in TIER_ORDER:
        tiers[name].sort(key=lambda r: (r.get("year") or 0, -(r.get("cited_by") or 0)))
        for idx, rec in enumerate(tiers[name], start=1):
            rec["tier_rank"] = idx

    shortfall = {k: targets[k] - actual[k] for k in TIER_ORDER}
    if total < effective_max:
        warnings.append(
            f"目标 {effective_max} 篇，实际入选 {total} 篇：去噪后符合主题的候选只有 "
            f"{len(records)} 篇，缺口 {effective_max - total} 篇。"
            "不要用仅提及主题的关联文献凑数。")
    if any(v > 0 for v in shortfall.values()):
        detail = "，".join(f"{TIER_LABEL[k]}缺 {v}" for k, v in shortfall.items() if v > 0)
        warnings.append(f"分层配额未完全兑现：{detail}；缺口已回填到其他层。")
    return tiers, targets, actual, warnings


def build_timeline(records: list, year_now: int) -> list:
    years = sorted({r["year"] for r in records if r.get("year")})
    timeline = []
    for year in years:
        bucket = [r for r in records if r.get("year") == year]
        bucket.sort(key=lambda r: (-(r.get("cited_by") or 0), -(r.get("in_set_cited") or 0)))
        timeline.append({
            "year": year,
            "count": len(bucket),
            "top": [
                {
                    "title": r.get("title"),
                    "cited_by": r.get("cited_by"),
                    "in_set_cited": r.get("in_set_cited", 0),
                    "ref_key": r.get("ref_key"),
                    "authors": (r.get("authors") or [])[:2],
                    "is_review": bool(r.get("is_review")),
                }
                for r in bucket[:3]
            ],
        })
    return timeline


def top_terms(records: list, limit: int = 30) -> list:
    counts = {}
    for rec in records:
        for term in rec.get("topics") or []:
            key = term.strip()
            if 2 <= len(key) <= 60:
                counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"term": term, "count": count} for term, count in ordered[:limit]]


def _phrases(text: str) -> list:
    words = re.findall(r"[A-Za-z][A-Za-z\-]{1,}", (text or "").lower())
    out = []
    for n in (2, 3, 4):
        for i in range(len(words) - n + 1):
            gram = words[i:i + n]
            if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                continue
            if all(w in STOPWORDS for w in gram):
                continue
            if any(len(w) < 2 for w in gram):
                continue
            out.append(" ".join(gram))
    return out


def extract_review_terms(reviews: list, limit: int = 60) -> tuple[list, list]:
    """从综述/教程的摘要抽术语种子。

    理想来源是 Introduction 与 Related Work 全文；OpenAlex/arXiv 只给摘要，
    因此这里明确降级为"综述摘要 + 标题"，并在输出里标注来源，避免假装读了全文。
    """
    warnings = []
    docs = [r for r in reviews if (r.get("abstract") or "").strip()]
    if not docs:
        return [], ["综述/教程没有可用摘要，无法抽术语；需人工从 Introduction 补。"]
    if len(docs) < 2:
        warnings.append(
            f"综述/教程仅 {len(docs)} 篇有摘要，术语种子来自单篇，覆盖不足；"
            "建议再检 1–2 篇综述。")

    df = Counter()
    tf = Counter()
    sources = defaultdict(set)
    for rec in docs:
        text = f"{rec.get('title') or ''}. {rec.get('abstract') or ''}"
        seen = set()
        for phrase in _phrases(text):
            tf[phrase] += 1
            if phrase not in seen:
                df[phrase] += 1
                seen.add(phrase)
            sources[phrase].add(rec.get("ref_key"))

    min_df = 2 if len(docs) >= 2 else 1
    candidates = [
        (p, df[p], tf[p]) for p in df
        if df[p] >= min_df and tf[p] >= 2
    ]
    candidates.sort(key=lambda x: (-x[1], -x[2], -len(x[0]), x[0]))
    terms = [
        {
            "term": p,
            "doc_freq": d,
            "count": t,
            "source": "review_abstract",
            "source_refs": sorted(sources[p])[:5],
        }
        for p, d, t in candidates[:limit]
    ]
    warnings.append(
        "术语种子来自综述/教程摘要（OpenAlex/arXiv 不提供 Introduction 全文），"
        "报告中须标注这一降级来源。")
    return terms, warnings


def build_citation_analysis(records: list) -> tuple[dict, list]:
    """用集合内引用关系排演化顺序，并算共被引。

    边方向：被引文献 -> 引用文献（时间流）。depth 越小越早。
    """
    warnings = []
    by_oa = {}
    for rec in records:
        oa = (rec.get("openalex_id") or "").strip().upper()
        if oa:
            by_oa[oa] = rec

    edges = []
    for rec in records:
        for rid in rec.get("referenced_works") or []:
            target = by_oa.get(str(rid).strip().upper())
            if target and target["ref_key"] != rec["ref_key"]:
                edges.append((target, rec))

    in_cited = Counter(t["ref_key"] for _, t in edges)
    for rec in records:
        rec["in_set_cited"] = in_cited.get(rec["ref_key"], 0)

    coverage = sum(1 for r in records if r.get("referenced_works"))
    analysis = {
        "available": len(edges) >= 3,
        "edge_count": len(edges),
        "coverage": round(coverage / max(1, len(records)), 3),
        "layers": [],
        "top_cited": [],
        "co_cited": [],
    }
    if not analysis["available"]:
        warnings.append(
            "集合内引文边不足，无法可靠排序演化顺序；已退回按年份排序。"
            "可在 OpenAlex 补齐 referenced_works 后重跑。")
        return analysis, warnings

    adj = defaultdict(list)
    indegree = defaultdict(int)
    nodes = {r["ref_key"] for r in records}
    has_in = set()
    has_out = set()
    for cited, citing in edges:
        ck, tk = cited["ref_key"], citing["ref_key"]
        adj[ck].append(tk)
        indegree[tk] += 1
        has_out.add(tk)
        has_in.add(ck)

    depth = {}
    queue = [k for k in nodes if k in has_out and indegree[k] == 0]
    seen = set(queue)
    for k in queue:
        depth[k] = 0
    while queue:
        u = queue.pop(0)
        for v in adj.get(u, []):
            depth[v] = max(depth.get(v, 0), depth.get(u, 0) + 1)
            indegree[v] -= 1
            if indegree[v] == 0 and v not in seen:
                seen.add(v)
                queue.append(v)

    by_key = {r["ref_key"]: r for r in records}
    max_depth = max(depth.values(), default=0)
    # 有出边但落在环里的节点，按最深兜底
    for k in nodes:
        if k in has_out and k not in depth:
            depth[k] = max_depth + 1

    # 只被集合内引用、自己不引用集合内论文的新论文放在最后；
    # 完全没有引文边的记录不参与演化排序，避免被误放在 D0。
    sink_depth = max(depth.values(), default=0) + 1
    for k in nodes:
        if k not in depth:
            depth[k] = sink_depth if k in has_in else None

    for rec in records:
        rec["citation_depth"] = depth.get(rec["ref_key"])

    layers = defaultdict(list)
    for rec in records:
        d = rec.get("citation_depth")
        if d is not None:
            layers[d].append(rec)
    for d in sorted(layers):
        items = sorted(layers[d], key=lambda r: (r.get("year") or 0, -(r.get("cited_by") or 0)))
        analysis["layers"].append({
            "depth": d,
            "count": len(items),
            "items": [
                {
                    "ref_key": r["ref_key"],
                    "title": r.get("title"),
                    "year": r.get("year"),
                    "is_review": bool(r.get("is_review")),
                }
                for r in items
            ],
        })

    top = sorted(records, key=lambda r: (-(r.get("in_set_cited") or 0), -(r.get("cited_by") or 0)))
    analysis["top_cited"] = [
        {
            "ref_key": r["ref_key"],
            "title": r.get("title"),
            "year": r.get("year"),
            "in_set_cited": r.get("in_set_cited", 0),
            "cited_by": r.get("cited_by", 0),
        }
        for r in top[:10] if (r.get("in_set_cited") or 0) > 0
    ]

    # 共被引：两篇文献被同一批集合内论文同时引用
    citing_sets = defaultdict(set)
    for cited, citing in edges:
        citing_sets[cited["ref_key"]].add(citing["ref_key"])
    pair_counts = Counter()
    keys = [k for k, v in citing_sets.items() if len(v) >= 2]
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            pair_counts[(keys[i], keys[j])] += len(citing_sets[keys[i]] & citing_sets[keys[j]])
    co = []
    for (a, b), count in pair_counts.most_common(10):
        if count <= 0:
            continue
        co.append({
            "a": a, "b": b, "count": count,
            "a_title": by_key.get(a, {}).get("title"),
            "b_title": by_key.get(b, {}).get("title"),
        })
    analysis["co_cited"] = co
    return analysis, warnings


def pick_latest(records: list, recent_days: int, limit: int, year_now: int) -> tuple[list, list]:
    """最新通道：按发表日期倒序，不看被引数；当年论文永远优先。"""
    cutoff = (datetime.now() - timedelta(days=recent_days)).strftime("%Y-%m-%d")
    pool = [
        r for r in records
        if (r.get("pub_date") or "") >= cutoff and (r.get("doi") or r.get("url"))
    ]
    pool.sort(key=lambda r: (r.get("pub_date") or "", r.get("cited_by") or 0), reverse=True)
    # 稳定排序：当年论文整体提前，组内仍保持发表日期倒序
    pool.sort(key=lambda r: 0 if (r.get("year") or 0) >= year_now else 1)
    picked = pool[:limit]
    for idx, rec in enumerate(picked, start=1):
        rec["latest_rank"] = idx
        rec["latest_priority"] = "current-year" if (rec.get("year") or 0) >= year_now else "recent"
    warnings = []
    if not picked:
        warnings.append(
            f"{cutoff} 之后未命中带链接的新文献（窗口 {recent_days} 天）；"
            "可能是检索式过窄或数据源收录延迟，建议补充 arXiv 检索或人工核查预印本平台。")
    return picked, warnings


def build_peripheral(records: list, limit: int = 20) -> tuple[list, int]:
    """仅提及主题或相邻领域的关联文献；同名词污染不计入关联文献。"""
    homonym_count = sum(
        1 for r in records if r.get("scope_reason") == "homonym")
    pool = sorted(
        [r for r in records
         if r.get("scope") in ("mention", "related")
         and r.get("scope_reason") != "homonym"],
        key=lambda r: (-(r.get("relevance") or 0), -(r.get("cited_by") or 0)),
    )
    out = []
    for rec in pool[:limit]:
        if rec.get("scope_reason") == "related_only":
            reason = "只命中相邻概念，未以该主题为核心"
        else:
            reason = "摘要仅提及主题一次，不构成主题研究"
        out.append({
            "ref_key": rec["ref_key"],
            "title": rec.get("title"),
            "year": rec.get("year"),
            "cited_by": rec.get("cited_by", 0),
            "doi": rec.get("doi", ""),
            "url": rec.get("url", ""),
            "scope": rec.get("scope"),
            "relevance": rec.get("relevance", 0),
            "reason": reason,
        })
    return out, homonym_count


def main() -> int:
    parser = argparse.ArgumentParser(description="去噪、分层、引文演化与术语种子")
    parser.add_argument("--in", dest="inp", required=True, help="fetch_refs.py 输出的 JSONL")
    parser.add_argument("--out", required=True, help="输出 JSON")
    parser.add_argument("--topic", default="", help="研究问题，写入元信息")
    parser.add_argument("--max-refs", type=int, default=25, help="入选清单总数上限")
    parser.add_argument("--year-now", type=int, default=0, help="基准年份，缺省取系统年份")
    parser.add_argument("--recent-days", type=int, default=RECENT_DAYS,
                        help="最新窗口天数，缺省 183（约 6 个月）")
    parser.add_argument("--latest-limit", type=int, default=10, help="最新进展条数上限")
    parser.add_argument("--focus", default="",
                        help="主题核心词，逗号分隔；旧数据没有 scope 字段时用它补判")
    parser.add_argument("--peripheral-limit", type=int, default=20,
                        help="仅提及/相邻领域文献最多展示条数")
    parser.add_argument("--profile", default="",
                        help="学科模板：general / cs / medicine")
    args = parser.parse_args()

    profile = load_profile(args.profile)

    year_now = args.year_now or datetime.now().year
    records = load(Path(args.inp))
    if not records:
        print("输入为空，请检查检索结果文件。", file=sys.stderr)
        return 1

    merged, removed = dedupe(records)
    assign_ref_keys(merged)

    focus_terms = [t.strip() for t in re.split(r"[,，]", args.focus or "") if t.strip()]
    core_records, related_records, mention_records = split_scopes(merged, focus_terms)

    warnings = []
    core_true_count = len(core_records)
    related_merged = 0
    if len(core_records) < MIN_CORE_FOR_TIERS:
        if related_records:
            warnings.append(
                f"主题核心文献仅 {len(core_records)} 篇，已把 {len(related_records)} 篇"
                "相邻文献并入候选（标记 scope_fallback=related）；"
                "分层代表性下降，必须在报告中说明。")
            for rec in related_records:
                rec["scope_fallback"] = "related"
            core_records = core_records + related_records
            related_merged = len(related_records)
        if len(core_records) < MIN_CORE_FOR_TIERS:
            warnings.append(
                f"去噪后主题核心文献仅 {len(core_records)} 篇，信息量不足以支撑开题；"
                "建议放宽检索式或补充综述检索。")

    has_link = [r for r in core_records if r.get("doi") or r.get("url")]
    if len(has_link) < len(core_records):
        warnings.append(
            f"{len(core_records) - len(has_link)} 条主题记录缺少 DOI 与链接，已排除在清单外。")
        core_records = has_link

    citation, citation_warnings = build_citation_analysis(core_records)
    warnings.extend(citation_warnings)

    tiers, targets, actual, tier_warnings = assign_tiers(core_records, year_now, args.max_refs, profile)
    warnings.extend(tier_warnings)

    latest, latest_warnings = pick_latest(core_records, args.recent_days, args.latest_limit, year_now)
    warnings.extend(latest_warnings)



    timeline = build_timeline(core_records, year_now)
    terms = top_terms(core_records)
    review_terms, term_warnings = extract_review_terms(
        [r for r in core_records if r.get("is_review")])
    warnings.extend(term_warnings)

    selected_keys = {r["ref_key"] for name in TIER_ORDER for r in tiers[name]}
    peripheral_pool = [r for r in (mention_records + related_records)
                       if r["ref_key"] not in selected_keys]
    peripheral, homonym_count = build_peripheral(
        peripheral_pool, args.peripheral_limit)
    if homonym_count:
        warnings.append(
            f"已剔除 {homonym_count} 条同名词记录：这些论文里的主题缩写属于其他学科，"
            "不是本主题文献，也不放进关联文献附录。")

    selected = [rec for name in TIER_ORDER for rec in tiers[name]]
    total_selected = len(selected)
    effective_max = min(args.max_refs, len(core_records))
    if total_selected < MIN_CORE_FOR_TIERS:
        warnings.append(
            f"入选清单仅 {total_selected} 篇，信息量不足以支撑开题；建议扩大检索。")

    scope_counts = {
        "core": len(core_records),
        "related": len(related_records),
        "mention": len(mention_records),
    }
    current_year_count = sum(1 for r in core_records if (r.get("year") or 0) >= year_now)
    recent_priority_count = sum(1 for r in core_records if r.get("recent_priority"))
    review_count = sum(1 for r in core_records if r.get("is_review"))

    quota_shortfall = {k: targets[k] - actual[k] for k in TIER_ORDER}
    result = {
        "meta": {
            "topic": args.topic,
            "year_now": year_now,
            "raw_count": len(records),
            "deduped_count": len(merged),
            "duplicates_removed": removed,
            "selected_count": total_selected,
            "target_count": effective_max,
            "quota_target": targets,
            "quota_actual": actual,
            "quota_shortfall": quota_shortfall,
            "quota_consistent": total_selected == effective_max,
            "scope_counts": scope_counts,
            "core_count": len(core_records),
            "core_true_count": core_true_count,
            "related_merged_count": related_merged,
            "mention_only_count": len(peripheral),
            "homonym_count": homonym_count,
            "review_count": review_count,
            "current_year_count": current_year_count,
            "recent_priority_count": recent_priority_count,
            "citation_available": bool(citation.get("available")),
            "citation_coverage": citation.get("coverage", 0),
            "recent_days": args.recent_days,
            "profile": args.profile or "general",
            "profile_label": profile.get("label") or "通用学科",
            "coverage_note": profile.get("coverage_note") or "",
            "search_note": profile.get("search_note") or "",
            "report_sections": profile.get("report_sections") or [],
            "latest_count": len(latest),
            "newest_date": max((r.get("pub_date") or "" for r in core_records), default=""),
            "focus_terms": focus_terms,
            "tier_label": TIER_LABEL,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "tiers": tiers,
        "latest": latest,
        "peripheral": peripheral,
        "records": {r["ref_key"]: r for r in merged},
        "timeline": timeline,
        "top_terms": terms,
        "review_terms": review_terms,
        "citation_analysis": citation,
        "warnings": warnings,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"原始 {len(records)} 条 -> 去重后 {len(merged)} 条 -> 主题核心 {len(core_records)} 条 "
          f"-> 入选 {total_selected} 篇（目标 {effective_max}）")
    print(f"主题中心度: core {scope_counts['core']} / related {scope_counts['related']} "
          f"/ mention {scope_counts['mention']}；关联文献单列 {len(peripheral)} 条，"
          f"同名词剔除 {homonym_count} 条")
    print(f"综述/教程 {review_count} 篇；{year_now} 年论文 {current_year_count} 篇；"
          f"近 12 个月 {recent_priority_count} 篇")
    print(f"最新文献日期: {result['meta']['newest_date'] or '未知'}   近期进展: {len(latest)} 条")
    print(f"引文关系: {'可用' if citation.get('available') else '不足'}"
          f"（边 {citation.get('edge_count', 0)}，覆盖 {citation.get('coverage', 0):.0%}）")
    for name in TIER_ORDER:
        print(f"  {name:12s} 目标 {targets[name]:2d}  实际 {actual[name]:2d}")
    if quota_shortfall and any(v > 0 for v in quota_shortfall.values()):
        print("  配额缺口: " + "，".join(
            f"{k} -{v}" for k, v in quota_shortfall.items() if v > 0))
    if warnings:
        print("警告:")
        for w in warnings:
            print("  - " + w)
    print(f"已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())