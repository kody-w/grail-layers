"""succession: whose Brainstem this is, and who inherits it (rapp-succession/1 over the device chain).

Model: Google's Inactive Account Manager (a silence window, then a confirmation window, then the hand-off) and
PGP key-transition statements (the old key signs the statement that names the new key). Bitcoin's inheritance
losses were lost keys, not bad contracts, so the AI never holds a secret: the chain carries PUBLIC keys only;
private keys live in files the owner controls (default ~/.brainstem/keys/, mode 600) and are read only to sign.

Three signed kinds (memory family; they interleave with shape.* frames in the same chain):
  succession.name     {schema, owner{rappid,spki_der_b64}, successor{name,rappid,spki_der_b64}, window{silence_days,confirm_days}}
                      signed by the current owner (the first one establishes the chain's owner key)
  succession.pulse    {schema, head}                                   owner-signed sign of life
  succession.handoff  {schema, from_head, reason "owner"|"silence", at}  owner-signed any time, or successor-signed
                      once `at` >= last owner-signed utc + silence_days + confirm_days
After a valid handoff the successor is the owner. The stream id never changes: the chain is the asset.
"""
import base64
import datetime
import json
import os
import stat
from pathlib import Path

import rapp

SCHEMA = "rapp-succession/1"
KINDS = ("succession.name", "succession.pulse", "succession.handoff")
DEFAULT_WINDOW = {"silence_days": 30, "confirm_days": 7}


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as e:  # the Brainstem venv has it; plain python3 may not
        raise SystemExit("succession needs the 'cryptography' package (run with the Brainstem's venv python)") from e
    return serialization, ed25519


def _b64(b):
    return base64.b64encode(b).decode()


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _parse_utc(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc)


def _fmt_utc(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


# ---- keys (the owner's files; never printed, never copied into the chain) ----
def mint_key(path, owner, slug, name):
    """A new Ed25519 key: private PEM at `path` (mode 600) and `<path>.pub.json` with the public record."""
    serialization, ed25519 = _crypto()
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} already exists; refusing to overwrite a key")
    priv = ed25519.Ed25519PrivateKey.generate()
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    rec = public_record(path, owner, slug, name)
    Path(str(path) + ".pub.json").write_text(json.dumps(rec, indent=1) + "\n")
    return rec


