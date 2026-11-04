"""hologram: a Brainstem is a living hologram (kody-w.github.io/living-holograms, over rapp/1).

Identity = an append-only stream of rapp/1 frames per device (`chain.jsonl`). Present = the head.
The object carried is the shape (`brainstem-shape/1`, see shape_lens.py). The shape changes only
through the one pure lens, as a tile; a tile takes effect only after a passing gate frame; the
supervisor boots whatever the newest passing gate authorises, or plain grail if nothing does.

Frames (all memory-stream, no signature needed; order is chain position, never the clock):
  shape.genesis  payload = the shape object (grail pin, no parts)        -> plain grail
  shape.tile     payload = {object, derived_from, lens, params, params_hash, slush_in, caller, lease}
  shape.gate     payload = {tile, gate: "lab"|"live", passed, evidence}  -> only a passing one authorises
"Time Machine": any earlier passing tile can be re-applied with `set`; the chain keeps every shape ever run.
Re-genesis (§12.1) needs the owner's signing key; until the estate holds one, the chain is simply kept.

Why these numbers: a device chain over 50 000 frames is read in full at each boot (~seconds), so the
supervisor warns at that size and asks for a checkpoint; nothing is ever deleted.
"""
import hashlib
import json
import os
import socket
import time
import urllib.request
from pathlib import Path

import rapp
import shape_lens

SCHEMA = shape_lens.SCHEMA
WARN_FRAMES = 50_000


def utc_now():
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int((t % 1) * 1000)


def next_ms(utc):
    """The millisecond after a rapp/1 utc string (used only when the device clock is behind the chain head)."""
    import datetime
    t = datetime.datetime.strptime(utc, "%Y-%m-%dT%H:%M:%S.%fZ") + datetime.timedelta(milliseconds=1)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


