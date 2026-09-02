import pytest

import categorize
import db


def _insert_note(note_id, content, path="/", title=""):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notes (id, content, path, title) VALUES (?, ?, ?, ?)",
            (note_id, content, path, title),
        )


# --- _sanitize_segment -------------------------------------------------------


def test_sanitize_segment_lowercases():
    assert categorize._sanitize_segment("Recipes") == "recipes"


def test_sanitize_segment_strips_quotes_backticks_and_slashes():
    assert categorize._sanitize_segment('`"/recipes/"`') == "recipes"


def test_sanitize_segment_takes_first_line_only():
    assert categorize._sanitize_segment("recipes\nextra commentary here") == "recipes"


def test_sanitize_segment_strips_disallowed_characters():
    assert categorize._sanitize_segment("recipes!!ok??") == "recipesok"


def test_sanitize_segment_rejects_blank():
    assert categorize._sanitize_segment("") is None
    assert categorize._sanitize_segment("   ") is None
    assert categorize._sanitize_segment("///") is None


def test_sanitize_segment_truncates_length():
    long_name = "a" * 100
    assert len(categorize._sanitize_segment(long_name)) == categorize.MAX_SEGMENT_LENGTH


# --- _note_label --------------------------------------------------------------


def test_note_label_prefers_title():
    assert categorize._note_label({"title": "Dune", "content": "irrelevant"}) == "Dune"


def test_note_label_falls_back_to_content():
    assert categorize._note_label({"title": "", "content": "a desert planet story"}) == "a desert planet story"


def test_note_label_falls_back_to_untitled_when_empty():
    assert categorize._note_label({"title": "", "content": ""}) == "(untitled)"


# --- _choose_top_level --------------------------------------------------------


def test_choose_top_level_returns_sanitized_segment(monkeypatch):
    monkeypatch.setattr(categorize, "generate_text", lambda prompt: "  `Books`  \n")
    note = {"title": "Dune", "content": "a desert planet story"}
    assert categorize._choose_top_level(note, {}) == "books"


def test_choose_top_level_includes_taxonomy_and_note_in_prompt(monkeypatch):
    captured = {}

    def fake_generate(prompt):
        captured["prompt"] = prompt
        return "books"

    monkeypatch.setattr(categorize, "generate_text", fake_generate)
    note = {"title": "Dune", "content": "a desert planet story"}
    taxonomy = {"books": {"examples": ["1984"], "subs": {}}}
    categorize._choose_top_level(note, taxonomy)

    assert "books" in captured["prompt"]
    assert "1984" in captured["prompt"]
    assert "Dune" in captured["prompt"]
    assert "a desert planet story" in captured["prompt"]


def test_choose_top_level_handles_empty_taxonomy(monkeypatch):
    captured = {}

    def fake_generate(prompt):
        captured["prompt"] = prompt
        return "books"

    monkeypatch.setattr(categorize, "generate_text", fake_generate)
    note = {"title": "Dune", "content": "a desert planet story"}
    assert categorize._choose_top_level(note, {}) == "books"
    assert "none yet" in captured["prompt"]


def test_choose_top_level_returns_none_on_llm_error(monkeypatch):
    def raise_error(prompt):
        raise categorize.LLMError("down")

    monkeypatch.setattr(categorize, "generate_text", raise_error)
    note = {"title": "Dune", "content": "a desert planet story"}
    assert categorize._choose_top_level(note, {}) is None


# --- _choose_subdirectory ------------------------------------------------------


def test_choose_subdirectory_returns_sanitized_segment(monkeypatch):
    monkeypatch.setattr(categorize, "generate_text", lambda prompt: "Fiction")
    note = {"title": "Dune", "content": "a desert planet story"}
    assert categorize._choose_subdirectory(note, "books", {}) == "fiction"


def test_choose_subdirectory_returns_none_for_none_response(monkeypatch):
    monkeypatch.setattr(categorize, "generate_text", lambda prompt: "none")
    note = {"title": "Dune", "content": "a desert planet story"}
    assert categorize._choose_subdirectory(note, "books", {}) is None


def test_choose_subdirectory_returns_none_on_llm_error(monkeypatch):
    def raise_error(prompt):
        raise categorize.LLMError("down")

    monkeypatch.setattr(categorize, "generate_text", raise_error)
    note = {"title": "Dune", "content": "a desert planet story"}
    assert categorize._choose_subdirectory(note, "books", {}) is None


def test_choose_subdirectory_includes_parent_and_existing_subs(monkeypatch):
    captured = {}

    def fake_generate(prompt):
        captured["prompt"] = prompt
        return "fiction"

    monkeypatch.setattr(categorize, "generate_text", fake_generate)
    note = {"title": "Dune", "content": "a desert planet story"}
    subs = {"fiction": {"examples": ["1984"]}}
    categorize._choose_subdirectory(note, "books", subs)

    assert "/books" in captured["prompt"]
    assert "fiction" in captured["prompt"]
    assert "1984" in captured["prompt"]


# --- _extract_json -----------------------------------------------------------


