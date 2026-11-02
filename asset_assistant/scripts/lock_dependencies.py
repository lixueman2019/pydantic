"""Capture the actually validated environment as pip constraints (no secrets or paths)."""
from importlib.metadata import distributions
from pathlib import Path

packages = sorted({f'{d.metadata["Name"]}=={d.version}' for d in distributions()
                   if d.metadata['Name'].lower() not in {'asset-assistant', 'pip', 'setuptools'}})
target = Path(__file__).resolve().parents[1] / 'constraints-win-py312.txt'
target.write_text('# Validated Windows CPython 3.12 environment; regenerate after tests.\n'
                  + '\n'.join(packages) + '\n', encoding='utf-8')
print(f'Locked {len(packages)} package versions.')