"""Turn a model-probe result into a chart and a table: what reasoning effort costs.

    uv run python spikes/effort_chart.py runs/model-probe-<time>.json
    uv run python spikes/effort_chart.py results/model-probe-<time>.json --out results/effort-study.svg

Writes an SVG (two panels per effort: median decision time, and how often the
right row was picked, with cut-off replies shown apart from wrong ones) and
prints a Markdown table for the README. No dependencies beyond the standard
library.
"""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

INK = "#1f2328"
MUTED = "#59636e"
GRID = "#d1d9e0"
TIME_BAR = "#4c8df6"
RIGHT_BAR = "#3dbe8b"
CUT_BAR = "#e5a93b"


def table_markdown(probe: dict) -> str:
    rows = ["| Effort | Median decision | Right row | Valid JSON | Cut off (budget) | "
            "Invalid JSON | Median output tokens |",
            "|---|---|---|---|---|---|---|"]
    for effort, e in probe["efforts"].items():
        ms = e.get("decision_median_ms")
        rows.append(
            f"| {effort} | {f'{ms / 1000:.1f} s' if ms else '—'} | "
            f"{e.get('correct_row_pct', 0):.0f}% | {e.get('valid_json_pct', 0):.0f}% | "
            f"{e.get('cut_off_pct', 0):.0f}% | {e.get('invalid_json_pct', 0):.0f}% | "
            f"{e.get('output_tokens_median') or '—'} |")
    return "\n".join(rows)


def chart_svg(probe: dict) -> str:
    efforts = list(probe["efforts"].items())
    width, row_h, top, left = 760, 46, 78, 96
    panel_w = (width - left - 40) // 2
    height = top + row_h * len(efforts) + 44
    max_ms = max((e.get("decision_median_ms") or 0) for _, e in efforts) or 1
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui, -apple-system, Segoe UI, '
        f'sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="20" y="30" font-size="17" font-weight="600" fill="{INK}">'
        f'{escape(probe.get("model", "model"))}: what reasoning effort costs on a '
        f'row-picking decision</text>',
        f'<text x="20" y="52" font-size="12.5" fill="{MUTED}">'
        f'{len(efforts)} efforts · token budget {probe.get("max_tokens", "?")} per reply · '
        f'cut-off replies (budget too small) shown apart from wrong answers</text>',
    ]
    x_time, x_right = left, left + panel_w + 20
    parts.append(f'<text x="{x_time}" y="{top - 8}" font-size="12" fill="{MUTED}">'
                 f'Median decision time</text>')
    parts.append(f'<text x="{x_right}" y="{top - 8}" font-size="12" fill="{MUTED}">'
                 f'Right row picked (of all attempts)</text>')
    for i, (effort, e) in enumerate(efforts):
        y = top + i * row_h
        parts.append(f'<text x="20" y="{y + 22}" font-size="13.5" font-weight="600" '
                     f'fill="{INK}">{escape(effort)}</text>')
        ms = e.get("decision_median_ms") or 0
        w = max(2, round((panel_w - 70) * ms / max_ms)) if ms else 0
        parts.append(f'<rect x="{x_time}" y="{y + 8}" width="{panel_w - 70}" height="20" '
                     f'rx="3" fill="{GRID}" opacity="0.45"/>')
        if w:
            parts.append(f'<rect x="{x_time}" y="{y + 8}" width="{w}" height="20" rx="3" '
                         f'fill="{TIME_BAR}"/>')
        parts.append(f'<text x="{x_time + max(w, 0) + 6}" y="{y + 23}" font-size="12.5" '
                     f'fill="{INK}">{f"{ms / 1000:.1f} s" if ms else "no valid reply"}</text>')
        right = e.get("correct_row_pct") or 0
        cut = e.get("cut_off_pct") or 0
        scale = (panel_w - 60) / 100
        parts.append(f'<rect x="{x_right}" y="{y + 8}" width="{round(100 * scale)}" '
                     f'height="20" rx="3" fill="{GRID}" opacity="0.45"/>')
        if right:
            parts.append(f'<rect x="{x_right}" y="{y + 8}" width="{round(right * scale)}" '
                         f'height="20" rx="3" fill="{RIGHT_BAR}"/>')
        if cut:
            parts.append(f'<rect x="{x_right + round(right * scale)}" y="{y + 8}" '
                         f'width="{max(2, round(cut * scale))}" height="20" fill="{CUT_BAR}"/>')
        label = f"{right:.0f}%" + (f" · {cut:.0f}% cut off" if cut else "")
        parts.append(f'<text x="{x_right + round(100 * scale) + 6}" y="{y + 23}" '
                     f'font-size="12.5" fill="{INK}">{escape(label)}</text>')
    foot = top + len(efforts) * row_h + 24
    commit = (probe.get("provenance") or {}).get("commit", "")
    parts.append(f'<text x="20" y="{foot}" font-size="11.5" fill="{MUTED}">spikes/model_probe.py'
                 f'{f" at {escape(commit)}" if commit else ""} · one fixed prompt, known right '
                 f'answer (row 4)</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("probe_json", type=Path)
    parser.add_argument("--out", type=Path, default=None,
                        help="SVG path (default: next to the JSON, .svg)")
    args = parser.parse_args()
    probe = json.loads(args.probe_json.read_text(encoding="utf-8"))
    out = args.out or args.probe_json.with_suffix(".svg")
    out.write_text(chart_svg(probe), encoding="utf-8")
    print(table_markdown(probe))
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