def test_extract_json_plain():
    assert categorize._extract_json('{"a": "b"}') == {"a": "b"}


def test_extract_json_markdown_fenced():
    text = '```json\n{"a": "b"}\n```'
    assert categorize._extract_json(text) == {"a": "b"}


def test_extract_json_invalid_returns_none():
    assert categorize._extract_json("not json at all") is None


# --- _consolidate_segments -----------------------------------------------------


def test_consolidate_segments_skips_llm_call_for_single_name(monkeypatch):
    called = False

    def fake_generate(prompt):
        nonlocal called
        called = True
        return "{}"

    monkeypatch.setattr(categorize, "generate_text", fake_generate)
    result = categorize._consolidate_segments(["books"], "top-level directory names")
    assert result == {"books": "books"}
    assert not called


def test_consolidate_segments_merges_duplicates(monkeypatch):
    monkeypatch.setattr(
        categorize,
        "generate_text",
        lambda prompt: '{"names": "names", "people": "names"}',
    )
    result = categorize._consolidate_segments(["names", "people"], "top-level directory names")
    assert result == {"names": "names", "people": "names"}


def test_consolidate_segments_falls_back_to_identity_on_bad_json(monkeypatch):
    monkeypatch.setattr(categorize, "generate_text", lambda prompt: "not json")
    result = categorize._consolidate_segments(["books", "recipes"], "top-level directory names")
    assert result == {"books": "books", "recipes": "recipes"}


def test_consolidate_segments_falls_back_on_llm_error(monkeypatch):
    def raise_error(prompt):
        raise categorize.LLMError("down")

    monkeypatch.setattr(categorize, "generate_text", raise_error)
    result = categorize._consolidate_segments(["books", "recipes"], "top-level directory names")
    assert result == {"books": "books", "recipes": "recipes"}


# --- reorganize_notes (integration, mocked Ollama) ---------------------------


def test_reorganize_notes_too_few_notes_is_a_noop():
    _insert_note("only-one", "just one note")
    result = categorize.reorganize_notes()
    assert result == {"classified": 0, "moved": 0, "unchanged": 1, "failed": 0}


def test_reorganize_notes_classifies_with_subdirectories(monkeypatch):
    _insert_note("book-a", "Dune: a desert planet story", title="Dune")
    _insert_note("book-b", "1984: a dystopian future", title="1984")
    _insert_note("recipe-a", "Pad Thai: rice noodles and shrimp", title="Pad Thai")
    _insert_note("lonely", "A note already filed correctly", path="/misc", title="Lonely")

    def fake_generate(prompt):
        # Anchor on "Note title: X" (unique to the note actually being
        # classified) rather than a bare substring — taxonomy example
        # listings can otherwise leak an earlier note's title into a later
        # note's prompt and produce a false match.
        if "Here is a list of" in prompt:
            if "top-level directory names" in prompt:
                return '{"books": "books", "recipes": "recipes", "misc": "misc"}'
            return "{}"  # single-item subdirectory consolidations never reach here
        if "Note title: Dune" in prompt or "Note title: 1984" in prompt:
            return "fiction" if "Existing subdirectories" in prompt else "books"
        if "Note title: Pad Thai" in prompt:
            return "none" if "Existing subdirectories" in prompt else "recipes"
        if "Note title: Lonely" in prompt:
            return "none" if "Existing subdirectories" in prompt else "misc"
        raise AssertionError(f"unexpected prompt: {prompt}")

    monkeypatch.setattr(categorize, "generate_text", fake_generate)

    result = categorize.reorganize_notes()

    assert result == {"classified": 4, "moved": 3, "unchanged": 1, "failed": 0}

    notes = categorize._fetch_all_notes()
    assert notes["book-a"]["path"] == "/books/fiction"
    assert notes["book-b"]["path"] == "/books/fiction"
    assert notes["recipe-a"]["path"] == "/recipes"
    assert notes["lonely"]["path"] == "/misc"  # already correctly filed, untouched


def test_reorganize_notes_consolidates_top_level_duplicates(monkeypatch):
    _insert_note("person-a", "Emma: a common name meaning universal", title="Emma")
    _insert_note("person-b", "Liam: a popular name of Irish origin", title="Liam")

    def fake_generate(prompt):
        if "Here is a list of" in prompt:
            return '{"names": "names", "people": "names"}'
        if "Note title: Emma" in prompt:
            return "none" if "Existing subdirectories" in prompt else "names"
        if "Note title: Liam" in prompt:
            return "none" if "Existing subdirectories" in prompt else "people"
        raise AssertionError(f"unexpected prompt: {prompt}")

    monkeypatch.setattr(categorize, "generate_text", fake_generate)
    categorize.reorganize_notes()

    notes = categorize._fetch_all_notes()
    assert notes["person-a"]["path"] == "/names"
    assert notes["person-b"]["path"] == "/names"  # "people" consolidated into "names"


