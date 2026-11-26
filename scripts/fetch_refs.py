#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Research Landscape V1 - 文献检索。

从 OpenAlex 与 arXiv 拉取文献，归一化成统一的 JSONL 记录。

用法：
    python scripts/fetch_refs.py --query '"graph neural network" drug discovery' --out work/refs.jsonl

设计要点：
- 所有 OpenAlex 检索都走 title_and_abstract.search 的精确匹配，先在精确集内排序，
  否则"按引用数降序"会把领域内与本主题无关的巨型论文（如 BLAST、MAFFT）排在前面
- 4 个检索任务各司其职：相关性、高被引、最近发表、综述
- 精确检索命中过少时自动放宽一次（只保留对象段），并在 warnings 里说明
- OpenAlex 免费额度按 IP 共享（约 $0.10/天），固定请求数 + 磁盘缓存
- 摘要来自 abstract_inverted_index，必须重建
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖：pip install requests")

try:
    # 不强制 utf-8：Windows 控制台默认 GBK，强制 utf-8 会输出乱码
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
USER_AGENT = "research-landscape-skill/1.0"
CACHE_TTL_DAYS = 30
LOW_YIELD_THRESHOLD = 40
RECENT_DAYS = 183          # 近 6 个月算"最新"，可按需通过 spec 的 recent_days 覆盖

SELECT_FIELDS = (
    "id,doi,title,display_name,publication_year,publication_date,cited_by_count,type,"
    "primary_location,authorships,abstract_inverted_index,"
    "referenced_works,referenced_works_count,topics"
)

# 标题/摘要里的综述、教程信号。教程（tutorial）单列，因为它最适合搭骨架。
REVIEW_PATTERN = re.compile(
    r"\b(survey|review|overview|tutorial|advances\s+in|progress\s+in|"
    r"recent\s+(developments|advances|trends)|state[\s-]+of[\s-]+the[\s-]+art|"
    r"systematic\s+review|meta[\s-]+analysis|primer|perspective|roadmap|"
    r"taxonomy|research\s+challenges|open\s+problems|comprehensive\s+"
    r"(review|survey|overview)|introduction\s+to)\b",
    re.I,
)
TUTORIAL_PATTERN = re.compile(r"\btutorial\b", re.I)

REVIEW_ABSTRACT_PATTERN = re.compile(
    r"\b(this\s+(paper|article|work)\s+(surveys|reviews|provides\s+an\s+overview)|"
    r"we\s+(survey|review|provide\s+an\s+overview|present\s+a\s+survey|"
    r"present\s+a\s+tutorial)|literature\s+(review|survey)|"
    r"systematic\s+(review|literature)|meta[\s-]+analysis|"
    r"tutorial\s+(introduction|overview)|comprehensive\s+(review|survey|overview))\b",
    re.I,
)

# 标题含这些词但通常不是综述的反例
REVIEW_FALSE_POSITIVE = re.compile(
    r"(peer\s+review|reviewer|book\s+review|review\s+of\s+review|"
    r"code\s+review|performance\s+review\s+of\s+systems?)", re.I
)

# 这些 OpenAlex 类型不是研究文献，直接视为噪声
NOISE_TYPES = {
    "paratext", "peer-review", "editorial", "erratum", "libguides",
    "reference-entry", "retraction", "supplementary-materials",
}
NOISE_TITLE_PATTERN = re.compile(
    r"^(table\s+of\s+contents|front\s+matter|back\s+matter|index|"
    r"correction|corrigendum|erratum|retraction|editorial\s+board|"
    r"list\s+of\s+reviewers|contents)$", re.I
)

BOOLEAN_WORDS = {"and", "or", "not"}

# 竞争/冲突信号：只认明确的对比、反驳、推翻，不把常见的 "challenge" 当冲突
CONFLICT_PATTERN = re.compile(
    r"\b(contradict|conflict|versus|vs\.?|fair\s+comparison|outperform|"
    r"superior|inferior|refut|counter-?example|myth|overstated|"
    r"not\s+necessary|no\s+(gain|improvement|benefit)|"
    r"does\s+not\s+(improve|help|work)|worse\s+than|revisit|re-?examin)\b",
    re.I,
)


class RateLimited(Exception):
    """数据源返回 429，额度耗尽。"""


def normalize_doi(value: str) -> str:
    if not value:
        return ""
    doi = value.strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.strip().lower()


def rebuild_abstract(inverted) -> str:
    """OpenAlex 的摘要是 {词: [位置]} 倒排索引，重建为自然顺序文本。"""
    if not inverted or not isinstance(inverted, dict):
        return ""
    positions = {}
    for word, idxs in inverted.items():
        if not isinstance(idxs, list):
            continue
        for i in idxs:
            positions[i] = word
    if not positions:
        return ""
    return " ".join(positions[i] for i in sorted(positions))


