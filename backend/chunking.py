import re
import uuid

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def chunk_content(content: str) -> list[str]:
    # Splitting on blank lines naturally isolates a markdown "# Title" line as
    # its own chunk (there's a blank line after it in every note this app's
    # editor produces) — the strongest-signal chunk in testing, since it
    # isn't diluted by averaging in the rest of the note's content.
    blocks = _PARAGRAPH_SPLIT.split(content.strip())
    return [b.strip() for b in blocks if b.strip()]


def chunk_point_id(note_id: str, chunk_index: int) -> str:
    # Qdrant point ids must be an unsigned int or a UUID — a plain
    # f"{note_id}:{chunk_index}" string isn't valid. Deterministic (not
    # uuid4-random) so re-embedding a note overwrites the same points
    # instead of leaking duplicates, with no separate id bookkeeping needed
    # anywhere else.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{note_id}/chunk/{chunk_index}"))
