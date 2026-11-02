"""Deterministic paired analysis; no generated Python execution."""
import hashlib
import io
import json

import numpy as np
import pandas as pd

from .service import PLOT_LOCK, failure


def paired_frame(temperature, power, minimum_kw):
    def clean(points, column):
        frame = pd.DataFrame(points, columns=['ts', 'value'])
        frame['ts'] = pd.to_datetime(frame['ts'], utc=True, errors='coerce')
        frame['value'] = pd.to_numeric(frame['value'], errors='coerce')
        frame = frame.dropna().loc[lambda f: np.isfinite(f['value'])]
        return frame.drop_duplicates('ts', keep='first').rename(columns={'value': column})
    t, p = clean(temperature, 'temperature_c'), clean(power, 'power_kw')
    paired = t.merge(p, on='ts', how='inner').sort_values('ts')
    filtered = paired[paired.power_kw >= minimum_kw].copy()
    return filtered, dict(raw_temperature=len(temperature), raw_power=len(power),
                          clean_temperature=len(t), clean_power=len(p), paired=len(paired),
                          excluded_low_load=len(paired) - len(filtered), valid_n=len(filtered))


def analyze(service, asset_id, from_date, to_date):
    from ..knowledge import KnowledgeService
    knowledge = KnowledgeService(service.root)
    # Rule is loaded from the audit artifact and bound to the exact current PDF hash.
    path = service.root / 'asset_assistant/data/provenance.json'
    if not path.is_file():
        return failure('data_not_ready', 'PDF effective-load rule not yet audited.')
    audit = json.loads(path.read_text('utf-8'))
    rule = audit.get('effective_load_rule')
    manual = next(m for m in knowledge.metadata if m['source_id'] == 'aeris_manual')
    if not rule or rule.get('sha256') != manual['sha256'] or not rule.get('verified'):
        return failure('data_not_ready', 'Effective-load rule must match the current PDF.')
    temperature = service.query_telemetry(asset_id, 'gearbox_temp_c', from_date, to_date)
    power = service.query_telemetry(asset_id, 'power_output_kw', from_date, to_date)
    for result in (temperature, power):
        if result['status'] != 'ok':
            return result
    frame, counts = paired_frame(temperature['points'], power['points'], rule['minimum_kw'])
    if len(frame) < 3 or frame.temperature_c.nunique() < 2 or frame.power_kw.nunique() < 2:
        return failure('insufficient_data', 'Need >=3 finite aligned samples and nonzero variance.', **counts)
    r = float(np.corrcoef(frame.temperature_c, frame.power_kw)[0, 1])
    data_hash = hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest()
    charts = []
    with PLOT_LOCK:
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib import pyplot as plt
        for scatter in (False, True):
            fig, ax = plt.subplots(figsize=(7, 3.8), layout='constrained')
            try:
                if scatter:
                    ax.scatter(frame.power_kw, frame.temperature_c)
                    ax.set(xlabel='Power (kW)', ylabel='Gearbox temperature (degC)')
                else:
                    ax.plot(frame.ts, frame.temperature_c, label='Temperature (degC)')
                    other = ax.twinx()
                    other.plot(frame.ts, frame.power_kw, color='tab:orange', label='Power (kW)')
                    ax.set(xlabel='UTC', ylabel='Temperature (degC)')
                    other.set_ylabel('Power (kW)')
                    fig.autofmt_xdate()
                ax.set_title(f'{asset_id} | synthetic | n={len(frame)}\n{from_date} to {to_date} UTC')
                buffer = io.BytesIO()
                fig.savefig(buffer, format='png', dpi=110)
                charts.append({'png': buffer.getvalue()})
            finally:
                plt.close(fig)
    return dict(status='ok', asset_id=asset_id, requested=[from_date, to_date],
                coverage=[frame.ts.min().date().isoformat(), frame.ts.max().date().isoformat()],
                counts=counts, pearson_r=r, data_sha256=data_hash, rule=rule,
                provenance=service.provenance(), charts=charts,
                temperature_validity='unverified: daily fixture cannot establish 30 minutes continuous operation at valid load',
                limitations='Exploratory synthetic analysis only; not a temperature-threshold compliance finding. UTC inner join, first duplicate retained, no interpolation. Correlation is not causation.')