def _load_private(path):
    serialization, ed25519 = _crypto()
    p = Path(path)
    if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{p} is readable by others; a signing key must be mode 600")
    key = serialization.load_pem_private_key(p.read_bytes(), password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError(f"{p} is not an Ed25519 key")
    return key


def public_record(private_path, owner, slug, name):
    """{name, rappid, spki_der_b64} for a private key file: the only thing that ever enters a chain."""
    serialization, _ = _crypto()
    key = _load_private(private_path)
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return {"name": name, "rappid": rapp.mint_rappid(owner, slug, spki), "spki_der_b64": _b64(spki)}


def load_public_record(path):
    rec = json.loads(Path(path).read_text())
    if set(rec) < {"name", "rappid", "spki_der_b64"}:
        raise ValueError(f"{path} is not a public key record (name, rappid, spki_der_b64)")
    return {k: rec[k] for k in ("name", "rappid", "spki_der_b64")}


def signer(private_path, kid):
    """A callable(frame_without_sig) -> detached JWS (§10): header exactly alg,b64,crit,kid; input header.canonical(frame)."""
    key = _load_private(private_path)
    header = rapp.canonical({"alg": "EdDSA", "b64": False, "crit": ["b64"], "kid": kid}).encode()
    protected = _b64url(header)

    def sign(frame):
        value = {k: v for k, v in frame.items() if k != "sig"}
        sig = key.sign(protected.encode("ascii") + b"." + rapp.canonical(value).encode("utf-8"))
        return protected + ".." + _b64url(sig)
    return sign


# ---- reading the chain ----
def _verify(frame, spki_b64, kid):
    value = {k: v for k, v in frame.items() if k != "sig"}
    return rapp.verify_detached_jws(value, frame["sig"], base64.b64decode(spki_b64), expected_kid=kid)


def validate(frames):
    """Walk the chain; verify every succession frame against the key the chain itself established.
    Returns (ok, why, state_dict). Plain words: an owner reads them in chat."""
    owner = successor = None          # public records
    last_owner_utc = None
    window = dict(DEFAULT_WINDOW)
    inherited = False
    for f in frames:
        k = f["kind"]
        if k not in KINDS:
            continue
        p = f["payload"]
        if p.get("schema") != SCHEMA:
            return False, f"frame #{f['seq']} is not a {SCHEMA} payload", None
        if f["sig"] is None:
            return False, f"frame #{f['seq']} ({k}) is not signed; succession frames must be signed", None
        if k == "succession.name":
            if set(p) != {"schema", "owner", "successor", "window"}:
                return False, f"frame #{f['seq']} names a successor with the wrong fields", None
            if owner is None:
                signer_rec = p["owner"]  # the first naming establishes the owner key
            else:
                signer_rec = owner
                if p["owner"]["rappid"] != owner["rappid"]:
                    return False, f"frame #{f['seq']} claims a different owner than the chain's", None
            ok, why = _verify(f, signer_rec["spki_der_b64"], signer_rec["rappid"])
            if not ok:
                return False, f"frame #{f['seq']}: the naming is not signed by the owner ({why})", None
            if not (1 <= int(p["window"]["silence_days"]) <= 3650 and 0 <= int(p["window"]["confirm_days"]) <= 365):
                return False, f"frame #{f['seq']}: the window is out of range", None
            owner = {"name": p["owner"].get("name", "owner"), **{x: p["owner"][x] for x in ("rappid", "spki_der_b64")}}
            successor = {x: p["successor"][x] for x in ("name", "rappid", "spki_der_b64")}
            window = {"silence_days": int(p["window"]["silence_days"]), "confirm_days": int(p["window"]["confirm_days"])}
            last_owner_utc = f["utc"]
        elif k == "succession.pulse":
            if owner is None:
                return False, f"frame #{f['seq']}: a pulse before anyone was named", None
            ok, why = _verify(f, owner["spki_der_b64"], owner["rappid"])
            if not ok:
                return False, f"frame #{f['seq']}: the pulse is not the owner's ({why})", None
            last_owner_utc = f["utc"]
        elif k == "succession.handoff":
            if owner is None or successor is None:
                return False, f"frame #{f['seq']}: a hand-off before a successor was named", None
            if set(p) != {"schema", "from_head", "reason", "at"} or p["reason"] not in ("owner", "silence"):
                return False, f"frame #{f['seq']}: the hand-off has the wrong fields", None
            if p["reason"] == "owner":
                ok, why = _verify(f, owner["spki_der_b64"], owner["rappid"])
                if not ok:
                    return False, f"frame #{f['seq']}: the hand-off is not signed by the owner ({why})", None
            else:
                ok, why = _verify(f, successor["spki_der_b64"], successor["rappid"])
                if not ok:
                    return False, f"frame #{f['seq']}: the hand-off is not signed by the named successor ({why})", None
                deadline = _parse_utc(last_owner_utc) + datetime.timedelta(days=window["silence_days"] + window["confirm_days"])
                if _parse_utc(p["at"]) < deadline or _parse_utc(f["utc"]) < _parse_utc(p["at"]):
                    return False, (f"frame #{f['seq']}: hand-off before the window; the owner was last heard "
                                   f"{last_owner_utc}, the window closes {_fmt_utc(deadline)}"), None
            owner = dict(successor); successor = None; inherited = True
            last_owner_utc = f["utc"]
    return True, "ok", {"owner": owner, "successor": successor, "last_owner_utc": last_owner_utc,
                        "window": window, "inherited": inherited}


def state(frames, now_utc=None):
    """Plain state for the owner: who owns, who inherits, where the clock stands."""
    ok, why, s = validate(frames)
    if not ok:
        return {"status": "refused", "why": why}
    if s["owner"] is None:
        return {"status": "unnamed", "why": "no owner or successor named yet"}
    now = _parse_utc(now_utc) if now_utc else datetime.datetime.now(datetime.timezone.utc)
    last = _parse_utc(s["last_owner_utc"])
    silence = last + datetime.timedelta(days=s["window"]["silence_days"])
    confirm = silence + datetime.timedelta(days=s["window"]["confirm_days"])
    if s["successor"] is None:
        status = "inherited" if s["inherited"] else "owned"
    elif now < silence:
        status = "owned"
    elif now < confirm:
        status = "silent"
    else:
        status = "handoff-open"
    return {"status": status, "owner": s["owner"], "successor": s["successor"], "last_owner_utc": s["last_owner_utc"],
            "silence_deadline": _fmt_utc(silence), "confirm_deadline": _fmt_utc(confirm), "window": s["window"],
            "inherited": s["inherited"]}


def describe(st):
    if st["status"] == "refused":
        return "Succession record refused: " + st["why"]
    if st["status"] == "unnamed":
        return "No successor named. Say 'if something happens to me, <name> gets this' to name one."
    o = st["owner"]["name"]
    if st["successor"] is None:
        return f"Owned by {o}" + (" (inherited)" if st["inherited"] else "") + "; no next successor named yet."
    s = st["successor"]["name"]; w = st["window"]
    line = f"Owned by {o}; if {o} is silent for {w['silence_days']} days, then {w['confirm_days']} more to confirm, {s} inherits."
    if st["status"] == "silent":
        line += f" {o} has been silent since {st['last_owner_utc']}; the hand-off opens {st['confirm_deadline']}."
    elif st["status"] == "handoff-open":
        line += f" The window closed {st['confirm_deadline']}: {s} may take over now."
    else:
        line += f" Last sign of life {st['last_owner_utc']}."
    return line


# ---- writing frames (through the chain's own append, so order and hashes are the chain's) ----
def name_successor(chain, owner_priv, owner_rec, successor_rec, window=None):
    payload = {"schema": SCHEMA, "owner": owner_rec, "successor": successor_rec, "window": window or DEFAULT_WINDOW}
    return chain.append("succession.name", payload, signer=signer(owner_priv, owner_rec["rappid"]))


def pulse(chain, owner_priv, owner_rec):
    return chain.append("succession.pulse", {"schema": SCHEMA, "head": chain.head["frame_hash"]},
                        signer=signer(owner_priv, owner_rec["rappid"]))


def handoff(chain, priv, rec, reason, at=None):
    at = at or _fmt_utc(datetime.datetime.now(datetime.timezone.utc))
    payload = {"schema": SCHEMA, "from_head": chain.head["frame_hash"], "reason": reason, "at": at}
    return chain.append("succession.handoff", payload, signer=signer(priv, rec["rappid"]), utc=at if reason == "silence" else None)
