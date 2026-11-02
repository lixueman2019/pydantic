"""Reproducible local audit; writes only generated application provenance artifacts."""
import hashlib
import json
from pathlib import Path

from asset_assistant.knowledge import KnowledgeService
from asset_assistant.tools.service import ToolService


def main():
    root = Path(__file__).resolve().parents[2]
    knowledge, tools = KnowledgeService(root), ToolService(root)
    data = root / 'asset_assistant/data'
    manual = next(m for m in knowledge.metadata if m['source_id'] == 'aeris_manual')
    passages = [p for p in knowledge.pages if p['source_id'] == 'aeris_manual'
                and '960 kW or more' in p['excerpt'] and '30%' in p['excerpt']]
    rule = None
    if passages and manual['version'] in {'Revision G', 'Rev G'}:
        p = passages[0]
        rule = dict(verified=True, minimum_kw=960, comparator='>=', sha256=p['sha256'],
                    citation_id=p['citation_id'], page=p['page'], excerpt=p['excerpt'],
                    limitation='Temperature additionally requires 30 min continuous valid load; fixture cannot verify duration.')
    fixture = data / 'fixtures/replacement_work_orders.json'
    manifest = dict(sources=knowledge.metadata + [tools.provenance(), dict(
        source_id='replacement_work_orders', path=str(fixture.relative_to(root)).replace('\\', '/'),
        sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(), category='replacement_synthetic',
        original_lab_reuse=False, version='local-1', license_status='locally_authored')],
        effective_load_rule=rule, reference_date=tools.reference_date,
        assets=len(tools.assets), telemetry_series=len(tools.telemetry),
        telemetry_points=sum(len(s['points']) for s in tools.telemetry),
        printed_page_numbers='unknown; page means PDF page index, 1-based',
        upstream='Connector comments reference microsoft/new-copilot-studio-tech-guide and troystaylor/SharingIsCaring; upstream data version/license not independently verified.',
        synthetic_training_only=True)
    (data / 'provenance.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (data / 'pdf-pages.json').write_text(json.dumps(knowledge.pages, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: manifest[key] for key in ('reference_date', 'assets', 'telemetry_series', 'telemetry_points')}))
    print('PDF load rule:', bool(rule), '| PDF pages:', [m['pages'] for m in knowledge.metadata])


if __name__ == '__main__':
    main()