def clean_text(text: str) -> str:
    if not text:
        return ""
    return " ".join(str(text).split())


def is_review_title(title: str) -> bool:
    if not title:
        return False
    if REVIEW_FALSE_POSITIVE.search(title):
        return False
    return bool(REVIEW_PATTERN.search(title))


def detect_review(title: str, abstract: str, oa_type: str = "") -> tuple[bool, list]:
    """综合类型、标题、摘要判断综述/教程，返回 (是否, 信号列表)。

    只靠标题里的 "review" 会把 'review of reviewer comments' 和普通文章混进来；
    只靠 OpenAlex type 又会漏掉大量标成 article 的综述。三路信号合并更稳。
    """
    signals = []
    oa_type = (oa_type or "").lower()

    if oa_type == "review":
        signals.append("type:review")
    if title and not REVIEW_FALSE_POSITIVE.search(title):
        if REVIEW_PATTERN.search(title):
            signals.append("title:review")
        if TUTORIAL_PATTERN.search(title):
            signals.append("title:tutorial")
        if re.search(r"\b(survey|overview|tutorial)\b", title, re.I):
            signals.append("title:strong")

    if abstract and REVIEW_ABSTRACT_PATTERN.search(abstract):
        signals.append("abstract:review")

    # 标题信号或类型信号足够；摘要信号单独出现时要求摘要不是一句话带过
    strong = any(s.startswith(("type:", "title:")) for s in signals)
    return bool(strong or signals), signals


def normalize_for_match(text: str) -> str:
    text = (text or "").lower()
    # 连字符/斜杠在标题里很常见：Orthogonal Time-Frequency Space 必须等同
    # orthogonal time frequency space，否则真论文会被误判为"仅提及"。
    text = re.sub(r"[-_/–—−]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has_positive_hit(text: str, term: str) -> bool:
    """term 在 text 中出现且前面不是 non-/not/anti- 这类否定前缀。"""
    if not term:
        return False
    for match in re.finditer(re.escape(term), text):
        prefix = text[max(0, match.start() - 6):match.start()]
        if re.search(r"\b(non|not|anti|pseudo)[\s-]*$", prefix):
            continue
        return True
    return False


def _context_near(text: str, focus_terms: list, context_terms: list,
                  window: int = 120) -> bool:
    """focus 词附近是否出现领域上下文词，用来排除同名词污染。

    例：OTFS 在药学里是 oral thin films，在地震里是别的缩写；
    如果摘要里 OTFS 附近没有 wireless/channel/modulation 这类词，就不算主题论文。
    """
    if not context_terms:
        return True
    for term in focus_terms:
        for match in re.finditer(re.escape(term), text):
            prefix = text[max(0, match.start() - 6):match.start()]
            if re.search(r"\b(non|not|anti|pseudo)[\s-]*$", prefix):
                continue
            segment = text[max(0, match.start() - window):match.end() + window]
            if any(ctx and ctx in segment for ctx in context_terms):
                return True
    return False


def _focus_near_start(abstract_l: str, focus_terms: list, limit: int = 260) -> bool:
    head = abstract_l[:limit]
    return any(_has_positive_hit(head, term) for term in focus_terms)


def compute_relevance(title: str, abstract: str, focus_terms: list,
                      broad_terms: list, context_terms: list = None) -> dict:
    """主题中心度：core=真正做这个主题，mention=只在摘要提了一句，
    related=只命中相邻词，off_topic=无关。

    这是去噪的核心。只按关键词是否出现判断会把"用 OTFS 做信道估计"和
    "6G 综述里顺带提到 OTFS"混为一谈。
    """
    title_l = normalize_for_match(title)
    abstract_l = normalize_for_match(abstract)
    focus = [normalize_for_match(t) for t in (focus_terms or []) if t]
    broad = [normalize_for_match(t) for t in (broad_terms or []) if t]

    title_focus = []
    abstract_focus_hits = 0
    matched = []

    for term in focus:
        if _has_positive_hit(title_l, term):
            title_focus.append(term)
            matched.append(term)
        if term:
            abstract_focus_hits += abstract_l.count(term)

    title_broad = [t for t in broad if t and t not in focus and t in title_l]
    abstract_broad_hits = sum(abstract_l.count(t) for t in broad if t and t not in focus)

    if title_focus:
        scope = "core"
        reason = "title_match"
        score = 3.0 + min(2.0, 0.5 * (len(title_focus) - 1))
    elif abstract_focus_hits >= 2:
        # 标题没命中时，摘要多次命中还不够：必须是"摘要开头就在讲这个主题"
        # 且附近出现领域上下文词。否则可能是同名词污染（OTFS 在药学/地震里另有含义）。
        context_ok = (
            not context_terms
            or (_context_near(abstract_l, focus, context_terms)
                and _focus_near_start(abstract_l, focus))
        )
        if context_ok:
            scope = "core"
            reason = "abstract_context"
            score = 1.5 + min(1.5, 0.25 * abstract_focus_hits)
        else:
            scope = "mention"
            reason = "homonym"
            score = 0.6
    elif abstract_focus_hits == 1:
        scope = "mention"
        reason = "mention_once"
        score = 0.6
    elif title_broad or abstract_broad_hits:
        scope = "related"
        reason = "related_only"
        score = 0.4
    else:
        scope = "off_topic"
        reason = "off_topic"
        score = 0.0

    return {
        "relevance": round(score, 2),
        "scope": scope,
        "scope_reason": reason,
        "matched_terms": matched[:8],
        "focus_hits_title": len(title_focus),
        "focus_hits_abstract": abstract_focus_hits,
        "broad_hits": len(title_broad) + abstract_broad_hits,
    }


def is_recent_priority(pub_date: str, year, year_now: int) -> bool:
    """2026 优先：当年论文或近 12 个月论文，不依赖被引数。"""
    if year and int(year) >= year_now:
        return True
    if pub_date:
        try:
            published = datetime.strptime(pub_date[:10], "%Y-%m-%d")
            return (datetime.now() - published).days <= 366
        except ValueError:
            return False
    return False


def conflict_signal(title: str, abstract: str) -> int:
    score = 0
    if title and CONFLICT_PATTERN.search(title):
        score += 3
    if abstract:
        hits = len(CONFLICT_PATTERN.findall(abstract))
        score += min(4, hits)
    return score


def parse_terms(query: str) -> list:
    """从检索式抽检索词：保留引号内短语，丢弃布尔运算符与括号。"""
    terms, seen = [], set()
    for chunk in re.findall(r'"[^"]+"|[^\s"()]+', query or ""):
        term = chunk.strip().strip('"').strip()
        if not term or term.lower() in BOOLEAN_WORDS:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def oa_expression(terms: list) -> str:
    """OpenAlex filter 值：多词短语加引号，多个词之间是 AND。"""
    parts = []
    for term in terms:
        parts.append('"{}"'.format(term) if " " in term else term)
    return " ".join(parts)


def oa_focus_expression(focus_terms: list, other_terms: list) -> str:
    """同义词之间 OR、不同概念之间 AND。"""
    focus = [t for t in (focus_terms or []) if t]
    others = [t for t in (other_terms or []) if t]
    if not focus:
        return oa_expression(others)
    clause = "(" + " OR ".join(
        '"{}"'.format(t) if " " in t else t for t in focus
    ) + ")"
    if others:
        clause += " AND " + " AND ".join(
            '"{}"'.format(t) if " " in t else t for t in others
        )
    return clause


def cache_paths(cache_dir: Path, url: str, params: dict, is_xml: bool):
    raw = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v not in (None, ""))
    key = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    suffix = ".xml" if is_xml else ".json"
    return cache_dir / (key + suffix)


