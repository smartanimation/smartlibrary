from smartlib.review.rv import output_targets


def test_output_targets_finds_playblast_sequence_first_frame(tmp_path):
    prefix = tmp_path / "shot010_beauty"
    first = tmp_path / "shot010_beauty.0100.png"
    second = tmp_path / "shot010_beauty.0101.png"
    second.write_text("b", encoding="utf-8")
    first.write_text("a", encoding="utf-8")

    targets = output_targets({"output_path": str(prefix), "compression": "png"})

    assert targets == [first]


def test_output_targets_accepts_existing_movie(tmp_path):
    movie = tmp_path / "review.mov"
    movie.write_text("movie", encoding="utf-8")

    targets = output_targets({"output_path": str(movie), "compression": "png"})

    assert targets == [movie]


def test_output_targets_prefers_package_movie(tmp_path):
    package = tmp_path / "output" / "review" / "layout" / "v001" / "take001"
    movie = package / "mov" / "shot010_layout_v001_take001.mov"
    sequence = package / "image_sequence" / "CHA" / "shot010_layout_CHA_v001_take001_1001.jpg"
    movie.parent.mkdir(parents=True)
    sequence.parent.mkdir(parents=True)
    movie.write_text("movie", encoding="utf-8")
    sequence.write_text("frame", encoding="utf-8")

    targets = output_targets({"package_root": str(package)})

    assert targets == [movie]


def test_output_targets_finds_package_images_folder(tmp_path):
    package = tmp_path / "output" / "review" / "layout" / "CHA" / "v001" / "01"
    frame = package / "images" / "shot010_layout_CHA_v001_01_1001.png"
    frame.parent.mkdir(parents=True)
    frame.write_text("frame", encoding="utf-8")

    targets = output_targets({"package_root": str(package)})

    assert targets == [frame]
