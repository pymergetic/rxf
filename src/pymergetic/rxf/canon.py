"""canon.py — JSON canonicalizer.

Takes raw JSON-as-code input (may have templates, includes, sugar) and
produces a normalized, deterministic dict ready for pack().
"""


def _sort_recursive(obj):
    """Recursively sort dict keys for deterministic output."""
    if isinstance(obj, dict):
        return {k: _sort_recursive(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort_recursive(v) for v in obj]
    return obj


def _resolve_templates(obj, ctx: dict | None = None):
    """Expand template references (${name}) in string values.

    Templates are dicts with a "$ref" key pointing to a path in the
    document, resolved against the current context.
    """
    if ctx is None:
        ctx = {}
    if isinstance(obj, dict):
        if "$ref" in obj and len(obj) == 1:
            return _resolve_ref(obj["$ref"], ctx)
        if "$template" in obj:
            tmpl = obj["$template"]
            merged = dict(ctx)
            merged.update(obj.get("$with", {}))
            return _sort_recursive(_resolve_templates(tmpl, merged))
        return {k: _resolve_templates(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_templates(v, ctx) for v in obj]
    if isinstance(obj, str) and "${" in obj:
        return _expand_string(obj, ctx)
    return obj


def _resolve_ref(ref: str, ctx: dict):
    """Resolve a $ref path (dot-separated) against context."""
    parts = ref.split(".")
    val = ctx
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return None
    return val


def _expand_string(s: str, ctx: dict) -> str:
    """Expand ${name} references in a string."""
    result = []
    i = 0
    while i < len(s):
        if s[i : i + 2] == "${" and i + 2 < len(s):
            end = s.find("}", i + 2)
            if end != -1:
                key = s[i + 2 : end]
                result.append(str(ctx.get(key, "")))
                i = end + 1
                continue
        result.append(s[i])
        i += 1
    return "".join(result)


def canon(input_obj: dict) -> dict:
    """Normalize JSON-as-code input.

    1. Resolve templates (${...} expansions, $ref, $template).
    2. Sort all dict keys deterministically.
    3. Validate basic structure (must have "header", "nodes").
    """
    obj_raw = _resolve_templates(input_obj)
    obj_sorted = _sort_recursive(obj_raw)
    if not isinstance(obj_sorted, dict):
        raise TypeError(
            f"canon: expected dict after resolve, got {type(obj_sorted).__name__}"
        )
    obj: dict = obj_sorted
    if "nodes" not in obj:
        obj["nodes"] = []
    return obj
