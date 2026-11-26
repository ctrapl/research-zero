#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Research Landscape 回归测试。

全部使用 tests/fixtures/refs_sample.jsonl（TEST DATA ONLY），
不访问网络，不写入生产目录。

运行：
    python -m unittest discover -s evals -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_report  # noqa: E402
import export_proposal  # noqa: E402
import fetch_refs  # noqa: E402
import rank_refs  # noqa: E402
import verify_doi  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "refs_sample.jsonl"


def load_fixture() -> list:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_rank(tmp: Path, profile: str = "") -> dict:
    out = tmp / "ranked.json"
    cmd = [
        sys.executable, str(SCRIPTS / "rank_refs.py"),
        "--in", str(FIXTURE), "--out", str(out),
        "--topic", "widget reliability", "--focus", "widget",
        "--max-refs", "15", "--recent-days", "400",
    ]
    if profile:
        cmd += ["--profile", profile]
    subprocess.run(cmd, check=True, capture_output=True)
    return json.loads(out.read_text(encoding="utf-8"))


def make_verified_refs(counts: dict) -> dict:
    """构造带回验状态的 refs：counts = {status: n}。"""
    records = {}
    tiers = {"review": [], "foundational": [], "frontier": [], "competitive": [], "core": []}
    idx = 0
    for status, n in counts.items():
        for _ in range(n):
            idx += 1
            key = f"doi:10.1000/verify.{idx}"
            rec = {
                "ref_key": key,
                "title": f"Verified Record {idx}",
                "year": 2024,
                "authors": ["Test Author"],
                "doi": key.replace("doi:", ""),
                "url": "https://doi.org/" + key.replace("doi:", ""),
                "doi_verification": {"status": status},
            }
            records[key] = rec
            tiers["core"].append(rec)
    return {
        "records": records,
        "tiers": tiers,
        "latest": [],
        "peripheral": [],
        "meta": {"profile": "general", "profile_label": "通用学科"},
    }


def make_plan(keys: list) -> dict:
    return {
        "topic": "widget reliability",
        "audience": "测试",
        "time_budget": "半年",
        "direction_card": {
            "question": "如何提升 widget 的可靠性？",
            "do": ["a", "b", "c"],
            "dont": ["x", "y"],
            "subfield": "widget",
            "keywords_en": ["widget"],
            "keywords_zh": ["部件"],
        },
        "queries": {"core": "widget", "broad": "widget", "review": "widget survey"},
        "landscape": [
            {"line": "主线一", "status": "当前状态", "stages": [
                {"year": 2024, "label": "节点", "why": "原因", "ref_key": keys[0]}]}
        ],
        "routes": [
            {"family": "方法族", "idea": "核心思想", "ref_keys": keys[:2],
             "fits": "条件", "limits": "局限"}
        ],
        "route_advice": "建议",
        "why_read": {k: "理解问题起点，必读。" for k in keys},
        "latest_notes": {},
        "terms": [
            {"zh": "术语", "en": "term", "layer": "入门", "def": "一句话解释", "source": "综述摘要"}
        ],
        "gaps": [{"gap": "空白", "evidence": "本次检索 15 篇主题文献中未见相同方案", "why_not": "a", "needs": "b", "risk": "c"}],
        "next_steps": ["一", "二", "三"],
        "boundary_notes": "仅英文文献",
    }


class TestVerifyStatus(unittest.TestCase):
    def test_link_only_when_no_doi_but_url(self):
        result = verify_doi.verify_one({"title": "x", "doi": "", "url": "http://arxiv.org/abs/1"}, "")
        self.assertEqual(result["status"], "link_only")

    def test_no_doi_when_nothing(self):
        result = verify_doi.verify_one({"title": "x", "doi": "", "url": ""}, "")
        self.assertEqual(result["status"], "no_doi")


class TestApplyVerification(unittest.TestCase):
    def test_keeps_verified_and_link_only_blocks_mismatch(self):
        refs = make_verified_refs({"verified": 2, "link_only": 1, "mismatch": 1})
        latest_key = "doi:10.1000/latest.bad"
        refs["records"][latest_key] = {
            "ref_key": latest_key, "title": "Latest Not Found",
            "url": "https://doi.org/10.1000/latest.bad",
            "doi_verification": {"status": "not_found"},
        }
        refs["latest"] = [refs["records"][latest_key]]
        out, blocked, stats = build_report.apply_verification(refs)

        kept = [r["ref_key"] for r in out["tiers"]["core"]]
        self.assertEqual(len(kept), 3, "verified 2 + link_only 1 应保留")
        self.assertEqual(stats["blocked"], 2)
        self.assertEqual(out["latest"], [], "latest 中 not_found 应剔除")
        self.assertTrue(out["records"]["doi:10.1000/verify.4"]["_blocked"])

    def test_link_only_not_counted_as_blocked(self):
        refs = make_verified_refs({"link_only": 1})
        _out, blocked, stats = build_report.apply_verification(refs)
        self.assertEqual(blocked, [])
        self.assertEqual(stats["blocked"], 0)
        self.assertEqual(stats["link_only"], 1)


