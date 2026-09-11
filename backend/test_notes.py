import pytest
from pydantic import ValidationError

from routes.notes import NoteBody


def test_note_body_normalizes_path_on_construction():
    assert NoteBody(content="x", path="/recipe/").path == "/recipe"


def test_equivalent_paths_normalize_to_the_same_string():
    a = NoteBody(content="x", path="/recipe/")
    b = NoteBody(content="x", path="recipe ")
    c = NoteBody(content="x", path="//recipe//")
    assert a.path == b.path == c.path == "/recipe"


def test_note_body_defaults_to_root_path():
    assert NoteBody(content="x").path == "/"


def test_note_body_still_rejects_disallowed_characters():
    with pytest.raises(ValidationError):
        NoteBody(content="x", path="/recipe;drop")
