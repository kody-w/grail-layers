"""shape_lens: the ONE lens through which a Brainstem's shape changes (living-holograms §3.2).

A Brainstem is a hologram: its identity is an append-only rapp/1 stream, its present is the head, and
it changes only by passing through a lens. This lens is pure: no model, clock, randomness, network or
files. `perform(object, params)` returns the next shape object or raises ValueError (nothing appended).
Replay: anyone holding the input shape, this source and the tile must get the same object back.

The shape object (`brainstem-shape/1`):
  {"schema": "brainstem-shape/1",
   "grail": {"sha256": <hex64 of brainstem.py>, "bytes": <int>},   # the safe mode, never changes here
   "safe_mode": false,
   "parts": [ {"id": "@publisher/name", "kind": "layer", "point": "provider",
               "file": "responses_models.py", "sha256": <hex64>, "locator": "https://..."} ]}
Parts are applied in list order. Rules the lens refuses to break (Dynamics 365 publisher lessons):
  - ids are publisher-scoped `@publisher/name`; a bare name is refused;
  - one occupant per extension point (provider | auth | storage | agents | transport);
  - a part is pinned by sha256; the locator is discovery only;
  - kinds: `layer` (in-process, over brainstem.py) and `kernel` (a whole Brainstem replacing brainstem.py,
    e.g. the LisPy host; it must pass the same oracle). `sidecar` is reserved and refused until gated.
"""
import re

SCHEMA = "brainstem-shape/1"
POINTS = ("provider", "auth", "storage", "agents", "transport", "kernel")
KINDS = ("layer", "kernel")  # kernel: a whole Brainstem that replaces brainstem.py (LisPy host); it occupies the kernel point
_ID = re.compile(r"^@[a-z0-9]+(?:-[a-z0-9]+)*/[a-z0-9_]+(?:[-.][a-z0-9_]+)*$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.py$")

description = (
    "Change the shape of this Brainstem: add or remove a published part (layer) over the unchanged grail, "
    "switch safe mode, or set the whole part list. Pure; the supervisor gates the result before it goes live."
)
parameters = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "remove", "safe_mode", "set"]},
        "part": {"type": "object", "description": "for add: {id, kind, point, file, sha256, locator}"},
        "id": {"type": "string", "description": "for remove: the part id"},
        "on": {"type": "boolean", "description": "for safe_mode"},
        "parts": {"type": "array", "description": "for set: the full ordered part list"},
        "safe_mode": {"type": "boolean", "description": "for set: optional, restore safe mode too"},
    },
    "required": ["action"],
}


def _check_part(p):
    if not isinstance(p, dict) or set(p) != {"id", "kind", "point", "file", "sha256", "locator"}:
        raise ValueError("a part is exactly {id, kind, point, file, sha256, locator}")
    if not _ID.match(p["id"]):
        raise ValueError(f"part id {p['id']!r} must be publisher-scoped: @publisher/name")
    if p["kind"] not in KINDS:
        raise ValueError(f"part kind {p['kind']!r} is not available yet (today: {', '.join(KINDS)})")
    if (p["kind"] == "kernel") != (p["point"] == "kernel"):
        raise ValueError("a kernel part occupies the kernel point, and only a kernel part may")
    if p["point"] not in POINTS:
        raise ValueError(f"part point {p['point']!r} is not one of {', '.join(POINTS)}")
    if not _FILE.match(p["file"]):
        raise ValueError("part file must be a single .py file name")
    if not _HEX64.match(p["sha256"]):
        raise ValueError("part sha256 must be 64 lowercase hex")
    if not (isinstance(p["locator"], str) and p["locator"].startswith("https://")):
        raise ValueError("part locator must be an https URL")
    return {k: p[k] for k in ("id", "kind", "point", "file", "sha256", "locator")}


def _check_parts(parts):
    seen_ids, seen_points, seen_files = set(), set(), set()
    out = []
    for p in parts:
        p = _check_part(p)
        if p["id"] in seen_ids:
            raise ValueError(f"part {p['id']} appears twice")
        if p["point"] in seen_points:
            raise ValueError(f"two parts occupy the {p['point']} point; one occupant per point")
        if p["file"] in seen_files:
            raise ValueError(f"two parts use the file {p['file']}")
        seen_ids.add(p["id"]); seen_points.add(p["point"]); seen_files.add(p["file"])
        out.append(p)
    if len(out) > 64:
        raise ValueError("a shape holds at most 64 parts")
    return out


def check_object(obj):
    if not isinstance(obj, dict) or set(obj) != {"schema", "grail", "safe_mode", "parts"}:
        raise ValueError("shape object is exactly {schema, grail, safe_mode, parts}")
    if obj["schema"] != SCHEMA:
        raise ValueError(f"shape schema must be {SCHEMA}")
    g = obj["grail"]
    if not isinstance(g, dict) or set(g) != {"sha256", "bytes"} or not _HEX64.match(str(g["sha256"])) \
            or not isinstance(g["bytes"], int) or g["bytes"] <= 0:
        raise ValueError("shape grail must be {sha256: hex64, bytes: int > 0}")
    if not isinstance(obj["safe_mode"], bool):
        raise ValueError("safe_mode must be a boolean")
    return {"schema": SCHEMA, "grail": {"sha256": g["sha256"], "bytes": g["bytes"]},
            "safe_mode": obj["safe_mode"], "parts": _check_parts(obj["parts"])}


def perform(object, params):
    """Pure: (present shape, params) -> next shape. Raises ValueError; never repairs."""
    cur = check_object(object)
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    action = params.get("action")
    if action == "add":
        part = _check_part(params.get("part"))
        parts = [p for p in cur["parts"] if p["id"] != part["id"]] + [part]
    elif action == "remove":
        pid = params.get("id")
        if pid not in {p["id"] for p in cur["parts"]}:
            raise ValueError(f"no part {pid!r} in the present shape")
        parts = [p for p in cur["parts"] if p["id"] != pid]
    elif action == "safe_mode":
        if not isinstance(params.get("on"), bool):
            raise ValueError("safe_mode needs on: true|false")
        return {**cur, "safe_mode": params["on"]}
    elif action == "set":  # the whole desired state (GitOps): parts, and optionally safe_mode (undo restores both)
        parts = params.get("parts")
        if not isinstance(parts, list):
            raise ValueError("set needs parts: [...]")
        sm = params.get("safe_mode", cur["safe_mode"])
        if not isinstance(sm, bool):
            raise ValueError("set safe_mode must be a boolean when given")
        return {**cur, "safe_mode": sm, "parts": _check_parts(parts)}
    else:
        raise ValueError("action must be add, remove, safe_mode or set")
    return {**cur, "parts": _check_parts(parts)}
