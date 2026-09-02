import json
import re
import sys
import threading

from db import connect
from llm import LLMError, generate_text

SEGMENT_CHARSET = re.compile(r"[^a-z0-9_-]")
MAX_SEGMENT_LENGTH = 40
MAX_EXAMPLES_PER_NODE = 2
MAX_CONTENT_CHARS = 400

_reorganize_lock = threading.Lock()


class ReorganizeInProgress(Exception):
    pass


class ClassificationFailed(Exception):
    pass


def _fetch_all_notes() -> dict[str, dict]:
    with connect() as db:
        rows = db.execute("SELECT id, content, path, title, updated_at FROM notes ORDER BY id").fetchall()
    return {r[0]: {"content": r[1], "path": r[2], "title": r[3], "updated_at": r[4]} for r in rows}


def _note_label(note: dict) -> str:
    return (note["title"] or note["content"][:60]).strip() or "(untitled)"


def _sanitize_segment(raw: str) -> str | None:
    candidate = raw.strip().splitlines()[0].strip() if raw and raw.strip() else ""
    candidate = candidate.strip("`\"' /").lower()
    candidate = SEGMENT_CHARSET.sub("", candidate)
    candidate = candidate[:MAX_SEGMENT_LENGTH]
    return candidate or None


def _format_examples(node: dict) -> str:
    examples = node.get("examples", [])
    return ", ".join(f'"{t}"' for t in examples) if examples else "(no examples yet)"


def _choose_top_level(note: dict, taxonomy: dict[str, dict]) -> str | None:
    if not taxonomy:
        listing = "(none yet — this is the first note)"
    else:
        listing = "\n".join(f"- {name}: {_format_examples(node)}" for name, node in taxonomy.items())
    title = (note["title"] or "(untitled)").strip()
    content = note["content"][:MAX_CONTENT_CHARS].strip()
    prompt = (
        "You are organizing a personal wiki into top-level directories.\n\n"
        f"Existing top-level directories, with example note titles already "
        f"filed there:\n{listing}\n\n"
        f"Note title: {title}\n"
        f"Note content:\n{content}\n\n"
        "Which top-level directory best fits this note? If one of the "
        "existing directories above is a genuinely good topical fit, "
        "respond with its exact name. Otherwise propose ONE new short "
        "lowercase directory name (a single word or short hyphenated "
        "phrase, no slashes). Respond with ONLY the directory name, "
        "nothing else."
    )
    try:
        raw = generate_text(prompt)
    except LLMError as e:
        print(f"warning: failed to classify top-level directory: {e}", file=sys.stderr)
        return None
    return _sanitize_segment(raw)


