"""Picture packs: the catalogue, the per-video licence limit, credits."""

import textwrap

import pytest
import yaml
from PIL import Image

from tube_auto.canvas import catalogue
from tube_auto.models import Chapter, Line
from tube_auto.stages import script as script_stage


@pytest.fixture
def assets(tmp_path, monkeypatch):
    """An assets tree with one limited pack beside the built-in Noto one."""
    (tmp_path / "illustrations").mkdir()
    (tmp_path / "packs").mkdir()
    for name in ("surprised_person", "bell_person"):
        Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(tmp_path / "illustrations" / f"{name}.png")
    (tmp_path / "packs" / "irasutoya.yaml").write_text(textwrap.dedent("""
        id: irasutoya
        title: いらすとや
        licence: 20点まで無料
        credit: "イラスト: いらすとや"
        max_per_video: 2
        pictures:
          surprised_person: [surprised_person, 驚く人]
          bell_person: [bell_person, 鐘をつく人]
    """), encoding="utf-8")
    catalogue.load_packs.cache_clear()
    monkeypatch.setattr("tube_auto.paths.ASSETS_DIR", tmp_path)
    yield tmp_path
    catalogue.load_packs.cache_clear()


def test_a_pack_joins_the_catalogue(assets):
    pictures = catalogue.pictures(assets)
    assert pictures["surprised_person"][1] == "驚く人"
    assert pictures["bell"][2].id == "noto"          # the built-in pack is still there
    assert ("驚く人", "surprised_person") in catalogue.japanese_index(assets)


def test_only_limited_packs_are_counted(assets):
    assert catalogue.limited(assets) == {"irasutoya": 2}
    counts = catalogue.count_by_pack(assets, ["surprised_person", "surprised_person", "bell", "ruler"])
    assert counts == {"irasutoya": 1}                # distinct pictures, and Noto is not counted


def test_the_licence_limit_is_enforced_on_a_script(assets):
    def chapter(names):
        return Chapter(key="k", title="t", lines=[
            Line("explainer", "x", "x", ops=[{"op": "place", "element": n}]) for n in names])

    assert script_stage.check_licences([chapter(["surprised_person", "bell_person"])]) == []
    over = script_stage.check_licences([chapter(["surprised_person", "bell_person"]),
                                        chapter(["surprised_person"])])
    assert over == []                                # the same picture twice is one illustration

    (assets / "packs" / "irasutoya.yaml").write_text(
        yaml.safe_dump({"id": "irasutoya", "credit": "c", "max_per_video": 1,
                        "pictures": {"surprised_person": ["surprised_person", "驚く人"],
                                     "bell_person": ["bell_person", "鐘をつく人"]}}, allow_unicode=True),
        encoding="utf-8")
    catalogue.load_packs.cache_clear()
    over = script_stage.check_licences([chapter(["surprised_person", "bell_person"])])
    assert over and "2種類" in over[0] and "1種類" in over[0]


def test_credits_name_only_the_packs_used(assets):
    assert catalogue.credits(assets, ["bell", "ruler"]) == ["Illustrations: Noto Emoji (Apache-2.0)"]
    assert "イラスト: いらすとや" in catalogue.credits(assets, ["surprised_person", "bell"])


def test_pictures_used_reads_every_op_shape():
    chapters = [Chapter(key="k", title="t", lines=[
        Line("explainer", "a", "a", ops=[{"op": "place", "element": "bell"}]),
        Line("explainer", "b", "b", ops=[{"op": "add", "element": "ice_block", "near": "x"}]),
        Line("explainer", "c", "c", ops=[{"op": "steps", "items": ["bell:叩く", "note:鳴る"]}]),
        Line("explainer", "d", "d", ops=[{"op": "compare", "elements": ["sun", "moon"]}]),
    ])]
    assert script_stage.pictures_used(chapters) == ["bell", "ice_block", "bell", "note", "sun", "moon"]
