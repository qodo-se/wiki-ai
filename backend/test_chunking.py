import chunking


def test_chunk_content_splits_on_blank_lines():
    content = "# Title\n\nFirst paragraph.\n\nSecond paragraph."
    assert chunking.chunk_content(content) == ["# Title", "First paragraph.", "Second paragraph."]


def test_chunk_content_title_is_its_own_chunk():
    # The strongest-signal case found in testing: a markdown heading followed
    # by a blank line becomes its own concentrated chunk, not diluted by the
    # body text that follows it.
    content = "# Electric Vehicle Review\n\nTest drove a new EV this weekend."
    chunks = chunking.chunk_content(content)
    assert chunks[0] == "# Electric Vehicle Review"
    assert len(chunks) == 2


def test_chunk_content_single_block_with_no_blank_lines():
    content = "just one line, no blank lines anywhere"
    assert chunking.chunk_content(content) == [content]


def test_chunk_content_drops_empty_blocks_from_extra_blank_lines():
    content = "# Title\n\n\n\nBody text."
    assert chunking.chunk_content(content) == ["# Title", "Body text."]


def test_chunk_content_blank_content_yields_no_chunks():
    assert chunking.chunk_content("") == []
    assert chunking.chunk_content("   \n\n  ") == []


def test_chunk_point_id_is_deterministic_and_distinct_per_chunk():
    a = chunking.chunk_point_id("note-1", 0)
    b = chunking.chunk_point_id("note-1", 0)
    c = chunking.chunk_point_id("note-1", 1)
    d = chunking.chunk_point_id("note-2", 0)
    assert a == b  # same note/index always maps to the same point id
    assert len({a, c, d}) == 3  # different note or index -> different id


def test_chunk_point_id_is_a_valid_uuid():
    import uuid

    uuid.UUID(chunking.chunk_point_id("note-1", 0))  # raises if not a valid UUID