class TestValidate(unittest.TestCase):
    def test_plan_citing_blocked_ref_is_error(self):
        refs = make_verified_refs({"verified": 2, "mismatch": 1})
        blocked_key = "doi:10.1000/verify.3"
        refs["records"][blocked_key]["_blocked"] = True
        plan = make_plan(["doi:10.1000/verify.1"])
        plan["landscape"][0]["stages"][0]["ref_key"] = blocked_key
        errors = build_report.validate(plan, refs)
        self.assertTrue(any("已被 DOI 回验剔除" in e for e in errors), errors)

    def test_valid_plan_passes(self):
        refs = make_verified_refs({"verified": 3})
        plan = make_plan(["doi:10.1000/verify.1", "doi:10.1000/verify.2",
                          "doi:10.1000/verify.3"])
        errors = build_report.validate(plan, refs)
        self.assertEqual(errors, [])


class TestPubmedParsing(unittest.TestCase):
    XML = """
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>12345678</PMID>
          <Article>
            <Journal>
              <Title>Journal of Test Medicine</Title>
              <JournalIssue>
                <PubDate><Year>2025</Year><Month>Feb</Month><Day>3</Day></PubDate>
              </JournalIssue>
            </Journal>
            <ArticleTitle>A Randomized Trial of Widget Therapy</ArticleTitle>
            <Abstract>
              <AbstractText Label="BACKGROUND">Widget therapy is common.</AbstractText>
              <AbstractText Label="RESULTS">Improvement was observed.</AbstractText>
            </Abstract>
            <AuthorList>
              <Author><LastName>Patel</LastName><ForeName>Nina</ForeName></Author>
            </AuthorList>
            <PublicationTypeList>
              <PublicationType>Randomized Controlled Trial</PublicationType>
            </PublicationTypeList>
          </Article>
          <MeshHeadingList>
            <MeshHeading><DescriptorName>Widget Therapy</DescriptorName></MeshHeading>
          </MeshHeadingList>
        </MedlineCitation>
        <PubmedData>
          <ArticleIdList>
            <ArticleId IdType="doi">10.1000/test.pmid</ArticleId>
          </ArticleIdList>
        </PubmedData>
      </PubmedArticle>
    </PubmedArticleSet>
    """

    def test_fields(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.XML)
        article = root.find(".//PubmedArticle")
        rec = fetch_refs.to_pubmed_record(article, ["widget"], ["widget"], 2026)

        self.assertEqual(rec["pmid"], "12345678")
        self.assertEqual(rec["doi"], "10.1000/test.pmid")
        self.assertEqual(rec["year"], 2025)
        self.assertEqual(rec["pub_date"], "2025-02-03")
        self.assertEqual(rec["evidence_level"], "rct")
        self.assertFalse(rec["is_review"])
        self.assertIn("Widget Therapy", rec["mesh_terms"])
        self.assertEqual(rec["source"], "pubmed")
        self.assertTrue(rec["url"].startswith("https://pubmed.ncbi.nlm.nih.gov/"))

    def test_review_type_detected(self):
        import xml.etree.ElementTree as ET
        xml = self.XML.replace("Randomized Controlled Trial", "Systematic Review")
        root = ET.fromstring(xml)
        rec = fetch_refs.to_pubmed_record(root.find(".//PubmedArticle"), ["widget"], ["widget"], 2026)
        self.assertEqual(rec["evidence_level"], "systematic_review")
        self.assertTrue(rec["is_review"])


class TestProfileWeights(unittest.TestCase):
    def test_cs_top_venue_bonus(self):
        rec = {"venue": "NeurIPS", "cited_by": 0, "year": 2026}
        plain = {"venue": "Some Journal", "cited_by": 0, "year": 2026}
        profile = {"top_venues": ["NeurIPS"]}
        self.assertGreater(rank_refs.venue_bonus(rec, profile), 0)
        self.assertEqual(rank_refs.venue_bonus(plain, profile), 0)
        self.assertGreater(rank_refs.score(rec, 2026, profile),
                           rank_refs.score(plain, 2026, profile))

    def test_medicine_evidence_bonus(self):
        profile = {"evidence_weights": {"guideline": 3.0, "rct": 2.2}}
        self.assertGreater(rank_refs.evidence_bonus({"evidence_level": "guideline"}, profile),
                           rank_refs.evidence_bonus({"evidence_level": "rct"}, profile))
        self.assertEqual(rank_refs.evidence_bonus({"evidence_level": "other"}, profile), 0)


