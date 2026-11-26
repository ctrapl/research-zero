
import argparse
import json
import os
import re
import time
import requests

from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import quote


CROSSREF_API = "https://api.crossref.org/works/"


# 1. 统一 DOI 格式 去掉 DOI 前面的网址前缀
def normalize_doi(value):
    if not value:
        return ""

    doi = str(value).strip()

    doi = re.sub(
        r"^https?://(dx\.)?doi\.org/",
        "", doi, flags=re.I
    )

    doi = re.sub(
        r"^doi:\s*", "", doi, flags=re.I
    )

    return doi.strip().lower()


# 2. 统一标题格式 方便比较
def normalize_text(value):
    value = (value or "").lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


# 3. 从 Crossref 获取出版年份
def get_year(item):

    for field in (
        "published-print",
        "published-online",
        "published",
        "created"
    ):
        dates = (
            (item.get(field) or {}).get("date-parts") or []
        )

        if dates and dates[0]:
            return dates[0][0]

    return None


# 4. 提取作者姓氏
def get_family(name):

    name = (name or "").strip()

    if not name:
        return ""

    if "," in name:
        name = name.split(",", 1)[0]
    else:
        name = name.split()[-1]

    return normalize_text(name)


# 5. 核验单篇论文：真正请求Crossref并比较信息
def verify_one(paper, mailto=""):

    doi = normalize_doi(paper.get("doi"))

    if not doi:
        return {
            "status": "no_doi",
            "reason": "没有 DOI"
        }

    headers = {
        "User-Agent": "ResearchLandscape/1.3"
    }

    params = {"mailto": mailto} if mailto else None

    try:

        response = requests.get(
            CROSSREF_API + quote(doi, safe="/"),
            params=params,
            headers=headers,
            timeout=20
        )# 连接Crossref

        if response.status_code == 404:
            return {"status": "not_found"}

        response.raise_for_status()

        item = response.json()["message"]

        # 标题比较
        cross_title = (item.get("title") or [""])[0]

        original_title = paper.get("title") or ""

        similarity = (
            SequenceMatcher(
                None,
                normalize_text(original_title),
                normalize_text(cross_title)
            ).ratio()
            if original_title and cross_title
            else 0
        )

        # 年份比较
        cross_year = get_year(item)

        original_year = paper.get("year")

        year_match = None

        if original_year and cross_year:
            year_match = (
                abs(int(original_year) - int(cross_year)) <= 1
            )

        # 第一作者比较
        authors = paper.get("authors") or []
        cross_authors = item.get("author") or []

        author_match = None

        if authors and cross_authors:

            original_family = get_family(str(authors[0]))

            cross_family = normalize_text(
                cross_authors[0].get("family", "")
            )

            if original_family and cross_family:
                author_match = (
                    original_family == cross_family
                )

        # 综合判断
        mismatch = (
            similarity < 0.92
            or year_match is False
            or author_match is False
        )

        if mismatch:
            status = "mismatch"
        elif similarity >= 0.92:
            status = "verified"
        else:
            status = "partial"

        return {
            "status": status,
            "crossref_title": cross_title,
            "crossref_year": cross_year,
            "title_similarity": round(similarity, 3),
            "year_match": year_match,
            "first_author_match": author_match,
            "checked_at": datetime.now().isoformat()
        }

    except requests.RequestException as e:

        return {
            "status": "error",
            "reason": str(e)
        }


# 6. 主程序
def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--in",
        dest="input_path",
        required=True
    )

    parser.add_argument(
        "--out",
        required=True
    )

    parser.add_argument(
        "--mailto",
        default=os.getenv("CROSSREF_MAILTO", "")
    )

    args = parser.parse_args()

    data = json.loads(
        Path(args.input_path).read_text(
            encoding="utf-8"
        )
    )

    # 只检查四个最终入选文献层
    selected = {}

    for tier in (
        "review",
        "foundational",
        "frontier",
        "competitive",
        "core"
    ):

        for paper in data.get("tiers", {}).get(tier, []):

            key = paper.get("ref_key")

            if key:
                selected[key] = paper

    if not selected:
        raise SystemExit(
            "没有找到最终入选文献，请检查输入文件。"
        )

    counts = {}

    for index, (key, paper) in enumerate(
        selected.items(), start=1
    ):

        # 使用 records 中的主记录
        main_paper = data.get("records", {}).get(key, paper)

        result = verify_one(
            main_paper,
            args.mailto
        )

        main_paper["doi_verification"] = result
        paper["doi_verification"] = result

        status = result["status"]

        counts[status] = counts.get(status, 0) + 1

        print(
            f"[{index}/{len(selected)}] "
            f"{status}: {main_paper.get('title', key)}"
        )

        if status != "no_doi":
            time.sleep(0.2)

    output = Path(args.out)

    output.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("\n核验统计：", counts)
    print("输出文件：", output)


if __name__ == "__main__":
    main()
