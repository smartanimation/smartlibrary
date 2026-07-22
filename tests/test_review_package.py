from smartlib.review.package import build_review_package_plan, next_review_take, write_review_package_plan


def test_review_package_uses_output_publish_take_layout(tmp_path):
    shot_root = tmp_path / "shots" / "ep001" / "sq010" / "shot010"
    shot_data = {
        "episode": "ep001",
        "sequence": "sq010",
        "shot": "shot010",
        "editorial": {"cut_in": 1001, "cut_out": 1002, "fps": 24},
    }
    cast_data = {
        "review_layers": {
            "CHA": {
                "members": ["hero"],
                "order": 1,
                "outputs": ["beauty"],
                "resolution": {"width": 1280, "height": 720},
            }
        }
    }

    plan = build_review_package_plan(shot_root, shot_data, cast_data, "layout", version=1, take=2)

    assert plan.version_dir == shot_root / "publish" / "review" / "layout" / "v001" / "take002"
    assert plan.review_json == plan.version_dir / "metadata" / "review.json"
    assert plan.review_data["movie"] == "mov/shot010_layout_v001_take002.mov"
    assert plan.review_data["layers"]["CHA"]["outputs"]["beauty"] == "image_sequence/CHA/shot010_layout_CHA_v001_take002_####.jpg"


def test_review_package_writes_latest_to_review_department_root(tmp_path):
    shot_root = tmp_path / "shots" / "ep001" / "sq010" / "shot010"
    shot_data = {"shot": "shot010", "editorial": {"cut_in": 1001, "cut_out": 1001}}
    cast_data = {"review_layers": {"CHA": {"members": ["hero"]}}}
    plan = build_review_package_plan(shot_root, shot_data, cast_data, "layout", version=3, take=4)

    write_review_package_plan(plan)

    assert (plan.version_dir / "metadata" / "review.json").exists()
    assert (shot_root / "publish" / "review" / "layout" / "latest.json").exists()
    assert next_review_take(shot_root / "publish" / "review" / "layout" / "v003") == 5
