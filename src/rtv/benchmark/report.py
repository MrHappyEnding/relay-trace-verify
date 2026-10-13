"""Inspectable derived reports with raw-input hashes and English explanations."""

import csv
import hashlib
import json


def export(report, output, *, plan, records):
    output.mkdir(parents=True, exist_ok=False)
    (output / "records.jsonl").write_bytes(records.read_bytes())
    (output / "protocol.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    result = {**report, "raw_records_sha256": hashlib.sha256(records.read_bytes()).hexdigest()}
    (output / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    rows = result["observations"]
    if rows:
        fields = sorted(set().union(*(r.keys() for r in rows)))
        with (output / "summary.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    intro = (
        "This report does not launch models. Missing observations remain in the denominator; "
        "confidence intervals are withheld while observations are missing. Subscription "
        "inference cost is not zero. Synthetic/oracle checks do not measure "
        "model effectiveness."
    )
    lines = [
        "# RTV SWE-bench report",
        "",
        f"Mode: **{result['mode']}** · Split: **{result['split']}**",
        "",
        intro,
        "",
        f"Received / planned: {result['received']} / {result['planned']}",
        "",
        "| Branch | Solved / planned | Graded | Missing | Known API USD | Unknown cost rows |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for branch, r in result["arms"].items():
        lines.append(
            f"| {branch} | {r['outcomes'].get('solved', 0)} / {r['planned']} | "
            f"{r['graded']} | {r['outcomes'].get('missing', 0)} | "
            f"{r['known_incremental_api_cost_usd']:.6f} | {r['unknown_cost_rows']} |"
        )
    lines += [
        "",
        "| Contrast | Task clusters | Difference | 95% CI |",
        "| --- | ---: | ---: | --- |",
    ]
    for contrast, r in result["comparisons"].items():
        lines.append(f"| {contrast} | {r['tasks']} | {r['difference']} | {r['ci95']} |")
    lines += [
        "",
        "[JSON](report.json) · [CSV](summary.csv) · [Raw input](records.jsonl)",
        "",
        f"Protocol SHA256: `{result['protocol_sha256']}`",
        "",
        f"Raw SHA256: `{result['raw_records_sha256']}`",
        "",
    ]
    (output / "report.md").write_text("\n".join(lines))
