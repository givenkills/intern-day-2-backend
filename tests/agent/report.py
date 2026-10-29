"""把评测结果 jsonl 汇总成可读的前后对比。

    uv run python tests/agent/report.py tests/agent/results/baseline-xxx.jsonl
    uv run python tests/agent/report.py baseline.jsonl after.jsonl

只做统计与拼表，不重新跑模型。同一份 jsonl 也能直接看单次结果。
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def case_summary(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按用例归并：跑了几次、通过几次、平均耗时、平均字数。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        grouped.setdefault(r["case_id"], []).append(r)

    out: dict[str, dict[str, Any]] = {}
    for cid, runs in grouped.items():
        latencies = [r["elapsed_ms"] for r in runs if r.get("elapsed_ms") is not None]
        lengths = [len(r.get("actual_text") or "") for r in runs]
        tool_ok = 0
        tool_expected = 0
        for r in runs:
            expected = r.get("expect_tool")
            if expected:
                tool_expected += 1
                if any(t.get("name") == expected for t in r.get("tool_calls") or []):
                    tool_ok += 1
        out[cid] = {
            "category": runs[0]["category"],
            "runner": runs[0]["runner"],
            "runs": len(runs),
            "passed": sum(1 for r in runs if r["passed"]),
            "latency_ms": statistics.median(latencies) if latencies else None,
            "chars": round(statistics.mean(lengths)) if lengths else 0,
            "tool_ok": tool_ok,
            "tool_expected": tool_expected,
            "failed_checks": sorted({s["name"] for r in runs for s in r["scores"] if not s["passed"]}),
        }
    return out


def stable_cases(summary: dict[str, dict[str, Any]]) -> int:
    """重复跑的用例全部通过，才算这条用例稳定通过。"""
    return sum(1 for v in summary.values() if v["passed"] == v["runs"])


def render(records: list[dict[str, Any]], label: str) -> list[str]:
    summary = case_summary(records)
    runs = len(records)
    passed = sum(1 for r in records if r["passed"])
    latencies = [r["elapsed_ms"] for r in records if r.get("elapsed_ms") and r["runner"] != "unit"]
    ttfts = [r["ttft_ms"] for r in records if r.get("ttft_ms")]
    tool_ok = sum(v["tool_ok"] for v in summary.values())
    tool_expected = sum(v["tool_expected"] for v in summary.values())
    chars = [len(r.get("actual_text") or "") for r in records if r["runner"] == "agent"]

    lines = [
        f"### {label}",
        "",
        f"- 运行次数：{runs}，通过 {passed}（{passed / runs:.0%}）",
        f"- 用例数：{len(summary)}，全部通过 {stable_cases(summary)} 条",
        f"- 工具调用正确：{tool_ok}/{tool_expected}",
    ]
    if latencies:
        lines.append(
            f"- 响应时间中位数 {statistics.median(latencies) / 1000:.1f}s，"
            f"最长 {max(latencies) / 1000:.1f}s"
        )
    if ttfts:
        lines.append(f"- 首字时延中位数 {statistics.median(ttfts) / 1000:.1f}s（仅 HTTP 用例）")
    if chars:
        lines.append(f"- 回答平均字数 {round(statistics.mean(chars))}（仅模型生成用例）")
    lines.append("")
    return lines


def table(summaries: list[tuple[str, dict[str, dict[str, Any]]]]) -> list[str]:
    ids = sorted({cid for _, s in summaries for cid in s})
    headers = ["用例", "类别"]
    for label, _ in summaries:
        headers += [f"{label} 通过", f"{label} 中位耗时", f"{label} 字数"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for cid in ids:
        item0 = summaries[0][1].get(cid)
        cells = [cid, item0["category"] if item0 else "-"]
        for _, summary in summaries:
            item = summary.get(cid)
            if item is None:
                cells += ["未跑", "-", "-"]
            else:
                cells += [
                    f"{item['passed']}/{item['runs']}",
                    f"{item['latency_ms'] / 1000:.1f}s" if item["latency_ms"] else "-",
                    str(item["chars"]),
                ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="汇总评测结果")
    parser.add_argument("files", nargs="+", help="一个或多个 jsonl")
    parser.add_argument("--labels", default="", help="逗号分隔的标签，默认用文件名")
    args = parser.parse_args()

    paths = [Path(f) for f in args.files]
    labels = [x.strip() for x in args.labels.split(",")] if args.labels else [
        p.stem.split("-")[0] for p in paths
    ]

    all_records = [load(p) for p in paths]
    summaries = [(labels[i], case_summary(all_records[i])) for i in range(len(paths))]

    print()
    for label, records in zip(labels, all_records, strict=False):
        for line in render(records, label):
            print(line)
    print("\n".join(table(summaries)))
    print()


if __name__ == "__main__":
    main()