def http_get(url: str, params: dict, cache_dir: Path, use_cache: bool,
             is_xml: bool = False, timeout: int = 45, retries: int = 1) -> str:
    path = cache_paths(cache_dir, url, params, is_xml)
    if use_cache and path.exists():
        age = time.time() - path.stat().st_mtime
        if age < CACHE_TTL_DAYS * 86400:
            return path.read_text(encoding="utf-8")

    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url, params=params, timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            )
            if resp.status_code == 429:
                raise RateLimited("429 rate limit")
            if resp.status_code == 400 and "select" in params:
                # select 字段可能随 schema 变更失效，去掉后重试一次
                params = {k: v for k, v in params.items() if k != "select"}
                resp = requests.get(
                    url, params=params, timeout=timeout,
                    headers={"User-Agent": USER_AGENT},
                )
            resp.raise_for_status()
            text = resp.text
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return text
        except RateLimited:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(f"请求失败：{url} -> {last_error}")


# ---------------------------------------------------------------- OpenAlex


def is_noise_record(title: str, oa_type: str) -> bool:
    if (oa_type or "").lower() in NOISE_TYPES:
        return True
    if not title or len(title.strip()) < 8:
        return True
    return bool(NOISE_TITLE_PATTERN.match(title.strip()))


def to_openalex_record(work: dict, focus_terms: list = None,
                       broad_terms: list = None, year_now: int = 0,
                       context_terms: list = None) -> dict:
    doi = normalize_doi(work.get("doi") or "")
    title = clean_text(work.get("title") or work.get("display_name") or "")
    year = work.get("publication_year")

    venue = ""
    url = ""
    loc = work.get("primary_location") or {}
    if isinstance(loc, dict):
        source = loc.get("source") or {}
        if isinstance(source, dict):
            venue = clean_text(source.get("display_name") or "")
        url = loc.get("landing_page_url") or ""
    if not url:
        url = work.get("id") or ""

    authors = []
    for auth in (work.get("authorships") or [])[:8]:
        person = (auth or {}).get("author") or {}
        name = clean_text(person.get("display_name") or "")
        if name:
            authors.append(name)

    topics = []
    for t in (work.get("topics") or [])[:5]:
        name = clean_text((t or {}).get("display_name") or "")
        if name:
            topics.append(name)
    if not topics:
        for c in (work.get("concepts") or [])[:5]:
            name = clean_text((c or {}).get("display_name") or "")
            if name:
                topics.append(name)

    oa_type = (work.get("type") or "").lower()
    abstract = rebuild_abstract(work.get("abstract_inverted_index"))
    review, review_signals = detect_review(title, abstract, oa_type)
    relevance = compute_relevance(
        title, abstract, focus_terms or [], broad_terms or [], context_terms or [])
    recent_priority = is_recent_priority(
        (work.get("publication_date") or "")[:10],
        int(year) if year else None,
        year_now or datetime.now().year,
    )

    oa_id = (work.get("id") or "").rstrip("/").split("/")[-1]
    if doi:
        ref_key = "doi:" + doi
    elif oa_id:
        ref_key = "openalex:" + oa_id
    else:
        ref_key = "title:" + title[:80]

    referenced = []
    for rid in (work.get("referenced_works") or [])[:300]:
        short = str(rid or "").rstrip("/").split("/")[-1]
        if short:
            referenced.append(short)

    return {
        "ref_key": ref_key,
        "openalex_id": oa_id,
        "title": title,
        "year": int(year) if year else None,
        "cited_by": int(work.get("cited_by_count") or 0),
        "venue": venue,
        "authors": authors,
        "abstract": abstract,
        "doi": doi,
        "url": url,
        "type": oa_type or "unknown",
        "is_review": bool(review),
        "review_signals": review_signals,
        "relevance": relevance["relevance"],
        "scope": relevance["scope"],
        "scope_reason": relevance["scope_reason"],
        "matched_terms": relevance["matched_terms"],
        "focus_hits_title": relevance["focus_hits_title"],
        "focus_hits_abstract": relevance["focus_hits_abstract"],
        "recent_priority": bool(recent_priority),
        "conflict_signal": conflict_signal(title, abstract),
        "topics": topics,
        "ref_count": int(work.get("referenced_works_count") or 0),
        "referenced_works": referenced,
        "pub_date": (work.get("publication_date") or "")[:10],
        "source": "openalex",
    }