def _choose_subdirectory(note: dict, top_name: str, subs: dict[str, dict]) -> str | None:
    listing = (
        "\n".join(f"- {name}: {_format_examples(node)}" for name, node in subs.items())
        if subs
        else "(none yet)"
    )
    title = (note["title"] or "(untitled)").strip()
    content = note["content"][:MAX_CONTENT_CHARS].strip()
    prompt = (
        f"You are organizing notes within the top-level directory "
        f"'/{top_name}'.\n\n"
        f"Existing subdirectories under '/{top_name}', with example note "
        f"titles already filed there:\n{listing}\n\n"
        f"Note title: {title}\n"
        f"Note content:\n{content}\n\n"
        f"Does this note need a subdirectory within '/{top_name}', or does "
        f"it belong directly in '/{top_name}'? If an existing subdirectory "
        "above is a genuinely good fit, respond with its exact name. If it "
        "needs a new subdirectory, propose ONE new short lowercase name (no "
        "slashes). If no subdirectory is needed, respond with exactly: "
        "none\nRespond with ONLY the subdirectory name or 'none', nothing "
        "else."
    )
    try:
        raw = generate_text(prompt)
    except LLMError as e:
        print(
            f"warning: failed to classify subdirectory under /{top_name}: {e}",
            file=sys.stderr,
        )
        raise ClassificationFailed from e
    if raw.strip().strip("`\"' ").lower().startswith("none"):
        return None
    return _sanitize_segment(raw)


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    for candidate in (text, re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _consolidate_segments(names: list[str], context: str) -> dict[str, str]:
    identity = {name: name for name in names}
    unique_names = sorted(set(names))
    if len(unique_names) < 2:
        return identity

    listing = "\n".join(f"- {name}" for name in unique_names)
    prompt = (
        f"Here is a list of {context}:\n\n{listing}\n\n"
        "Some may be near-duplicates referring to the same thing (e.g. "
        '"recipes" and "cooking" should become one). Respond with ONLY a '
        "JSON object mapping each original name to its final, consolidated "
        "name. Every input name must appear as a key. Names that are "
        'already fine should map to themselves. Example: '
        '{"recipes": "recipes", "cooking": "recipes"}'
    )
    try:
        raw = generate_text(prompt)
    except LLMError as e:
        print(f"warning: failed to consolidate {context}: {e}", file=sys.stderr)
        return identity

    parsed = _extract_json(raw)
    if parsed is None:
        print(f"warning: could not parse consolidation response for {context}: {raw!r}", file=sys.stderr)
        return identity

    result = dict(identity)
    for name in unique_names:
        final = parsed.get(name)
        if isinstance(final, str):
            sanitized = _sanitize_segment(final)
            if sanitized:
                result[name] = sanitized
    return result


def reorganize_notes() -> dict:
    if not _reorganize_lock.acquire(blocking=False):
        raise ReorganizeInProgress("a reorganize run is already in progress")
    try:
        notes = _fetch_all_notes()
        if not notes:
            return {"classified": 0, "moved": 0, "unchanged": len(notes), "failed": 0}

        taxonomy: dict[str, dict] = {}
        proposals: dict[str, tuple[str, str | None]] = {}
        failed = 0

        for note_id, note in notes.items():
            top = _choose_top_level(note, taxonomy)
            if top is None:
                failed += 1
                continue
            node = taxonomy.setdefault(top, {"examples": [], "subs": {}})

            try:
                  sub = _choose_subdirectory(note, top, node["subs"])
              except ClassificationFailed:
                  failed += 1
                  continue
            if sub == top:
                sub = None  # redundant self-named subdirectory folds into the parent

            label = _note_label(note)
            if sub:
                sub_node = node["subs"].setdefault(sub, {"examples": []})
                target_examples = sub_node["examples"]
            else:
                target_examples = node["examples"]
            if len(target_examples) < MAX_EXAMPLES_PER_NODE:
                target_examples.append(label)

            proposals[note_id] = (top, sub)

        # Consolidate top-level directory names first (a small, bounded set)...
        top_mapping = _consolidate_segments(
            [top for top, _ in proposals.values()], "top-level directory names"
        )

        # ...then consolidate subdirectory names within each *final* top-level
        # directory independently. Scoping consolidation to within a single
        # parent (rather than across the whole path, as an earlier attempt
        # did) is what prevents unrelated topics from merging just because
        # they happen to share a subdirectory word under different parents.
        subs_by_top: dict[str, list[str]] = {}
        for top, sub in proposals.values():
            if sub:
                subs_by_top.setdefault(top_mapping.get(top, top), []).append(sub)

        sub_mappings = {
            final_top: _consolidate_segments(subs, f"subdirectories within '/{final_top}'")
            for final_top, subs in subs_by_top.items()
        }

        moved, unchanged = 0, 0
        updates = []
        for note_id, note in notes.items():
            if note_id not in proposals:
                unchanged += 1
                continue
            top, sub = proposals[note_id]
            final_top = top_mapping.get(top, top)
            final_sub = sub_mappings.get(final_top, {}).get(sub, sub) if sub else None
            if final_sub == final_top:
                final_sub = None
            final_path = f"/{final_top}/{final_sub}" if final_sub else f"/{final_top}"

            if final_path == note["path"]:
                unchanged += 1
            else:
                updates.append((final_path, note_id, note["updated_at"]))
                moved += 1

        if updates:
            with connect() as db:
                db.executemany(
                    "UPDATE notes SET path = ?, updated_at = datetime('now') WHERE id = ? AND updated_at = ?", updates
                )

        return {
            "classified": len(proposals),
            "moved": moved,
            "unchanged": unchanged,
            "failed": failed,
        }
    finally:
        _reorganize_lock.release()
