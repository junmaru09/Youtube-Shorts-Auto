"""Sprite export from a PSDTool-style PSD, on a synthetic file."""

import importlib.util
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageDraw

psd_tools = pytest.importorskip("psd_tools")
from psd_tools import PSDImage  # noqa: E402
from psd_tools.api.layers import Group, PixelLayer  # noqa: E402

spec = importlib.util.spec_from_file_location("sprite_export", Path(__file__).parent.parent / "tools" / "sprite_export.py")
sprite_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sprite_export)


def _blob(colour, box, size=(200, 300)):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle(box, fill=colour)
    return im


@pytest.fixture
def psd_path(tmp_path):
    """A 立ち絵-shaped PSD: body, two eyes, two mouths, PSDTool prefixes."""
    # frompil writes the legacy (macroman) name and chokes on Japanese; the
    # name setter falls back to "?" there and puts the real name in the
    # Unicode block, which is how Photoshop stores these anyway.
    def named(layer, name):
        layer.name = name
        return layer

    psd = PSDImage.new("RGBA", (200, 300))
    body = named(Group.new(psd, name="g"), "体")
    named(PixelLayer.frompil(_blob((0, 200, 0, 255), (40, 100, 160, 290)), body, name="l"), "服1")
    eyes = named(Group.new(psd, name="g"), "!目")
    named(PixelLayer.frompil(_blob((0, 0, 0, 255), (70, 60, 130, 70)), eyes, name="l"), "*通常")
    named(PixelLayer.frompil(_blob((0, 0, 255, 255), (70, 55, 130, 75)), eyes, name="l"), "にっこり")
    mouth = named(Group.new(psd, name="g"), "!口")
    named(PixelLayer.frompil(_blob((255, 0, 0, 255), (90, 85, 110, 90)), mouth, name="l"), "*むふ")
    named(PixelLayer.frompil(_blob((255, 0, 0, 255), (90, 82, 110, 98)), mouth, name="l"), "ほあー")
    out = tmp_path / "chara.psd"
    psd.save(out)
    return out


RECIPE = {
    "size": 150,
    "base": ["体/服1"],
    "expressions": {"normal": ["目/通常"], "happy": ["にっこり"]},
    "mouth": {"closed": "口/むふ", "open": "口/ほあー"},
}


def test_paths_strip_psdtool_prefixes(psd_path):
    names = {p for _, p in sprite_export._paths(PSDImage.open(psd_path)).values()}
    assert "口/むふ" in names and "目/通常" in names


def test_export_writes_every_expression_and_mouth(psd_path, tmp_path):
    written = sprite_export.export(PSDImage.open(psd_path), RECIPE, tmp_path / "out")
    names = sorted(p.name for p in written)
    assert names == ["happy_closed.png", "happy_open.png", "normal_closed.png", "normal_open.png"]
    sizes = {Image.open(p).size for p in written}
    assert len(sizes) == 1, "all frames share one crop"
    (w, h), = sizes
    assert max(w, h) == 150


def test_only_the_chosen_layers_show(psd_path, tmp_path):
    written = sprite_export.export(PSDImage.open(psd_path), RECIPE, tmp_path / "out")
    import numpy as np

    def count(path, rgb):
        arr = np.asarray(Image.open(path).convert("RGBA"))
        return int((np.all(arr[..., :3] == rgb, axis=-1) & (arr[..., 3] > 0)).sum())

    normal = [p for p in written if p.name == "normal_closed.png"][0]
    happy = [p for p in written if p.name == "happy_closed.png"][0]
    assert count(normal, (0, 0, 255)) == 0     # にっこり is not in normal
    assert count(happy, (0, 0, 255)) > 0
    assert count(normal, (0, 200, 0)) > 0      # the body is in every frame


def test_ambiguous_and_missing_names_are_refused(psd_path):
    paths = sprite_export._paths(PSDImage.open(psd_path))
    with pytest.raises(SystemExit):
        sprite_export._resolve(paths, "存在しない")
    assert sprite_export._resolve(paths, "ほあー") == sprite_export._resolve(paths, "口/ほあー")


def test_recipe_roundtrips_through_yaml(tmp_path):
    text = yaml.safe_dump(RECIPE, allow_unicode=True)
    assert yaml.safe_load(text) == RECIPE
