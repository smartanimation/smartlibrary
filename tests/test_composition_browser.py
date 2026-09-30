from test_usd_handoff import service, asset, plan
from smartlib.apps.smart_composition.browser import composition_versions
from smartlib.core.metadata import read_json, write_json


def test_browser_lists_completed_matching_shot_in_numeric_order(service, tmp_path):
    svc, identity = service
    source = asset(tmp_path)
    result = svc.publish(identity, plan(svc, identity,
        [dict(kind='assets', target='BG', source=str(source))]))
    data = read_json(result, {})
    root = svc.paths.composition_dir(*svc._identity(identity), 'usd')
    for version in ('v002', 'v010'):
        directory = svc.paths.composition_dir(*svc._identity(identity), 'usd', version)
        write_json(svc.paths.artifact_file(directory, 'manifest.json'), dict(data, version=version))
    directory = svc.paths.composition_dir(*svc._identity(identity), 'usd', 'v011')
    directory.mkdir()
    directory = svc.paths.composition_dir(*svc._identity(identity), 'usd', 'v012')
    write_json(svc.paths.artifact_file(directory, 'manifest.json'), dict(data, status='building'))
    directory = svc.paths.composition_dir(*svc._identity(identity), 'usd', 'v013')
    write_json(svc.paths.artifact_file(directory, 'manifest.json'), dict(data, shot={'episode':'other'}))
    assert [v for v, p in composition_versions(svc.shots, identity)] == ['v010','v002','v001']