def device_label():
    raw = (os.environ.get("GRAIL_DEVICE") or socket.gethostname().split(".")[0]).lower()
    lab = "".join(c if c.isalnum() else "-" for c in raw).strip("-") or "device"
    return lab[:64]


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Chain:
    """The device's stream on disk: one frame per line, verified on read, refused on any break."""

    def __init__(self, path):
        self.path = Path(path)
        self.frames = []
        self.keys = {}  # rappid -> spki_der_b64, every public key the chain itself has recorded (succession.name)
        if self.path.is_file():
            head = None
            for n, line in enumerate(self.path.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                f = json.loads(line)
                ok, step, why = rapp.verify_frame(f, head=head, signature_verifier=self._verifier(f))
                if not ok:
                    raise ValueError(f"{self.path} line {n}: frame refused at step {step}: {why}")
                self.frames.append(f)
                self._harvest_keys(f)
                head = f
        if len(self.frames) > WARN_FRAMES:
            print(f"[hologram] chain holds {len(self.frames)} frames; consider a checkpoint", flush=True)

    # ---- signatures: the chain is its own key directory (device-local root; no registry on a device) ----
    def _harvest_keys(self, f):
        if f["kind"].startswith("succession."):
            for role in ("owner", "successor"):
                rec = f["payload"].get(role)
                if isinstance(rec, dict) and isinstance(rec.get("rappid"), str) and isinstance(rec.get("spki_der_b64"), str):
                    self.keys.setdefault(rec["rappid"], rec["spki_der_b64"])

    def _verifier(self, f):
        """Checks a §10 signature against a key THIS chain recorded (or the key a first naming carries for itself).
        Who may sign what (owner vs successor, the window) is the profile's rule, checked by succession.validate."""
        def verify(unsigned, sig):
            import base64
            header, _, _ = rapp.parse_detached_jws(sig)
            kid = header["kid"]
            spki = self.keys.get(kid)
            if spki is None and f["kind"] == "succession.name":
                own = f["payload"].get("owner") or {}
                if own.get("rappid") == kid:
                    spki = own.get("spki_der_b64")
            if spki is None:
                return False, "signed by a key this chain has never recorded"
            return rapp.verify_detached_jws(unsigned, sig, base64.b64decode(spki), expected_kid=kid)
        return verify

    @property
    def head(self):
        return self.frames[-1] if self.frames else None

    @property
    def stream_id(self):
        return self.frames[0]["stream_id"] if self.frames else None

    def append(self, kind, payload, signer=None, utc=None):
        """Append one frame. `signer(frame_without_sig) -> detached JWS` signs it (§10: the signature covers the
        ten other keys, so it is added after the hashes and before verification). `utc` may be given for a
        frame that must carry a stated time (a hand-off's `at`); it is never allowed to precede the head."""
        h = self.head
        utc = utc or utc_now()
        if h and utc <= h["utc"]:  # a clock that went backwards must not freeze the chain: order is position, never the clock
            utc = next_ms(h["utc"])
        f = rapp.build_frame(kind, self.stream_id, (h["seq"] + 1) if h else 0, utc, payload,
                             h["payload_hash"] if h else None)
        if signer:
            f["sig"] = signer(f)
        ok, step, why = rapp.verify_frame(f, head=h, signature_verifier=self._verifier(f))
        if not ok:
            raise ValueError(f"refusing to append: step {step}: {why}")
        with open(self.path, "a") as fh:
            fh.write(rapp.canonical(f) + "\n")
        self.frames.append(f)
        self._harvest_keys(f)
        return f

    # ---- reading the present ----
    def genesis_object(self):
        return self.frames[0]["payload"] if self.frames else None

    def present(self):
        """The object of the newest tile, else genesis (the present need not be gated yet)."""
        for f in reversed(self.frames):
            if f["kind"] == "shape.tile":
                return f["payload"]["object"]
        return self.genesis_object()

    def authorised(self):
        """(shape, tile_frame_or_None) the newest PASSING gate authorises; genesis if none."""
        tiles = {f["frame_hash"]: f for f in self.frames if f["kind"] == "shape.tile"}
        for f in reversed(self.frames):
            if f["kind"] == "shape.gate" and f["payload"]["passed"] and f["payload"]["tile"] in tiles:
                t = tiles[f["payload"]["tile"]]
                return t["payload"]["object"], t
        return self.genesis_object(), None

    def live_failures_since_pass(self):
        n = 0
        for f in reversed(self.frames):
            if f["kind"] == "shape.gate":
                if f["payload"]["passed"]:
                    break
                if f["payload"]["gate"] == "live":
                    n += 1
        return n

    def since(self, utc):
        return [f for f in self.frames if f["utc"] >= utc]


def genesis(chain_path, kernel_path, owner="kody-w"):
    """First boot on a device: mint the stream and record the grail pin as the shape with no parts."""
    chain = Chain(chain_path)
    if chain.frames:
        return chain
    kb = Path(kernel_path).read_bytes()
    obj = {"schema": SCHEMA, "grail": {"sha256": hashlib.sha256(kb).hexdigest(), "bytes": len(kb)},
           "safe_mode": False, "parts": []}
    shape_lens.check_object(obj)
    chain.frames = []
    stream = rapp.mint_rappid(owner, "brainstem") + ":" + device_label()
    f = rapp.build_frame("shape.genesis", stream, 0, utc_now(), obj, None)
    ok, step, why = rapp.verify_frame(f)
    if not ok:
        raise ValueError(f"genesis refused: {why}")
    Path(chain_path).write_text(rapp.canonical(f) + "\n")
    return Chain(chain_path)


def lens_ref():
    src = Path(shape_lens.__file__).read_bytes()
    return {"name": "shape_lens", "sha256": hashlib.sha256(src).hexdigest()}


def tile(chain, params, caller):
    """One pass through the lens: present shape + params -> next shape, appended as a tile. Pure; may raise."""
    base, base_tile = chain.authorised()  # a failed tile is a dead end: the next pass derives from what passed
    nxt = shape_lens.perform(base, params)
    payload = {"object": nxt, "derived_from": (base_tile or chain.frames[0])["frame_hash"], "lens": lens_ref(),
               "params": params, "params_hash": rapp.H("rapp/1:particle", params), "slush_in": None,
               "caller": caller, "lease": None}
    return chain.append("shape.tile", payload)


def gate(chain, tile_frame, where, passed, evidence):
    return chain.append("shape.gate", {"tile": tile_frame["frame_hash"], "gate": where, "passed": bool(passed),
                                       "evidence": evidence})


def replay(chain):
    """Replay rule: re-run every tile through the recorded lens; the head object must come back byte-identical."""
    objects = {chain.frames[0]["frame_hash"]: chain.genesis_object()}  # frame hash -> the object it carries
    tiles = {}
    for f in chain.frames[1:]:
        if f["kind"] == "shape.gate":  # deriving from a gate frame means: from what that gate authorised
            t = tiles.get(f["payload"]["tile"])
            if f["payload"]["passed"] and t and t["frame_hash"] in objects:
                objects[f["frame_hash"]] = objects[t["frame_hash"]]
            continue
        if f["kind"] != "shape.tile":
            continue
        tiles[f["frame_hash"]] = f
        p = f["payload"]
        if p["lens"]["sha256"] != lens_ref()["sha256"]:
            return False, f"tile {f['seq']} used a different lens ({p['lens']['sha256'][:12]})"
        if p["derived_from"] not in objects:
            return False, f"tile {f['seq']} derives from an unknown frame"
        got = shape_lens.perform(objects[p["derived_from"]], p["params"])
        if rapp.canonical(got) != rapp.canonical(p["object"]):
            return False, f"tile {f['seq']} does not replay"
        objects[f["frame_hash"]] = got
    return True, f"{len(chain.frames)} frames replay; every tile reproduces its object"


# ---- parts on disk (the pinned files a shape names) ----
def part_path(parts_dir, part):
    pub, name = part["id"][1:].split("/", 1)
    return Path(parts_dir) / pub / name / part["file"]


def fetch_part(parts_dir, part):
    """Make the part's file present and pinned: existing file with the right sha, else fetch from the locator."""
    p = part_path(parts_dir, part)
    if p.is_file() and sha256_file(p) == part["sha256"]:
        return p
    url = part["locator"]
    pin = os.environ.get("RAR_PAGES", "").rstrip("/")
    if pin and url.startswith("https://kody-w.github.io/RAR/"):  # a pinned catalog fetches from the same pin
        url = pin + url[len("https://kody-w.github.io/RAR"):]
    b = urllib.request.urlopen(url, timeout=60).read()
    if hashlib.sha256(b).hexdigest() != part["sha256"]:
        raise ValueError(f"{part['id']}: fetched bytes do not match the pinned sha256; refusing")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b)
    return p


