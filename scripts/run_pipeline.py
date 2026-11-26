#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Research Landscape V3 - 一键流水线。

两个阶段：

1) prepare：检索 → 去噪分层 → 生成 plan 骨架
2) render：DOI 回验 → 校验渲染 → 导出开题素材包

叙述部分（脉络、路线、术语、空白）必须由模型/用户填在 plan.json 里，
脚本不会自动编造。

用法：
    python scripts/run_pipeline.py prepare --spec work/query.json --work-dir work
    python scripts/run_pipeline.py render --work-dir work --report 科研方向地图.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def run(cmd: list) -> int:
    if cmd and str(cmd[0]).endswith(".py"):
        cmd = [sys.executable] + cmd
    print("$ " + " ".join(str(c) for c in cmd))
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"\n步骤失败（退出码 {proc.returncode}）。上面的命令可单独重跑排错。",
              file=sys.stderr)
    return proc.returncode


def py(script: str) -> str:
    return str(SCRIPTS / script)


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def prepare(args) -> int:
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    refs_raw = work / "refs.jsonl"
    refs_ranked = work / "refs_ranked.json"
    plan = work / "plan.json"

    cmd = [py("fetch_refs.py"), "--spec", args.spec, "--out", str(refs_raw)]
    if args.profile:
        cmd += ["--profile", args.profile]
    if args.per_source:
        cmd += ["--per-source", str(args.per_source)]
    if args.recent_days:
        cmd += ["--recent-days", str(args.recent_days)]
    code = run(cmd)
    if code:
        return code

    spec = load_json(Path(args.spec), {}) or {}
    stats = load_json(refs_raw.with_suffix(".stats.json"), {}) or {}
    focus = spec.get("focus") or stats.get("focus_terms") or spec.get("core") or []
    focus_text = ",".join(focus) if isinstance(focus, list) else str(focus)

    cmd = [
        py("rank_refs.py"), "--in", str(refs_raw), "--out", str(refs_ranked),
        "--topic", args.topic, "--focus", focus_text,
        "--max-refs", str(args.max_refs),
        "--recent-days", str(args.recent_days or stats.get("recent_days") or 183),
        "--latest-limit", str(args.latest_limit),
    ]
    if args.profile:
        cmd += ["--profile", args.profile]
    code = run(cmd)
    if code:
        return code

    code = run([py("build_report.py"), "--init-plan", str(plan),
                "--refs", str(refs_ranked)])
    if code:
        return code

    print("\n准备完成。接下来：")
    print(f"  1. 填 {plan} 的 direction_card / landscape / routes / terms / gaps / why_read")
    print("  2. 运行 render 阶段：")
    print(f"     python scripts/run_pipeline.py render --work-dir {work} "
          f"--report {args.report_name}")
    return 0


def render(args) -> int:
    work = Path(args.work_dir)
    refs_ranked = work / "refs_ranked.json"
    refs_verified = work / "refs_verified.json"
    plan = work / "plan.json"

    for path in (refs_ranked, plan):
        if not path.exists():
            print(f"缺少文件：{path}。先运行 prepare 并填好 plan.json。", file=sys.stderr)
            return 1

    if args.skip_verify:
        refs_for_render = refs_ranked
        verify_flag = []
    else:
        code = run([py("verify_doi.py"), "--in", str(refs_ranked),
                    "--out", str(refs_verified)])
        if code:
            print("DOI 回验失败。网络不可达时可加 --skip-verify，"
                  "但报告必须注明本次未回验。", file=sys.stderr)
            return code
        refs_for_render = refs_verified
        verify_flag = ["--verify"]

    cmd = [py("build_report.py"), "--plan", str(plan), "--refs", str(refs_for_render),
           "--out", args.report, "--bib", args.bib] + verify_flag
    code = run(cmd)
    if code:
        return code

    if args.proposal_dir:
        code = run([py("export_proposal.py"), "--plan", str(plan),
                    "--refs", str(refs_for_render), "--report", args.report,
                    "--out-dir", args.proposal_dir])
        if code:
            return code

    print("\n交付物：")
    print(f"  - {args.report}")
    print(f"  - {args.bib}")
    if args.proposal_dir:
        print(f"  - {args.proposal_dir}/（开题素材包）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="科研方向地图一键流水线")
    sub = parser.add_subparsers(dest="stage", required=True)

    p1 = sub.add_parser("prepare", help="检索 + 分层 + 生成 plan 骨架")
    p1.add_argument("--spec", required=True, help="检索式 JSON（必填）")
    p1.add_argument("--work-dir", default="work")
    p1.add_argument("--profile", default="", help="general / cs / medicine")
    p1.add_argument("--topic", default="", help="研究问题")
    p1.add_argument("--max-refs", type=int, default=25)
    p1.add_argument("--latest-limit", type=int, default=10)
    p1.add_argument("--per-source", type=int, default=0)
    p1.add_argument("--recent-days", type=int, default=0)
    p1.add_argument("--report-name", default="科研方向地图.md")

    p2 = sub.add_parser("render", help="回验 + 渲染 + 导出素材")
    p2.add_argument("--work-dir", default="work")
    p2.add_argument("--report", default="科研方向地图.md")
    p2.add_argument("--bib", default="参考文献.bib")
    p2.add_argument("--proposal-dir", default="开题素材")
    p2.add_argument("--skip-verify", action="store_true",
                    help="Crossref 不可达时跳过回验（报告中会标注）")

    args = parser.parse_args()
    return prepare(args) if args.stage == "prepare" else render(args)


if __name__ == "__main__":
    raise SystemExit(main())