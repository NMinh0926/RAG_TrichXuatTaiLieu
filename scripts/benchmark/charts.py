"""SVG charts for docs/benchmarks/report.md from the stage 5 JSON results (no plotting dependency).

Run: python -m scripts.benchmark.charts --output docs/benchmarks/charts
"""
import argparse
import json
from html import escape
from pathlib import Path

COLORS = ['#2563eb', '#f59e0b', '#10b981', '#ef4444']
WIDTH, HEIGHT, LEFT, RIGHT, TOP, BOTTOM = 720, 360, 70, 20, 50, 60


def nice_max(value: float) -> float:
    if value <= 0:
        return 1
    step = 10 ** len(str(int(value))) / 10
    for factor in (1, 2, 2.5, 5, 10):
        if value <= step * factor:
            return step * factor
    return value


def bar_chart(title: str, labels: list[str], series: dict[str, list[float | None]], unit: str) -> str:
    """Grouped vertical bars; None values are drawn as missing."""
    top = nice_max(max((v for values in series.values() for v in values if v is not None), default=1))
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM
    group = plot_w / len(labels)
    bar = group * 0.8 / len(series)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" font-family="sans-serif" '
             f'font-size="12"><rect width="100%" height="100%" fill="#fff"/>',
             f'<text x="{WIDTH / 2}" y="22" text-anchor="middle" font-size="15" font-weight="bold">{escape(title)}</text>']
    for tick in range(5):
        value = top * tick / 4
        y = TOP + plot_h - plot_h * tick / 4
        parts.append(f'<line x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y:.1f}" y2="{y:.1f}" stroke="#e5e7eb"/>'
                     f'<text x="{LEFT - 6}" y="{y + 4:.1f}" text-anchor="end">{value:g}</text>')
    parts.append(f'<text x="14" y="{TOP + plot_h / 2}" transform="rotate(-90 14 {TOP + plot_h / 2})" '
                 f'text-anchor="middle">{escape(unit)}</text>')
    for index, label in enumerate(labels):
        x0 = LEFT + group * index + group * 0.1
        for position, (name, values) in enumerate(series.items()):
            value = values[index]
            if value is None:
                continue
            height = plot_h * value / top
            x, y = x0 + bar * position, TOP + plot_h - height
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar - 2:.1f}" height="{height:.1f}" '
                         f'fill="{COLORS[position % len(COLORS)]}"><title>{escape(name)} {escape(label)}: '
                         f'{value:g} {escape(unit)}</title></rect>'
                         f'<text x="{x + bar / 2 - 1:.1f}" y="{y - 4:.1f}" text-anchor="middle" font-size="10">'
                         f'{value:g}</text>')
        parts.append(f'<text x="{LEFT + group * (index + 0.5):.1f}" y="{TOP + plot_h + 18}" '
                     f'text-anchor="middle">{escape(label)}</text>')
    for position, name in enumerate(series):
        x = LEFT + position * 170
        parts.append(f'<rect x="{x}" y="{HEIGHT - 22}" width="12" height="12" fill="{COLORS[position % len(COLORS)]}"/>'
                     f'<text x="{x + 18}" y="{HEIGHT - 12}">{escape(name)}</text>')
    parts.append('</svg>')
    return '\n'.join(parts)


def by_kind(files: list[dict], kind: str, key) -> list[float | None]:
    rows = {row['target_mb']: row for row in files if row['kind'] == kind and row.get('status') == 'succeeded'}
    sizes = sorted({row['target_mb'] for row in files})
    return [key(rows[size]) if size in rows else None for size in sizes]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ingest', type=Path, default=Path('docs/benchmarks/stage5_ingest.json'))
    parser.add_argument('--load', type=Path, default=Path('docs/benchmarks/stage5_qa_load.json'))
    parser.add_argument('--quality', type=Path, default=Path('docs/benchmarks/stage5_quality.json'))
    parser.add_argument('--output', type=Path, default=Path('docs/benchmarks/charts'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    charts = {}
    if args.ingest.exists():
        files = json.loads(args.ingest.read_text(encoding='utf-8'))['files']
        labels = [f'{size} MB' for size in sorted({row['target_mb'] for row in files})]
        charts['ingest_time.svg'] = bar_chart('Thời gian nạp (upload → sẵn sàng)', labels, {
            'PDF có text': by_kind(files, 'text', lambda r: round(r['seconds'] / 60, 1)),
            'PDF scan': by_kind(files, 'scan', lambda r: round(r['seconds'] / 60, 1))}, 'phút')
        charts['ingest_pages_per_second.svg'] = bar_chart('Tốc độ nạp', labels, {
            'PDF có text': by_kind(files, 'text', lambda r: r['pages_per_second']),
            'PDF scan': by_kind(files, 'scan', lambda r: r['pages_per_second'])}, 'trang/giây')
        charts['ingest_memory.svg'] = bar_chart('RAM đỉnh của container khi nạp', labels, {
            'PDF có text': by_kind(files, 'text', lambda r: round(r['peak_memory_mb'] / 1024, 2)),
            'PDF scan': by_kind(files, 'scan', lambda r: round(r['peak_memory_mb'] / 1024, 2))}, 'GB')
    if args.load.exists():
        levels = json.loads(args.load.read_text(encoding='utf-8'))['levels']
        labels = [f"{level['users']} người" for level in levels]
        charts['qa_latency.svg'] = bar_chart('Hỏi đáp đồng thời (median)', labels, {
            'Chữ đầu tiên': [level['ttft_seconds']['median'] for level in levels],
            'Hoàn tất': [level['total_seconds']['median'] for level in levels],
            'Hoàn tất p95': [level['total_seconds']['p95'] for level in levels]}, 'giây')
    if args.quality.exists():
        ranking = json.loads(args.quality.read_text(encoding='utf-8'))['retrieval_ranking']
        ks = sorted(int(key[4:]) for key in ranking if key.startswith('hit@'))
        charts['recall_at_k.svg'] = bar_chart('Recall@k (chunk đúng nằm trong top k)', [f'k={k}' for k in ks],
                                              {'bge-m3': [round(ranking[f'hit@{k}'] * 100, 1) for k in ks]}, '%')
    for name, svg in charts.items():
        (args.output / name).write_text(svg, encoding='utf-8')
        print(args.output / name)


if __name__ == '__main__':
    main()
