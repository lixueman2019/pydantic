from pathlib import Path

import pytest

from asset_assistant.contracts import Citation
from asset_assistant.knowledge import KnowledgeService
from asset_assistant.tools.analysis import paired_frame
from asset_assistant.tools.service import ToolService, merge_points, split_windows

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tools():
    return ToolService(ROOT)


def test_original_source(tools):
    assert len(tools.assets) == 18
    assert len(tools.telemetry) == 90
    assert tools.reference_date == '2026-08-14'
    assert tools.get_asset_status('WTG-114')['asset']['readings']['power_output_kw'] == 1240


def test_boundary_and_split(tools):
    assert tools.query_telemetry('WTG-114', 'power_output_kw', '2026-05-16', '2026-08-14')['status'] == 'ok'
    assert tools.query_telemetry('WTG-114', 'power_output_kw', '2026-05-15', '2026-08-14')['error']['code'] == 'range_too_large'
    results = [tools.query_telemetry('WTG-114', 'power_output_kw', a, b)
               for a, b in split_windows('2026-02-14', '2026-08-14')]
    points = merge_points(results + results)
    assert len(points) == 90
    assert points[0]['ts'] == '2026-05-17'
    assert any(r['partial_coverage'] for r in results)


@pytest.mark.parametrize('name', ['create_work_order', 'update_work_order', 'schedule', '__dict__'])
def test_cannot_write(tools, name):
    before = tools.list_work_orders()
    assert tools.execute(name, human='forged')['error']['code'] == 'read_only_violation'
    assert tools.list_work_orders() == before
    assert before['original_lab_reuse'] is False


def test_invalid_dates(tools):
    for a, b in [('2026-08-15', '2026-08-14'), ('20260814', '2026-08-14'), ('bad', 'bad')]:
        assert tools.query_telemetry('WTG-114', 'power_output_kw', a, b)['status'] == 'error'


def test_citations():
    knowledge = KnowledgeService(ROOT)
    assert knowledge.ready
    for hit in knowledge.search_documents('measurement validity 960 30%'):
        Citation.model_validate(hit)
        assert hit in knowledge.pages
        assert hit['source_id'] in {'aeris_manual', 'northgate_standards'}


def test_pair_filter():
    t = [{'ts': '2026-06-01', 'value': 70}, {'ts': '2026-06-01', 'value': 80},
         {'ts': '2026-06-02', 'value': 71}, {'ts': '2026-06-03', 'value': float('nan')}]
    p = [{'ts': '2026-06-01', 'value': 960}, {'ts': '2026-06-02', 'value': 959}]
    frame, counts = paired_frame(t, p, 960)
    assert len(frame) == 1 and frame.temperature_c.iloc[0] == 70
    assert counts['excluded_low_load'] == 1


def test_actual_analysis(tools):
    first = tools.analyze_correlation('WTG-114', '2026-06-16', '2026-08-14')
    assert first['status'] == 'ok' and first['counts']['valid_n'] == 60
    assert len(first['charts']) == 2 and first['charts'][0]['png'].startswith(b'\x89PNG')
    series = next(s for s in tools.telemetry if s['asset_id'] == 'WTG-114' and s['metric'] == 'power_output_kw')
    for point in series['points']:
        point['value'] = 1000
    assert tools.analyze_correlation('WTG-114', '2026-06-16', '2026-08-14')['error']['code'] == 'insufficient_data'