def dedupe_openalex_records(records: list[dict]) -> list[dict]:
    """按 DOI / OpenAlex ID 去重，并尽量保留完整字段。"""
    unique = {}

    for rec in records:
        doi = normalize_doi(rec.get("doi") or "")

        if doi:
            key = "doi:" + doi
        else:
            key = rec.get("ref_key") or ""

        if not key:
            title = clean_text(rec.get("title") or "").lower()
            year = rec.get("year") or ""
            key = f"title:{title}|{year}"

        if key not in unique:
            unique[key] = dict(rec)
            continue

        old = unique[key]

        # 补充缺失字段
        for field, value in rec.items():
            if old.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
                old[field] = value

        # 合并作者和主题，避免信息丢失
        for field in ("authors", "topics"):
            merged = list(old.get(field) or [])
            for item in rec.get(field) or []:
                if item not in merged:
                    merged.append(item)
            if merged:
                old[field] = merged

        # 保留较大的引用数
        old["cited_by"] = max(
            int(old.get("cited_by") or 0),
            int(rec.get("cited_by") or 0)
        )

        # 主题中心度取最强的一次判断：标题命中 > 摘要多次命中 > 摘要一次 > 相邻词
        scope_rank = {"off_topic": 0, "related": 1, "mention": 2, "core": 3}
        if scope_rank.get(rec.get("scope"), 0) > scope_rank.get(old.get("scope"), 0):
            old["scope"] = rec.get("scope")
        old["relevance"] = max(
            float(old.get("relevance") or 0),
            float(rec.get("relevance") or 0),
        )
        old["conflict_signal"] = max(
            int(old.get("conflict_signal") or 0),
            int(rec.get("conflict_signal") or 0),
        )
        old["recent_priority"] = bool(old.get("recent_priority") or rec.get("recent_priority"))
        old["is_review"] = bool(old.get("is_review") or rec.get("is_review"))

        for field in ("matched_terms", "review_signals", "referenced_works"):
            merged = list(old.get(field) or [])
            for item in rec.get(field) or []:
                if item not in merged:
                    merged.append(item)
            if merged:
                old[field] = merged[:300]

    return list(unique.values())

