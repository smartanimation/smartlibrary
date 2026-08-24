from pathlib import Path

from smartlib.delivery import DeliveryInput, DeliveryPlanner, DeliveryProfile, ShotContext
from smartlib.delivery.after_effects import AfterEffectsDeliveryAdapter, _sequence_mappings


PROFILE = Path(__file__).parent / "fixtures" / "dandelione_v003.yml"


def test_ae_relink_script_uses_client_sequence_and_reopen_validation(tmp_path: Path):
    aep = tmp_path / "source.aep"
    frame = tmp_path / "CHA.0278.png"
    aep.write_text("aep", encoding="utf-8")
    frame.write_text("png", encoding="utf-8")
    profile = DeliveryProfile.load(PROFILE)
    context = ShotContext("ep02", "s027", "c001", "preComp", 2)
    plan = DeliveryPlanner(profile).plan(
        context,
        [
            DeliveryInput("aep.primary", "aep", aep, "aep"),
            DeliveryInput(
                "image_sequence.CHA.0278",
                "image_sequence",
                frame,
                "image_sequence",
                metadata={"review_layer": "CHA", "frame": "0278"},
            ),
        ],
        tmp_path / "ELCD",
    )
    for item in plan.items:
        target = plan.package_root / item.destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.source.read_bytes())
    adapter = AfterEffectsDeliveryAdapter(project_config=None)
    project = plan.package_root / plan.items[0].destination

    artifacts = adapter.prepare(plan, tmp_path / "metadata", project, _sequence_mappings(plan))

    relink = artifacts.script.read_text(encoding="utf-8")
    reopen = artifacts.reopen_script.read_text(encoding="utf-8")
    assert "replaceWithSequence" in relink
    assert "ELCD_ep02_s027_c001_CHA_v002.0278.png" in relink
    assert '"relink": true' in relink
    assert '"relink": false' in reopen
    assert "/shot" in reopen
