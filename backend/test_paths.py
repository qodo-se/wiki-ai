import pytest

from paths import normalize_path


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/recipe", "/recipe"),
        ("/recipe/", "/recipe"),
        ("/recipe ", "/recipe"),
        (" /recipe", "/recipe"),
        ("recipe", "/recipe"),
        ("//recipe//", "/recipe"),
        ("////////recipe", "/recipe"),
        ("/recipe//thai", "/recipe/thai"),
        ("/recipe/ thai ", "/recipe/thai"),
        ("/", "/"),
        ("", "/"),
        ("   ", "/"),
        ("///", "/"),
    ],
)
def test_normalize_path(raw, expected):
    assert normalize_path(raw) == expected