def fetch_openalex(terms: list, broad_terms: list, year_from: int, year_to: int,
                   per_page: int, cache_dir: Path, use_cache: bool,
                   mailto: str, api_key: str,
                   spec_recent_days: int = 0,
                   focus_terms: list = None,
                   year_priority: bool = True,
                   context_terms: list = None) -> tuple[list, list]:
    records, warnings = [], []
    terms = [t for t in (terms or []) if t]
    broad_terms = [t for t in (broad_terms or []) if t] or terms
    focus_terms = [t for t in (focus_terms or []) if t] or terms
    expr = oa_expression(terms)
    broad_expr = oa_focus_expression(broad_terms, [])
    focus_expr = oa_focus_expression(focus_terms, [t for t in terms if t not in focus_terms])

    year_now = datetime.now().year
    base = (f"from_publication_date:{year_from}-01-01,"
            f"to_publication_date:{year_to}-12-31,"
            f"is_retracted:false")
    recent_from = max(year_from, year_now - 2)
    recent_days = int(spec_recent_days or RECENT_DAYS)
    latest_from = (datetime.now() - timedelta(days=recent_days)).strftime("%Y-%m-%d")
    # 2026 优先：单独把"本年"整年拉出来，避免只覆盖近 183 天
    year_priority_from = f"{year_now}-01-01"

    common = {"per-page": per_page, "mailto": mailto, "select": SELECT_FIELDS}
    if api_key:
        common["api_key"] = api_key

    jobs = [
        # (名称, filter, 排序, 条数)
        ("core-relevance", f"title_and_abstract.search:{expr},{base},type:article|review|preprint",
         "relevance_score:desc", per_page),
        ("core-cited", f"title_and_abstract.search:{expr},{base},type:article|review|preprint",
         "cited_by_count:desc", max(10, per_page // 2)),
        ("core-recent",
         f"title_and_abstract.search:{expr},from_publication_date:{recent_from}-01-01,"
         f"to_publication_date:{year_to}-12-31,is_retracted:false",
         "publication_date:desc", max(10, per_page // 2)),
        ("core-latest",
         f"title_and_abstract.search:{expr},from_publication_date:{latest_from},"
         f"to_publication_date:{year_to}-12-31,is_retracted:false",
         "publication_date:desc", max(10, per_page // 2)),
        ("review-typed", f"title_and_abstract.search:{expr},{base},type:review",
         "cited_by_count:desc", max(10, per_page // 3)),
    ]
    # 同义词检索：OTFS 与 orthogonal time frequency space 必须都覆盖
    if focus_expr and focus_expr != expr:
        jobs.append(
            ("focus-synonym",
             f"title_and_abstract.search:{focus_expr},{base},type:article|review|preprint",
             "relevance_score:desc", per_page)
        )
    # 当年论文优先通道：被引数还没起来，但不能漏
    if year_priority and year_priority_from <= f"{year_to}-12-31":
        jobs.append(
            ("year-priority",
             f"title_and_abstract.search:{expr},from_publication_date:{year_priority_from},"
             f"to_publication_date:{year_to}-12-31,is_retracted:false",
             "publication_date:desc", max(10, per_page // 2))
        )

    noise_dropped = 0

    
    def run_job(name: str, filt: str, sort: str, size: int):
        nonlocal noise_dropped
        # 将旧检索式拆分为：检索表达式 + 普通过滤条件
        search_part, separator, filter_part = filt.partition(",")

        search_prefix = "title_and_abstract.search:"

        if not separator or not search_part.startswith(search_prefix):
            warnings.append(f"检索任务格式异常：{name}")
            return []

        search_query = search_part[len(search_prefix):].strip()

        # 使用 filter 形式的 search，支持 (A OR B) AND C 这类布尔表达式；
        # search.title_and_abstract.exact 会把整个布尔串当短语，同义词检索会失效。
        params = dict(common)
        params.update({
            "filter": f"title_and_abstract.search:{search_query},{filter_part}",
            "sort": sort,
            "per-page": size
        })

        try:
            raw = http_get(OPENALEX_URL, params, cache_dir, use_cache)
            payload = json.loads(raw)

        except RateLimited:
            warnings.append(
                "OpenAlex 额度耗尽（429），本次可能仅使用 arXiv 结果；"
                "建议次日重跑以获得期刊文献覆盖。"
            )
            return None

        except Exception as exc:  # noqa: BLE001
            warnings.append(f"OpenAlex 请求失败（{name}）：{exc}")
            return []

        out = []

        for work in payload.get("results") or []:
            try:
                rec = to_openalex_record(work, focus_terms, broad_terms, year_now, context_terms)
            except Exception:  # noqa: BLE001
                continue

            if not rec["title"]:
                noise_dropped += 1
                continue
            if is_noise_record(rec["title"], rec.get("type")):
                noise_dropped += 1
                continue
            # 完全无关的记录（连相邻词都没命中）不进结果集
            if rec.get("scope") == "off_topic":
                noise_dropped += 1
                continue
            out.append(rec)

        return out

    # 执行检索任务
    for name, filt, sort, size in jobs:
        got = run_job(name, filt, sort, size)

        if got is None:
            # 额度耗尽，直接停止
            return records, warnings

        
        records = dedupe_openalex_records(records + got)

    # 精确检索命中过少时，放宽到 broad 检索式重试一次
    if len(records) < LOW_YIELD_THRESHOLD and broad_expr != expr:

        warnings.append(
            f"精确检索仅命中 {len(records)} 条，"
            "已自动放宽为 broad 检索式重试一次；"
            "相关性可能下降，分层结果需人工复核。"
        )

        
        
        got = run_job(
          "broad-fallback",
           f"title_and_abstract.search:{broad_expr},{base},type:article|review|preprint",
           "cited_by_count:desc",
            per_page
         )



        if got is None:
            return records, warnings

        if got:
            records = dedupe_openalex_records(records + got)

    elif len(records) < LOW_YIELD_THRESHOLD:

        warnings.append(
            f"精确检索仅命中 {len(records)} 条，"
            "领域可能很新或检索词过窄；"
            "建议回到 Phase 1 放宽检索式（去掉方法段）后重跑。"
        )

    if noise_dropped:
        warnings.append(
            f"检索阶段已过滤 {noise_dropped} 条噪声记录"
            "（更正/社论/无标题/完全无关）；"
            "剩余记录仍会在排序阶段按主题中心度分流。"
        )

    return records, warnings


ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def to_arxiv_record(entry, focus_terms: list = None,
                    broad_terms: list = None, year_now: int = 0,
                    context_terms: list = None) -> dict:
    title = clean_text((entry.findtext(f"{ATOM}title") or ""))
    abstract = clean_text((entry.findtext(f"{ATOM}summary") or ""))
    published = entry.findtext(f"{ATOM}published") or ""
    updated = entry.findtext(f"{ATOM}updated") or ""
    year = int(published[:4]) if published[:4].isdigit() else None
    pub_date = published[:10] if len(published) >= 10 else ""
    if not pub_date and len(updated) >= 10:
        pub_date = updated[:10]

    abs_url = clean_text(entry.findtext(f"{ATOM}id") or "")
    arxiv_id = abs_url.rstrip("/").split("/abs/")[-1] if "/abs/" in abs_url else ""
    doi = normalize_doi(entry.findtext(f"{ARXIV_NS}doi") or "")

    authors = []
    for author in entry.findall(f"{ATOM}author")[:8]:
        name = clean_text(author.findtext(f"{ATOM}name") or "")
        if name:
            authors.append(name)

    primary = entry.find(f"{ARXIV_NS}primary_category")
    category = primary.get("term") if primary is not None else ""
    categories = [category] if category else []
    for c in entry.findall(f"{ARXIV_NS}category"):
        term = (c.get("term") or "").strip()
        if term and term not in categories:
            categories.append(term)

    if doi:
        ref_key = "doi:" + doi
    elif arxiv_id:
        ref_key = "arxiv:" + arxiv_id
    else:
        ref_key = "title:" + title[:80]

    review, review_signals = detect_review(title, abstract, "preprint")
    relevance = compute_relevance(
        title, abstract, focus_terms or [], broad_terms or [], context_terms or [])

    return {
        "ref_key": ref_key,
        "title": title,
        "year": year,
        "cited_by": 0,
        "venue": "arXiv",
        "authors": authors,
        "abstract": abstract,
        "doi": doi,
        "url": abs_url,
        "type": "preprint",
        "is_review": bool(review),
        "review_signals": review_signals,
        "relevance": relevance["relevance"],
        "scope": relevance["scope"],
        "scope_reason": relevance["scope_reason"],
        "matched_terms": relevance["matched_terms"],
        "focus_hits_title": relevance["focus_hits_title"],
        "focus_hits_abstract": relevance["focus_hits_abstract"],
        "recent_priority": is_recent_priority(pub_date, year, year_now or datetime.now().year),
        "conflict_signal": conflict_signal(title, abstract),
        "topics": categories,
        "ref_count": 0,
        "referenced_works": [],
        "pub_date": pub_date,
        "source": "arxiv",
    }


def fetch_arxiv(terms: list, per_page: int, cache_dir: Path,
                use_cache: bool, focus_terms: list = None,
                categories: list = None, broad_terms: list = None,
                context_terms: list = None) -> tuple[list, list]:
    records, warnings = [], []
    terms = [t for t in (terms or []) if t]
    focus_terms = [t for t in (focus_terms or []) if t] or terms
    categories = [c for c in (categories or []) if c]
    if not focus_terms:
        return records, ["未提供检索词，已跳过 arXiv。"]

    def all_clause(items):
        return " AND ".join(
            'all:"{}"'.format(t) if " " in t else "all:" + t for t in items
        )

    # 同义词之间 OR；分类码作为精度过滤（eess.SP / cs.IT 等）
    focus_clause = "(" + " OR ".join(
        'all:"{}"'.format(t) if " " in t else "all:" + t for t in focus_terms
    ) + ")"
    other = [t for t in terms if t not in focus_terms]
    search_query = focus_clause
    if other:
        search_query += " AND " + all_clause(other)
    if categories:
        cat_clause = " OR ".join("cat:" + c for c in categories)
        search_query += " AND (" + cat_clause + ")"

    jobs = [(search_query, "relevance", per_page),
            (search_query, "submittedDate", max(10, per_page // 2))]

    noise_dropped = 0
    for idx, (sq, sort_by, max_results) in enumerate(jobs):
        if idx > 0:
            time.sleep(3)  # arXiv 要求请求间隔
        params = {
            "search_query": sq,
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": "descending",
        }
        try:
            raw = http_get(ARXIV_URL, params, cache_dir, use_cache, is_xml=True)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"arXiv 请求失败（{sort_by}）：{exc}")
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            warnings.append(f"arXiv 响应解析失败：{exc}")
            continue
        for entry in root.findall(f"{ATOM}entry"):
            try:
                rec = to_arxiv_record(entry, focus_terms, broad_terms or terms,
                                     datetime.now().year, context_terms)
            except Exception:  # noqa: BLE001
                continue
            if not rec["title"] or is_noise_record(rec["title"], rec.get("type")):
                noise_dropped += 1
                continue
            if rec.get("scope") == "off_topic":
                noise_dropped += 1
                continue
            records.append(rec)

    if noise_dropped:
        warnings.append(f"arXiv 阶段已过滤 {noise_dropped} 条噪声或无关记录。")
    if categories:
        warnings.append(
            "arXiv 已按分类码过滤：" + ", ".join(categories) +
            "；跨分类工作可能被漏检，必要时去掉分类码重跑。"
        )
    return records, warnings


# ---------------------------------------------------------------- main


def parse_years(text: str) -> tuple[int, int]:
    this_year = datetime.now().year
    if not text:
        return this_year - 12, this_year
    match = re.match(r"^\s*(\d{4})\s*[-–~]\s*(\d{4})\s*$", text)
    if not match:
        raise ValueError("--years 格式应为 2012-2026")
    return int(match.group(1)), int(match.group(2))


def main() -> int:
    parser = argparse.ArgumentParser(description="检索文献并归一化为 JSONL")
    parser.add_argument("--spec", default="",
                        help="检索式定义文件（JSON），推荐使用：避免命令行转义问题")
    parser.add_argument("--query", default="",
                        help="精确检索式（英文），多个词用 AND/OR 连接，短语用双引号")
    parser.add_argument("--broad", default="",
                        help="放宽检索式（通常只保留对象段）；精确检索命中过少时才会用到")
    parser.add_argument("--out", required=True, help="输出 JSONL 路径")
    parser.add_argument("--years", default="", help="年份区间，如 2012-2026")
    parser.add_argument("--per-source", type=int, default=40, help="每源每轮条数")
    parser.add_argument("--sources", default="", help="数据源，逗号分隔；可用 spec.sources 指定")
    parser.add_argument("--cache-dir",
                        default=os.path.join(tempfile.gettempdir(), "research-landscape-cache"),
                        help="缓存目录，默认在系统临时目录下，避免写进技能目录或项目目录")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--mailto", default=os.environ.get("RESEARCH_CONTACT_EMAIL", ""))
    parser.add_argument("--api-key", default=os.environ.get("OPENALEX_API_KEY", ""))
    parser.add_argument("--recent-days", type=int, default=0,
                        help="最新窗口天数，缺省 183（约 6 个月）")
    args = parser.parse_args()

    spec = {}
    if args.spec:
        spec_path = Path(args.spec)
        if not spec_path.exists():
            print(f"检索式文件不存在: {spec_path}", file=sys.stderr)
            return 1
        spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))

    core_terms = [t for t in (spec.get("core") or []) if t] or parse_terms(args.query)
    if not core_terms:
        print("必须提供 --spec 或 --query（至少一个检索词）。", file=sys.stderr)
        return 1
    broad_terms = [t for t in (spec.get("broad") or []) if t] or parse_terms(args.broad) or core_terms
    # focus 是"必须真正属于这个主题"的词；同义词放在这里，检索时用 OR
    focus_terms = [t for t in (spec.get("focus") or []) if t] or core_terms
    # context 用于排除同名词污染：focus 词附近必须出现这些领域词才算主题论文
    context_terms = [t for t in (spec.get("context") or []) if t]
    categories = [c for c in (spec.get("categories") or []) if c]
    year_priority = spec.get("year_priority", True)

    year_from, year_to = parse_years(args.years or spec.get("years") or "")
    sources = {s.strip().lower() for s in (args.sources or spec.get("sources") or "openalex,arxiv").split(",") if s.strip()}
    cache_dir = Path(args.cache_dir)
    per_source = args.per_source
    if spec.get("per_source") and args.per_source == 40:
        per_source = int(spec["per_source"])

    records, warnings = [], []

    if "openalex" in sources:
        recs, warns = fetch_openalex(
            core_terms, broad_terms, year_from, year_to, per_source,
            cache_dir, not args.no_cache, args.mailto, args.api_key,
            spec_recent_days=args.recent_days or spec.get("recent_days", 0),
            focus_terms=focus_terms,
            year_priority=year_priority,
            context_terms=context_terms,
        )
        records.extend(recs)
        warnings.extend(warns)

    if "arxiv" in sources:
        recs, warns = fetch_arxiv(
            core_terms, per_source, cache_dir, not args.no_cache,
            focus_terms=focus_terms,
            categories=categories,
            broad_terms=broad_terms,
            context_terms=context_terms,
        )
        records.extend(recs)
        warnings.extend(warns)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    years = [r["year"] for r in records if r.get("year")]
    reviews = sum(1 for r in records if r.get("is_review"))
    by_source = {}
    scope_counts = {}
    for rec in records:
        by_source[rec["source"]] = by_source.get(rec["source"], 0) + 1
        scope = rec.get("scope") or "unknown"
        scope_counts[scope] = scope_counts.get(scope, 0) + 1

    year_now = datetime.now().year
    recent_priority_count = sum(1 for r in records if r.get("recent_priority"))
    current_year_count = sum(1 for r in records if (r.get("year") or 0) >= year_now)

    if len(records) < LOW_YIELD_THRESHOLD:
        warnings.append(f"检索仅得 {len(records)} 条记录，样本偏小，分层结果仅供参考；"
                        f"建议放宽检索式或扩大年份区间。")
    if reviews < 3:
        warnings.append(f"综述/教程类仅命中 {reviews} 篇，骨架可能不足；"
                        f"建议去掉方法段单独检索领域综述（review OR survey OR tutorial）。")
    if years and min(years) > year_from + 5:
        warnings.append(f"最早文献为 {min(years)} 年，领域可能较新，"
                        f"foundational 层会偏薄甚至为空。")
    if scope_counts.get("mention", 0) or scope_counts.get("related", 0):
        warnings.append(
            f"主题中心度分流：core {scope_counts.get('core', 0)} 篇，"
            f"mention {scope_counts.get('mention', 0)} 篇，"
            f"related {scope_counts.get('related', 0)} 篇；"
            "mention/related 只算关联文献，不进必读清单。"
        )
    if current_year_count == 0:
        warnings.append(
            f"未命中 {year_now} 年论文；可能是检索式过窄或数据源收录延迟。"
            "应补 arXiv submittedDate 检索，不要用旧论文冒充最新。"
        )

    pub_dates = [r.get("pub_date") for r in records if r.get("pub_date")]
    newest_date = max(pub_dates) if pub_dates else (f"{max(years)}-12-31" if years else "")
    if newest_date:
        try:
            gap_days = (datetime.now() - datetime.strptime(newest_date, "%Y-%m-%d")).days
            if gap_days > 60:
                warnings.append(
                    f"最新文献为 {newest_date}（距今 {gap_days} 天），数据源收录存在延迟；"
                    f"若需要更近的进展，应补充 arXiv submittedDate 检索或人工核查预印本平台。"
                )
        except ValueError:
            pass

    stats = {
        "core_terms": core_terms,
        "focus_terms": focus_terms,
        "context_terms": context_terms,
        "broad_terms": broad_terms,
        "categories": categories,
        "query_expression": oa_expression(core_terms),
        "focus_expression": oa_focus_expression(
            focus_terms, [t for t in core_terms if t not in focus_terms]),
        "year_from": year_from,
        "year_to": year_to,
        "year_priority": bool(year_priority),
        "total": len(records),
        "by_source": by_source,
        "review_count": reviews,
        "scope_counts": scope_counts,
        "recent_priority_count": recent_priority_count,
        "current_year_count": current_year_count,
        "year_range": [min(years), max(years)] if years else None,
        "newest_date": newest_date,
        "recent_days": int(args.recent_days or spec.get("recent_days") or RECENT_DAYS),
        "warnings": warnings,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    stats_path = out_path.with_suffix(".stats.json")
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"记录数: {len(records)}  来源: {by_source}  综述/教程: {reviews}")
    print(f"主题中心度: {scope_counts}")
    print(f"当年论文: {current_year_count}  近 12 个月: {recent_priority_count}")
    if years:
        print(f"年份: {min(years)} - {max(years)}")
    if warnings:
        print("警告:")
        for w in warnings:
            print("  - " + w)
    print(f"已写入: {out_path}")
    print(f"统计: {stats_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())