"""Pure helpers of the stage 5 benchmark scripts (the measurements themselves need the real stack)."""
import json
import xml.etree.ElementTree as ElementTree

from scripts.benchmark import charts, resources
from scripts.benchmark.bench_load import stats


def test_nice_axis_maximum():
    assert charts.nice_max(0) == 1
    assert charts.nice_max(7) == 10
    assert charts.nice_max(23) == 25
    assert charts.nice_max(120.8) == 200


def test_bar_chart_is_valid_svg_and_skips_missing_values():
    svg = charts.bar_chart('Thời gian <nạp>', ['1 MB', '5 MB'], {'text': [1.5, None], 'scan': [0.5, 2.0]}, 'phút')
    root = ElementTree.fromstring(svg)
    rects = [el for el in root.iter('{http://www.w3.org/2000/svg}rect') if el.find('{http://www.w3.org/2000/svg}title') is not None]
    assert len(rects) == 3
    assert 'Thời gian &lt;nạp&gt;' in svg


def test_charts_from_result_files(tmp_path):
    files = [{'kind': kind, 'target_mb': mb, 'status': 'succeeded', 'seconds': 60.0 * mb, 'pages_per_second': 0.8,
              'peak_memory_mb': 2048} for kind in ('text', 'scan') for mb in (1, 5)]
    (tmp_path / 'ingest.json').write_text(json.dumps({'files': files}), encoding='utf-8')
    level = {'users': 1, 'ttft_seconds': {'median': 1, 'p95': 2}, 'total_seconds': {'median': 3, 'p95': 4}}
    (tmp_path / 'load.json').write_text(json.dumps({'levels': [level]}), encoding='utf-8')
    (tmp_path / 'quality.json').write_text(json.dumps({'retrieval_ranking': {'hit@1': 0.9, 'hit@10': 1.0}}),
                                           encoding='utf-8')
    import sys
    argv = sys.argv
    sys.argv = ['charts', '--ingest', str(tmp_path / 'ingest.json'), '--load', str(tmp_path / 'load.json'),
                '--quality', str(tmp_path / 'quality.json'), '--output', str(tmp_path / 'out')]
    try:
        charts.main()
    finally:
        sys.argv = argv
    assert sorted(path.name for path in (tmp_path / 'out').iterdir()) == [
        'ingest_memory.svg', 'ingest_pages_per_second.svg', 'ingest_time.svg', 'qa_latency.svg', 'recall_at_k.svg']


def test_latency_stats():
    assert stats([]) is None
    assert stats([None, 3.0, 1.0, 2.0]) == {'median': 2.0, 'p95': 3.0, 'max': 3.0}


def test_sampler_summary_without_cgroup(monkeypatch, tmp_path):
    monkeypatch.setattr(resources, 'CGROUP', tmp_path)
    with resources.Sampler(interval=0.01) as sampler:
        pass
    assert sampler.summary() == {'peak_memory_mb': None, 'cpu_cores_average': None, 'cpu_cores_peak': None}
    (tmp_path / 'memory.current').write_text(str(3 * 2**20))
    (tmp_path / 'cpu.stat').write_text('usage_usec 1000\n')
    assert resources.read_int(tmp_path / 'memory.current') == 3 * 2**20 and resources.cpu_usec() == 1000
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'f').write_bytes(b'12345')
    assert resources.directory_bytes(tmp_path / 'sub') == 5