class TestRankPipeline(unittest.TestCase):
    def test_tiers_and_dedupe(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = run_rank(Path(tmp), profile="cs")
        tiers = data["tiers"]
        total = sum(len(v) for v in tiers.values())
        self.assertLessEqual(total, 15)
        self.assertGreater(len(tiers["review"]), 0, "综述层不能为空")
        self.assertGreater(len(tiers["frontier"]), 0, "前沿层不能为空")

        keys = [r["ref_key"] for v in tiers.values() for r in v]
        self.assertEqual(len(keys), len(set(keys)), "清单内不能有重复")

        titles = [r["title"].lower() for v in tiers.values() for r in v]
        self.assertEqual(len(titles), len(set(titles)), "同一标题不能出现两次")

    def test_mention_excluded_from_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = run_rank(Path(tmp))
        tier_keys = {r["ref_key"] for v in data["tiers"].values() for r in v}
        self.assertNotIn("doi:10.1000/test.m1", tier_keys)
        self.assertIn("doi:10.1000/test.d1",
                      [r["ref_key"] for r in data["peripheral"]])

    def test_title_level_dedupe(self):
        records = [
            {"title": "A Very Long Identical Title for Testing Dedupe", "year": 2024,
             "authors": ["A One"], "doi": "", "source": "arxiv"},
            {"title": "A Very Long Identical Title for Testing Dedupe", "year": 2025,
             "authors": ["B Two"], "doi": "10.1000/x", "source": "openalex"},
        ]
        merged, removed = rank_refs.dedupe(records)
        self.assertEqual(len(merged), 1)
        self.assertEqual(removed, 1)


class TestRender(unittest.TestCase):
    def test_render_contains_profile_and_verify_stats(self):
        refs = make_verified_refs({"verified": 3, "link_only": 1})
        plan = make_plan(["doi:10.1000/verify.1", "doi:10.1000/verify.2"])
        plan["_order"] = []
        refs, blocked, stats = build_report.apply_verification(refs)
        md = build_report.render(plan, refs)
        self.assertIn("学科模板", md)
        self.assertIn("必读文献", md)
        self.assertIn("链接回溯", md)
        self.assertIn("DOI 回验", md)


class TestExportGating(unittest.TestCase):
    def test_plan_citing_blocked_record_is_reported(self):
        refs = make_verified_refs({"verified": 1, "mismatch": 1})
        blocked_key = "doi:10.1000/verify.2"
        refs["records"][blocked_key]["_blocked"] = True
        plan = make_plan([blocked_key])
        _usable, problems, _hard = export_proposal.plan_records(plan, refs)
        self.assertTrue(any("已被 DOI 回验剔除" in p for p in problems), problems)

    def test_unlinked_record_is_reported(self):
        refs = make_verified_refs({"verified": 1})
        rec = refs["records"]["doi:10.1000/verify.1"]
        rec["doi"] = ""
        rec["url"] = ""
        plan = make_plan(["doi:10.1000/verify.1"])
        _usable, problems, _hard = export_proposal.plan_records(plan, refs)
        self.assertTrue(any("缺少可回溯链接" in p for p in problems), problems)


class TestEndToEndExport(unittest.TestCase):
    def test_export_produces_required_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ranked = tmp_path / "ranked.json"
            verified = tmp_path / "verified.json"
            plan_path = tmp_path / "plan.json"
            out_dir = tmp_path / "kit"

            subprocess.run([
                sys.executable, str(SCRIPTS / "rank_refs.py"),
                "--in", str(FIXTURE), "--out", str(ranked),
                "--topic", "widget", "--focus", "widget", "--max-refs", "15",
            ], check=True, capture_output=True)

            data = json.loads(ranked.read_text(encoding="utf-8"))
            for tier, items in data["tiers"].items():
                for rec in items:
                    status = "verified" if rec.get("doi") else "link_only"
                    rec["doi_verification"] = {"status": status}
                    holder = data["records"].get(rec["ref_key"])
                    if holder:
                        holder["doi_verification"] = {"status": status}
            verified.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            keys = [rec["ref_key"] for tier in build_report.TIER_ORDER
                    for rec in data["tiers"].get(tier, [])]
            plan = make_plan(keys)
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

            proc = subprocess.run([
                sys.executable, str(SCRIPTS / "export_proposal.py"),
                "--plan", str(plan_path), "--refs", str(verified),
                "--out-dir", str(out_dir),
            ], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            expected = {
                "00_开题速览.md", "01_研究背景与意义.md", "02_国内外研究现状.md",
                "03_研究内容与技术路线.md", "04_创新点候选与风险.md",
                "05_答辩预演.md", "06_参考文献.bib", "README.md",
            }
            actual = {p.name for p in out_dir.glob("*")}
            self.assertTrue(expected.issubset(actual), expected - actual)
            overview = (out_dir / "00_开题速览.md").read_text(encoding="utf-8")
            self.assertIn("待补清单", overview)


class TestGateHardening(unittest.TestCase):
    def test_empty_sections_rejected(self):
        refs = make_verified_refs({"verified": 2})
        plan = make_plan(["doi:10.1000/verify.1"])
        plan["routes"] = [{"family": "x", "idea": "y", "ref_keys": [""]}]
        plan["terms"] = []
        plan["gaps"] = []
        errors = build_report.validate(plan, refs)
        joined = " | ".join(errors)
        self.assertIn("缺少代表工作", joined)
        self.assertIn("术语表不能为空", joined)
        self.assertIn("疑似空白不能为空", joined)

    def test_gap_evidence_requires_scope(self):
        refs = make_verified_refs({"verified": 1})
        plan = make_plan(["doi:10.1000/verify.1"])
        plan["gaps"][0]["evidence"] = "这是空白"
        errors = build_report.validate(plan, refs)
        self.assertTrue(any("缺少检索范围或限定词" in e for e in errors), errors)

    def test_conflict_signals_exclude_blocked(self):
        refs = make_verified_refs({"verified": 1, "mismatch": 1})
        key = "doi:10.1000/verify.2"
        refs["records"][key]["_blocked"] = True
        refs["records"][key]["conflict_signal"] = 5
        self.assertEqual(build_report.conflict_signals(refs), [])

    def test_export_cli_blocks_plan_citing_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            refs = make_verified_refs({"verified": 1, "mismatch": 1})
            refs["records"]["doi:10.1000/verify.2"]["conflict_signal"] = 0
            refs_path = tmp_path / "refs.json"
            refs_path.write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")
            plan_path = tmp_path / "plan.json"
            plan = make_plan(["doi:10.1000/verify.2"])
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run([
                sys.executable, str(SCRIPTS / "export_proposal.py"),
                "--plan", str(plan_path), "--refs", str(refs_path),
                "--out-dir", str(tmp_path / "kit"),
            ], capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0, "引用被剔除文献时导出必须中止")
            self.assertIn("被 DOI 回验剔除", proc.stderr)


class TestWeeksBudget(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(export_proposal.weeks_from_budget("3–6 个月"), 20)
        self.assertEqual(export_proposal.weeks_from_budget("3-5 年"), 208)
        self.assertEqual(export_proposal.weeks_from_budget("1–2 年"), 78)
        self.assertEqual(export_proposal.weeks_from_budget("半年"), 26)
        self.assertEqual(export_proposal.weeks_from_budget("3 个月"), 13)

    def test_unknown_defaults(self):
        self.assertEqual(export_proposal.weeks_from_budget(""), 24)
        self.assertEqual(export_proposal.weeks_from_budget("看情况"), 24)


class TestExportRegression(unittest.TestCase):
    def test_why_read_residue_of_blocked_is_ignored(self):
        refs = make_verified_refs({"verified": 1, "mismatch": 1})
        blocked_key = "doi:10.1000/verify.2"
        refs["records"][blocked_key]["_blocked"] = True
        plan = make_plan(["doi:10.1000/verify.1"])
        plan["why_read"][blocked_key] = "init_plan 预填的残留理由"
        _usable, problems, hard = export_proposal.plan_records(plan, refs)
        self.assertEqual(hard, [])
        self.assertEqual(problems, [])

    def test_export_cli_allows_why_read_residue(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            refs = make_verified_refs({"verified": 1, "mismatch": 1})
            refs["records"]["doi:10.1000/verify.2"]["_blocked"] = True
            refs_path = tmp_path / "refs.json"
            refs_path.write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")
            plan = make_plan(["doi:10.1000/verify.1"])
            plan["why_read"]["doi:10.1000/verify.2"] = "init_plan 预填的残留理由"
            plan_path = tmp_path / "plan.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run([
                sys.executable, str(SCRIPTS / "export_proposal.py"),
                "--plan", str(plan_path), "--refs", str(refs_path),
                "--out-dir", str(tmp_path / "kit"),
            ], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


class TestLandscapeStages(unittest.TestCase):
    def test_empty_stages_rejected(self):
        refs = make_verified_refs({"verified": 1})
        plan = make_plan(["doi:10.1000/verify.1"])
        plan["landscape"][0]["stages"] = []
        errors = build_report.validate(plan, refs)
        self.assertTrue(any("至少需要 1 个节点" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)