def check_parts(parts_dir, shape):
    """Boot-time integrity: every part's bytes equal its pin, or it is reported (never silently run)."""
    bad = {}
    for part in shape["parts"]:
        p = part_path(parts_dir, part)
        if not p.is_file():
            bad[part["id"]] = "file missing"
        elif sha256_file(p) != part["sha256"]:
            bad[part["id"]] = "file changed on disk (hand edit?)"
    return bad


def describe(chain, parts_dir=None):
    """Plain words for the owner: what am I running, and the SBOM (the shape itself)."""
    shape, t = chain.authorised()
    lines = [f"Brainstem on {device_label()}: grail {shape['grail']['sha256'][:12]}"
             + (" in SAFE MODE" if shape["safe_mode"] else "")]
    if shape["parts"]:
        for p in shape["parts"]:
            lines.append(f"  + {p['id']} ({p['kind']} at {p['point']}) {p['sha256'][:12]}")
    else:
        lines.append("  plain grail, no parts")
    lines.append(f"  head #{chain.head['seq']} {chain.head['frame_hash'][:12]}; authorised by "
                 + (f"tile #{t['seq']}" if t else "genesis"))
    if parts_dir:
        bad = check_parts(parts_dir, shape)
        for k, v in bad.items():
            lines.append(f"  ! {k}: {v}")
    return "\n".join(lines)
