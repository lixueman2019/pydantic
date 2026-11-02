"""Decode complete C# verbatim JSON literals; never execute the connector."""
import copy
import hashlib
import json
import re
import threading
from datetime import date, timedelta
from pathlib import Path

from ..contracts import ALLOWED_TOOLS

CONNECTOR = ('NorthgateEnergyAssetTelemetryMCP_1_0_0_1/Connector/'
             'dci_asset-20telemetry-20mcp_customcodeblobcontent.csx')
PLOT_LOCK = threading.Lock()


def failure(code, message, **details):
    return {'status': 'error', 'error': {'code': code, 'message': message, 'details': details}}


def dates(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a.isoformat() != start or b.isoformat() != end or b < a:
        raise ValueError('Use ordered YYYY-MM-DD dates')
    return a, b


def split_windows(start, end):
    a, b = dates(start, end)
    windows = []
    while a <= b:
        last = min(a + timedelta(days=90), b)
        windows.append((a.isoformat(), last.isoformat()))
        a = last + timedelta(days=1)
    return windows


def merge_points(results):
    unique = {}
    for result in results:
        for point in result.get('points', []):
            unique[(result['asset_id'], result['metric'], point['ts'])] = point
    return [unique[key] for key in sorted(unique)]


class ToolService:
    def __init__(self, root: Path):
        self.root = root
        raw = (root / CONNECTOR).read_bytes()
        self.source_hash = hashlib.sha256(raw).hexdigest()
        text = raw.decode('utf-8-sig')
        def extract(name):
            pattern = rf'\b{name}\s*=\s*JArray\.Parse\(@"((?:""|[^"])*)"\);'
            match = re.search(pattern, text, re.S)
            if not match:
                raise ValueError(f'Missing complete literal: {name}')
            return json.loads(match.group(1).replace('""', '"'))
        self.assets = extract('Assets')
        self.telemetry = extract('Telemetry')
        stamps = [p['ts'] for series in self.telemetry for p in series['points']]
        self.reference_date = max(stamps)
        self.coverage = [min(stamps), max(stamps)]
        self.work_orders = json.loads((root / 'asset_assistant/data/fixtures/replacement_work_orders.json').read_text('utf-8'))
        self.ready = bool(self.assets and self.telemetry)

    def provenance(self):
        return dict(source_id='energy_connector', source_path=CONNECTOR, sha256=self.source_hash,
                    category='original_synthetic', synthetic=True, reference_date=self.reference_date,
                    source_version='1.0.0 (embedded connector metadata)',
                    upstream_version_status='unverified', license_status='unverified',
                    extraction='complete C# verbatim JSON literals; no C# execution', coverage=self.coverage)

    def execute(self, name, **kwargs):
        if name not in ALLOWED_TOOLS or name == 'search_documents':
            return failure('read_only_violation', 'Only registered read/compute operations are permitted.')
        return getattr(self, name)(**kwargs)

    def list_assets(self):
        return dict(status='ok', assets=copy.deepcopy(self.assets), provenance=self.provenance())

    def get_asset_status(self, asset_id):
        asset = next((a for a in self.assets if a['id'] == asset_id), None)
        return (dict(status='ok', asset=copy.deepcopy(asset), provenance=self.provenance()) if asset
                else failure('not_found', 'Unknown asset.'))

    def query_telemetry(self, asset_id, metric, from_date, to_date):
        try:
            a, b = dates(from_date, to_date)
        except (ValueError, TypeError):
            return failure('invalid_date_range', 'Use ordered UTC YYYY-MM-DD dates.')
        if (b - a).days > 90:
            return failure('range_too_large', 'Maximum span is 90 days; split into adjacent inclusive windows.',
                           max_span_days=90, requested=[from_date, to_date])
        series = next((s for s in self.telemetry if s['asset_id'] == asset_id and s['metric'] == metric), None)
        if series is None:
            return failure('not_found', 'Unknown asset or metric.')
        points = sorted({p['ts']: copy.deepcopy(p) for p in series['points']
                         if from_date <= p['ts'] <= to_date}.values(), key=lambda p: p['ts'])
        coverage = [points[0]['ts'], points[-1]['ts']] if points else None
        return dict(status='ok', asset_id=asset_id, metric=metric, unit=series['unit'],
                    requested=[from_date, to_date], coverage=coverage, timezone='UTC',
                    partial_coverage=not coverage or coverage != [from_date, to_date],
                    point_count=len(points), points=points, provenance=self.provenance())

    def list_work_orders(self, asset_id=None):
        return dict(status='ok', work_orders=copy.deepcopy([
            w for w in self.work_orders if asset_id is None or w['asset_id'] == asset_id]),
            category='replacement_synthetic', original_lab_reuse=False)

    def get_work_order(self, work_order_id):
        work_order = next((w for w in self.work_orders if w['id'] == work_order_id), None)
        return (dict(status='ok', work_order=copy.deepcopy(work_order), category='replacement_synthetic',
                     original_lab_reuse=False) if work_order else failure('not_found', 'Unknown work order.'))

    def analyze_correlation(self, asset_id, from_date, to_date):
        from .analysis import analyze
        return analyze(self, asset_id, from_date, to_date)