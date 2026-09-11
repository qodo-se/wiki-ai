import pathlib

from routes import version


def test_version_matches_the_version_file():
    on_disk = (pathlib.Path(__file__).parent / "VERSION").read_text().strip()
    assert version._VERSION == on_disk


def test_get_version_returns_dict():
    assert version.get_version() == {"version": version._VERSION}
