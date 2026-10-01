from smartlib.apps.review_build_manager.window import ReviewBuildManagerWindow as Window


def test_build_state_compares_latest_not_last_review(tmp_path):
    scene = tmp_path / 'input.ma'
    scene.write_text('scene')
    row = dict(component={'path': str(scene)}, build_version='v004',
               last_review_version='v002',
               input_versions=[{'version': 'v004'}, {'version': 'v002'}])
    assert Window._local_content_state(row, True) == 'READY'
    row['build_version'] = 'v002'
    assert Window._local_content_state(row, True) == 'UPDATE AVAILABLE'
    assert Window._local_content_state(row, False) == 'EXCLUDED'
    assert row['build_version'] == 'v002'
