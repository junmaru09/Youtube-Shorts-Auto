"""The NASA material filter.

Two separate jobs, both learned the hard way by watching a finished video:

- **Rights.** NASA's own work is public domain, but the library also holds
  third-party material, the trademarked insignia, and photographs of
  identifiable people. None of those may appear on a monetised channel.
- **Usability.** A great deal of the library is finished programmes — explainer
  segments with their own title cards, narration and a presenter. They pass
  every rights check and are still unusable as material. A test episode filled
  all seven of its footage slots with "What is a Black Hole | We Asked a NASA
  Expert", NASA logo and presenter included.
"""

import pytest

from tube_auto import nasa


def item(title="Nebula", description="", keywords=(), creator="", center="GSFC"):
    return nasa.NasaItem(
        nasa_id="X",
        media_type="image",
        title=title,
        description=description,
        keywords=list(keywords),
        center=center,
        secondary_creator=creator,
    )


# --- rights -------------------------------------------------------------------


def test_plain_nasa_imagery_is_cleared():
    verdict = nasa.assess_rights(item("The Tarantula Nebula"))
    assert verdict.ok
    assert verdict.credit == "NASA/GSFC"


def test_a_third_party_creator_is_refused():
    verdict = nasa.assess_rights(item(creator="Getty Images"))
    assert not verdict.ok
    assert "third-party" in verdict.reason


def test_a_copyright_notice_in_the_description_is_refused():
    verdict = nasa.assess_rights(item(description="Image © 2024 Some Photographer"))
    assert not verdict.ok


def test_material_credited_to_another_agency_is_refused():
    """ESA, JAXA and STScI all require permission that this project does not have."""
    for holder in ("ESA/Hubble", "STScI", "JAXA"):
        assert not nasa.assess_rights(item(description=f"Credit: {holder}")).ok


def test_the_insignia_is_refused():
    """The meatball and worm are trademarked and not public domain."""
    assert not nasa.assess_rights(item("NASA insignia on the vehicle")).ok


def test_a_person_as_the_subject_is_refused():
    assert not nasa.assess_rights(item("Astronaut portrait")).ok
    assert not nasa.assess_rights(item("Administrator speaks at the event")).ok


def test_an_event_with_people_is_refused():
    """Galas and anniversaries are wall-to-wall identifiable faces."""
    assert not nasa.assess_rights(item("Apollo 11 50th Gala")).ok
    assert not nasa.assess_rights(item("Celebrating 50 Years of Apollo 9")).ok


# --- finished programmes vs material -------------------------------------------


def test_the_explainer_that_broke_the_first_video_is_refused():
    verdict = nasa.assess_rights(item("What is a Black Hole | We Asked a NASA Expert"))
    assert not verdict.ok
    assert "programme" in verdict.reason


def test_a_series_episode_is_refused():
    assert not nasa.assess_rights(item("NASA ScienceCasts: Shedding Light on Black Holes")).ok
    assert not nasa.assess_rights(item("NASA Explorers: Episode 3")).ok


def test_live_coverage_is_refused():
    assert not nasa.assess_rights(item("IXPE Live Launch Coverage")).ok


def test_raw_imagery_is_recognised():
    """Not a requirement — a preference, used to order results."""
    assert nasa.assess_rights(item("Black Hole Simulation")).raw_imagery
    assert nasa.assess_rights(item("Mars Flyover Visualization")).raw_imagery
    assert not nasa.assess_rights(item("Tarantula Nebula")).raw_imagery


# --- URL handling ---------------------------------------------------------------


def test_spaces_in_an_asset_path_are_encoded():
    """Asset URLs are built from item titles, so many contain spaces that
    http.client rejects outright."""
    url = nasa._safe_url("https://images-assets.nasa.gov/video/What is a Black Hole/x~large.mp4")
    assert " " not in url
    assert "%20" in url


def test_the_query_string_is_left_alone():
    """Re-encoding it turned the `+` meaning "space" into a literal plus, and
    every search silently returned zero results."""
    url = nasa._safe_url("https://images-api.nasa.gov/search?q=black+hole&media_type=video")
    assert "q=black+hole" in url
    assert "%2B" not in url


def test_already_encoded_paths_are_not_double_encoded():
    url = nasa._safe_url("https://images-assets.nasa.gov/video/a%20b/c.mp4")
    assert "%2520" not in url


# --- rendition choice ------------------------------------------------------------


def test_large_is_preferred_over_orig():
    """`orig` runs to 200MB+ for a two-minute clip — detail a 1080p timeline
    throws away."""
    urls = [
        "https://x/a~orig.mp4",
        "https://x/a~large.mp4",
        "https://x/a~small.mp4",
    ]
    assert nasa.pick_rendition(urls, "video").endswith("~large.mp4")


def test_stills_prefer_the_original():
    urls = ["https://x/a~small.jpg", "https://x/a~orig.jpg"]
    assert nasa.pick_rendition(urls, "image").endswith("~orig.jpg")


def test_no_matching_rendition_returns_nothing():
    assert nasa.pick_rendition(["https://x/a.srt"], "video") is None


def test_captions_are_found_when_present():
    urls = ["https://x/a~large.mp4", "https://x/a.srt"]
    assert nasa.captions_url(urls).endswith(".srt")
    assert nasa.captions_url(["https://x/a~large.mp4"]) is None


def test_a_catalogue_number_title_is_refused():
    """Release packages are titled with their archive ID and open with a NASA
    slate. Nothing in the metadata says so — three of them reached a finished
    test video, full-frame insignia and all."""
    for title in ("KSC-05-S-00032", "GSFC_20181002_SMBH_m13043", "JSC2019-E-012345"):
        verdict = nasa.assess_rights(item(title))
        assert not verdict.ok, title
        assert "release package" in verdict.reason


def test_a_descriptive_title_survives_even_with_a_catalogue_id():
    """The check is on the title, not the archive id: PIA04206 is titled
    "Black Hole Simulation" and is exactly the material this wants."""
    assert nasa.assess_rights(item("Black Hole Simulation")).ok
    assert nasa.assess_rights(item("Perseverance Rover's First 360 View of Mars")).ok


def test_recurring_series_are_refused():
    for title in (
        "Space to Ground: Place of No Return",
        "Apollo Digest Series: The Lunar Module",
        "Mars Sample Return Media Reel",
        "This Week at NASA",
    ):
        assert not nasa.assess_rights(item(title)).ok, title


def test_a_figure_from_a_paper_is_refused():
    """White backgrounds, page-sized axis labels, several panels in one frame.
    A test episode opened on a four-panel simulation plot with the subtitle
    sitting on white."""
    for title in (
        "Figure 2: Accretion Disk Structure",
        "Side-by-Side Comparison of Two Galaxies",
        "Light Curve of the 2019 Outburst",
        "Composite of Four Hubble Panels",
    ):
        verdict = nasa.assess_rights(item(title))
        assert not verdict.ok, title
        assert "figure" in verdict.reason


def test_an_artist_concept_survives():
    """These are exactly the material this wants: single, striking, ours to use."""
    assert nasa.assess_rights(item("Black Hole With Jet (Artist's Concept)")).ok
    assert nasa.assess_rights(item("Neutron Stars Rip Each Other Apart")).ok
