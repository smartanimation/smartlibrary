from smartlib.dcc.maya import review_camera_rules


def test_publish_collection_uses_all_published_layers_not_playblast_checks(monkeypatch):
    captured = {}

    def collect(primary, rows, resolution, primary_publish, cmds):
        captured.update(rows=rows, primary=primary, resolution=resolution)
        return {"rows": rows}

    monkeypatch.setattr(review_camera_rules, "collect", collect)
    result = review_camera_rules.collect_for_review_layers(
        "cam",
        [
            {"layer": "CHA", "enabled": True, "start": 1, "end": 10},
            {"layer": "BGA", "enabled": False, "start": 1, "end": 10},
        ],
        {
            "CHA": {"display_layer": "CHA", "enabled": True},
            "BGA": {"display_layer": "BGA", "enabled": True},
        },
        [1920, 1080],
        "primary.json",
        object(),
        layer_rules={"BGA": {"mode": "scale", "scale": 1.2}},
    )

    assert [row["layer"] for row in result["rows"]] == ["CHA", "BGA"]
    assert all(row["enabled"] for row in result["rows"])
    assert result["rows"][1]["camera_rule"] == {"mode": "scale", "scale": 1.2}
