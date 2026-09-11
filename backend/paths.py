def normalize_path(raw: str) -> str:
    # Canonicalizes equivalent inputs (a trailing slash, stray whitespace, a
    # missing leading slash, doubled slashes) to the same string. The home page's
    # "by path" view groups notes by exact string match, so without this, notes a
    # user considers "the same path" (e.g. "/recipe" vs "/recipe/") land in
    # separate groups. Shared by routes.notes (new writes) and db's path-fixup
    # migration (existing rows) so both apply identical semantics.
    segments = [seg.strip() for seg in raw.split("/")]
    segments = [seg for seg in segments if seg]
    return "/" + "/".join(segments) if segments else "/"
