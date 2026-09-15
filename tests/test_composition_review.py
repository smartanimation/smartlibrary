from types import SimpleNamespace

from smartlib.apps.review_build_manager import composition_review as review


def test_review_queue_pins_source_and_bypasses_cache(tmp_path, monkeypatch):
    identity = {"episode":"ep", "sequence":"seq", "shot":"shot"}
    receipt = {"department":"anim", "task":"preComp", "scene":"submitted.ma"}
    fake = SimpleNamespace(load=lambda *a, **k: {"shot":identity},
                           _reserve=lambda root: ("v001", tmp_path))
    monkeypatch.setattr(review, "AnimationCompositionService", lambda shots: fake)
    monkeypatch.setattr(review, "review_context", lambda *a: ({}, receipt, {"construct":{"locked":True}}))
    active = {"identity":("ep","seq","shot"), "department":"anim",
              "task_name":"preComp", "version":"v004", "state":"RUNNING"}
    window = SimpleNamespace(service=SimpleNamespace(
        shots=SimpleNamespace(paths=SimpleNamespace(animation_build_dir=lambda *a: tmp_path,
                                                  artifact_file=lambda root,name: root/name)),
        review_workflow=lambda identity: SimpleNamespace(next_construct_version=lambda *a:"v003")),
        pending_jobs=[], queue_jobs=[active], active_job=active, job_counter=1,
        review_profile_combo=SimpleNamespace(currentText=lambda:"fast_default"),
        queue_table=SimpleNamespace(rowCount=lambda:1), _append_queue_row=lambda job:None)
    job = review.enqueue(window, "snapshot.json")
    assert job["version"] == "v005"
    assert job["delivery_profile"] == "internal"
    assert job["review_cache_policy"] == "ignore_all"
    assert job["reuse_construct"] == "submitted.ma"
    assert job["composition_snapshot"] == "snapshot.json"
    assert job["generate_review"] is True
    assert window.pending_jobs == [job]


def test_review_replaces_cast_with_selected_published_references(monkeypatch):
    from smartlib.dcc.maya import shot_builder
    data = {"cast":{"hero":{"namespace":"ANIM"}, "excluded":{"namespace":"OLD"}},
            "members":[{"instance_id":"hero", "products":{"rend":{"path":"fixed.ma", "version":"v002"}}}],
            "profile":{"representation":"maya"}}
    monkeypatch.setattr(review, "review_context", lambda *a:(data,{},{}))
    monkeypatch.setattr(review, "bake_review_cameras", lambda *a:{})
    monkeypatch.setattr(shot_builder, "ensure_scene_references_loaded", lambda cmds:None)
    monkeypatch.setattr(shot_builder, "_namespace_nodes", lambda cmds,ns:[ns+":root"])
    calls=[]
    cmds = SimpleNamespace(file=lambda *a,**k:calls.append((a,k)),
        referenceQuery=lambda node,**k:True if k.get("isNodeReferenced") else node+"RN")
    result,rows=review.apply_snapshot(cmds, SimpleNamespace(), None, "snapshot.json")
    assert [c[1]["referenceNode"] for c in calls if c[1].get("removeReference")] == ["ANIM:rootRN", "OLD:rootRN"]
    assert calls[-1][0] == ("fixed.ma",)
    assert calls[-1][1]["namespace"] == "hero"
    assert calls[-1][1]["reference"] is True
    assert len(rows)==1 and rows[0]["version"]=="rend v002"