def test_reorganize_notes_folds_subdirectory_named_like_its_parent(monkeypatch):
    _insert_note("note-a", "A note about literature", title="Classics")
    _insert_note("note-b", "A recipe for tacos", title="Tacos")

    def fake_generate(prompt):
        if "Here is a list of" in prompt:
            return "{}"
        if "Note title: Classics" in prompt:
            return "literature"  # same for both top-level and subdirectory
        if "Note title: Tacos" in prompt:
            return "none" if "Existing subdirectories" in prompt else "recipes"
        raise AssertionError(f"unexpected prompt: {prompt}")

    monkeypatch.setattr(categorize, "generate_text", fake_generate)
    categorize.reorganize_notes()

    notes = categorize._fetch_all_notes()
    assert notes["note-a"]["path"] == "/literature"  # not /literature/literature


def test_reorganize_notes_scopes_subdirectory_consolidation_per_parent(monkeypatch):
    _insert_note("tech-a", "Docker: containers explained", title="Docker")
    _insert_note("tech-b", "Python: a programming language", title="Python")
    _insert_note("travel-a", "Rome: a travel guide", title="Rome")
    _insert_note("travel-b", "Tokyo: a travel guide", title="Tokyo")

    captured_sub_consolidations = {}

    def fake_generate(prompt):
        if "Here is a list of" in prompt:
            if "top-level directory names" in prompt:
                return '{"technology": "technology", "travel": "travel"}'
            if "/technology" in prompt:
                captured_sub_consolidations["technology"] = prompt
                return '{"guides": "guides", "basics": "basics"}'
            if "/travel" in prompt:
                captured_sub_consolidations["travel"] = prompt
                return '{"guides": "guides", "tips": "tips"}'
        if "Note title: Docker" in prompt:
            return "guides" if "Existing subdirectories" in prompt else "technology"
        if "Note title: Python" in prompt:
            return "basics" if "Existing subdirectories" in prompt else "technology"
        if "Note title: Rome" in prompt:
            return "guides" if "Existing subdirectories" in prompt else "travel"
        if "Note title: Tokyo" in prompt:
            return "tips" if "Existing subdirectories" in prompt else "travel"
        raise AssertionError(f"unexpected prompt: {prompt}")

    monkeypatch.setattr(categorize, "generate_text", fake_generate)
    categorize.reorganize_notes()

    # each parent's subdirectory-consolidation call must only ever see that
    # parent's own subdirectory names — never the other parent's.
    assert "basics" in captured_sub_consolidations["technology"]
    assert "tips" not in captured_sub_consolidations["technology"]
    assert "tips" in captured_sub_consolidations["travel"]
    assert "basics" not in captured_sub_consolidations["travel"]

    notes = categorize._fetch_all_notes()
    assert notes["tech-a"]["path"] == "/technology/guides"
    assert notes["travel-a"]["path"] == "/travel/guides"


def test_reorganize_notes_survives_partial_top_level_failure(monkeypatch):
    _insert_note("book-a", "Dune: a desert planet story", title="Dune")
    _insert_note("book-b", "1984: a dystopian future", title="1984")

    def fake_generate(prompt):
        if "Here is a list of" in prompt:
            return "{}"
        if "Note title: Dune" in prompt:
            return "none" if "Existing subdirectories" in prompt else "books"
        if "Note title: 1984" in prompt:
            raise categorize.LLMError("down")
        raise AssertionError(f"unexpected prompt: {prompt}")

    monkeypatch.setattr(categorize, "generate_text", fake_generate)

    result = categorize.reorganize_notes()

    # "unchanged" counts every note whose path didn't change, which includes
    # the one that failed to classify (it necessarily stays put) — "failed"
    # is a breakdown of *why*, not a disjoint bucket.
    assert result == {"classified": 1, "moved": 1, "unchanged": 1, "failed": 1}
    notes = categorize._fetch_all_notes()
    assert notes["book-a"]["path"] == "/books"
    assert notes["book-b"]["path"] == "/"  # left untouched after classify failure


def test_reorganize_notes_survives_subdirectory_failure(monkeypatch):
    _insert_note("book-a", "Dune: a desert planet story", title="Dune")
    _insert_note("book-b", "1984: a dystopian future", title="1984")

    def fake_generate(prompt):
        if "Here is a list of" in prompt:
            return "{}"
        if "Existing subdirectories" in prompt:
            if "Dune" in prompt:
                raise categorize.LLMError("down")
            return "none"
        return "books"

    monkeypatch.setattr(categorize, "generate_text", fake_generate)

    result = categorize.reorganize_notes()

    assert result == {"classified": 2, "moved": 2, "unchanged": 0, "failed": 0}
    notes = categorize._fetch_all_notes()
    assert notes["book-a"]["path"] == "/books"  # subdirectory failure -> filed at top level
    assert notes["book-b"]["path"] == "/books"


def test_reorganize_notes_rejects_concurrent_calls():
    _insert_note("only-one", "just one note")
    categorize._reorganize_lock.acquire()
    try:
        with pytest.raises(categorize.ReorganizeInProgress):
            categorize.reorganize_notes()
    finally:
        categorize._reorganize_lock.release()
