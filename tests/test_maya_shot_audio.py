from pathlib import Path

from smartlib.dcc.maya.shot_builder import _load_shot_audio


def test_load_shot_audio_creates_node_at_cut_in(tmp_path: Path) -> None:
    audio = tmp_path / "production" / "shot" / "data" / "audio" / "v001" / "c001.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"wav")

    class Cmds:
        def __init__(self):
            self.sound_call = None

        def objExists(self, _name):
            return False

        def sound(self, **kwargs):
            self.sound_call = kwargs
            return kwargs["name"]

    cmds = Cmds()
    node = _load_shot_audio(
        cmds,
        tmp_path,
        {
            "editorial": {"cut_in": 278, "cut_range": [1001, 1134]},
            "audio": {
                "path": "production/shot/data/audio/v001/c001.wav",
                "cut_in": 278,
            },
        },
    )

    assert node == "smartEditorialAudio"
    assert cmds.sound_call == {
        "file": str(audio),
        "offset": 1001,
        "name": "smartEditorialAudio",
    }
