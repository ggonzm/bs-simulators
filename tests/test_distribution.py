"""Distribution and architecture boundaries for the extracted simulator."""

from email.parser import Parser
from importlib.resources import files
from pathlib import Path
import os
import re
import subprocess
import sys
import tomllib
import zipfile

import pytest

import bs_simulators


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_version_matches_new_distribution():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    assert project['name'] == 'bs-simulators'
    assert project['version'] == bs_simulators.__version__ == '0.1.0'
    assert any(re.match(r'^benchmark[-_]systems(?:\s|[<>=!~;\[]|$)', dependency,
                        re.IGNORECASE) for dependency in project['dependencies'])


def test_viewer_is_a_package_resource():
    content = files('bs_simulators.viewers').joinpath('pendulum.html').read_text(encoding='utf-8')
    assert '<html' in content.lower()
    assert '/assets/viewer.js' in content
    assert '/api/state' in files('bs_simulators.viewers').joinpath('viewer.js').read_text(encoding='utf-8')


def test_runtime_import_does_not_load_communications_or_viewers():
    check = subprocess.run(
        [sys.executable, '-c',
         'import sys; import bs_simulators.runtime; '
         'unexpected=[m for m in sys.modules if '
         'm=="bs_simulators.comms" or m.startswith("bs_simulators.comms.") '
         'or m=="bs_simulators.viewers" or m.startswith("bs_simulators.viewers.")]; '
         'assert not unexpected, unexpected'],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert check.returncode == 0, (check.stdout, check.stderr)


def test_built_wheel_contains_viewer_and_external_core_requirement():
    explicit_wheel = os.environ.get('BS_SIMULATORS_WHEEL')
    candidates = [Path(explicit_wheel)] if explicit_wheel else sorted((ROOT / 'dist').glob('bs_simulators-*.whl'))
    if not candidates:
        pytest.skip('build the wheel first, or set BS_SIMULATORS_WHEEL to inspect its contents')
    wheel = candidates[-1]
    assert wheel.is_file(), wheel
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert 'bs_simulators/viewers/pendulum.html' in names
        for name, model in [('pendulum', 'pendulum'), ('dc-motor', 'dc_motor'), ('neutralization', 'neutralization')]:
            assert f'bs_simulators/viewers/{name}.html' in names
            resource = f'bs_simulators/catalogue/{name}.toml'
            assert resource in names
            configuration = tomllib.loads(archive.read(resource).decode('utf-8'))
            assert configuration['model'] == model
            assert {'parameters', 'x0', 'u0'} <= configuration.keys()
        for asset in ('viewer.js', 'scenes.js', 'viewer.css'):
            assert f'bs_simulators/viewers/{asset}' in names
        assert not any(name.startswith('benchmark_systems/') for name in names)
        metadata_path = next(name for name in names if name.endswith('.dist-info/METADATA'))
        metadata = Parser().parsestr(archive.read(metadata_path).decode('utf-8'))
        assert metadata['Name'] == 'bs-simulators'
        assert metadata['Version'] == '0.1.0'
        assert any(re.match(r'^benchmark[-_]systems(?:\s|[<>=!~;\[]|$)', dependency,
                            re.IGNORECASE) for dependency in metadata.get_all('Requires-Dist', []))
        entrypoints_path = next(name for name in names if name.endswith('.dist-info/entry_points.txt'))
        entrypoints = archive.read(entrypoints_path).decode('utf-8')
        assert 'bs-simulators' in entrypoints
        assert 'bs_simulators.__main__:main' in entrypoints.replace(' ', '')
