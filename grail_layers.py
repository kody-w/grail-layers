#!/usr/bin/env python3
"""grail_layers: run the UNCHANGED grail as a living hologram, and fall back to plain grail.

Kody, 2026-11-03: "grail should still be the base and updates should be layered over it so if
something breaks when layering over it doesnt break grail and they still have grail"; and the same
day: the Brainstem is a living hologram (kody-w.github.io/living-holograms over rapp/1).

Precedents: Linux livepatch/kpatch (patch a running kernel in memory, the image on disk unchanged,
revertible), Fedora Silverblue/ostree (immutable base + layered packages + one-step rollback),
ChromeOS A/B (a bad update boots back into the known-good slot), Kubernetes (desired state +
reconcile), Git (append-only, content-addressed history).

How it works
  1. The grail file (brainstem.py) is never edited. Its sha256 is the genesis of this device's chain.
  2. The shape (which parts run over the grail, or which kernel replaces it) changes only through
     shape_lens (pure) as a tile on the chain; a tile takes effect only after a passing gate frame.
  3. Boot: read the chain, take the shape the newest passing gate authorises, start it, probe /chat,
     append a live gate frame. Two live failures in a row = fall back to the previous passing shape,
     then to plain grail. The user always ends up with a Brainstem.
  4. `--apply` (the agent's path): lens -> tile -> lab boot on a spare port -> probe -> gate frame.
     Nothing goes live from the lab; the next boot (or a restart) takes the authorised shape.

Usage
  python3 grail_layers.py --kernel /path/to/brainstem.py [--port 7071] [--no-probe]
  python3 grail_layers.py --kernel ... --plain                 # safe mode: grail, no parts
  python3 grail_layers.py --kernel ... --apply '{"action":"add","part":{...}}' [--by "kody"]
  python3 grail_layers.py --kernel ... --status | --replay | --since 2026-11-03T00:00:00.000Z | --undo
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("GRAIL_LAYERS_HOME") or HERE)   # chain.jsonl, parts/, logs live here
LOG = HOME / "layers.log"
SCHEMA_PLAIN = "brainstem-shape/1"
sys.path.insert(0, str(HERE))
import hologram  # noqa: E402  (rapp/1 chain + the one lens)


def apply_layers(kernel_bytes, layers_dir, names, report_dir=None):
    """Apply layers in THIS process before the kernel runs. Returns (grail_sha, applied, skipped).
    `names` are layer names in `layers_dir` or explicit file paths (hologram parts). Importable so a
    conformance harness (rapp/1 conformance/grail) runs the exact same code path."""
    import ast, hashlib, importlib.util, traceback
    grail_sha = hashlib.sha256(kernel_bytes).hexdigest()
    unpinned = os.environ.get("GRAIL_LAYERS_UNPINNED") == "1"
    applied, skipped, taken = [], {}, set()
    for n in names:
        path = n if os.sep in n else os.path.join(layers_dir, n + ".py")
        label = os.path.basename(path)[:-3]
        try:
            proven, touches = set(), set()
            for node in ast.parse(open(path).read()).body:  # the layer's own sets, read without running it
                if isinstance(node, ast.Assign):
                    ids = {getattr(t, "id", "") for t in node.targets}
                    if "PROVEN_GRAIL" in ids:
                        proven = set(ast.literal_eval(node.value))
                    if "TOUCHES" in ids:
                        touches = set(ast.literal_eval(node.value))
            if grail_sha not in proven and not unpinned:
                skipped[label] = "unproven for this grail"
                print(f"[grail-layers] layer {label} is not proven for this grail ({grail_sha[:12]}); skipped", flush=True)
                continue
            clash = touches & taken
            if clash:  # D365 lesson: never silent last-wins; the shape's order decides and the loser is logged
                skipped[label] = "conflict on " + ",".join(sorted(clash))
                print(f"[grail-layers] layer {label} conflicts on {sorted(clash)} with an earlier layer; skipped", flush=True)
                continue
            taken |= touches
            spec = importlib.util.spec_from_file_location("grail_layer_" + label, path)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            m.apply()
            applied.append(label)
        except Exception:
            skipped[label] = "failed to apply"
            print(f"[grail-layers] layer {label} failed to apply, skipped:\n{traceback.format_exc()}", flush=True)
    os.environ["GRAIL_LAYERS_APPLIED"] = ",".join(applied)
    os.environ["GRAIL_LAYERS_SKIPPED"] = ",".join(skipped)
    report_dir = report_dir or os.environ.get("GRAIL_LAYERS_REPORT_DIR") or os.path.dirname(str(layers_dir).rstrip("/"))
    try:
        with open(os.path.join(report_dir, "applied.json"), "w") as f:
            f.write(json.dumps({"grail_sha256": grail_sha, "applied": applied, "skipped": skipped}))
    except Exception:
        pass
    print(f"[grail-layers] grail {grail_sha[:12]}; applied: {applied or 'none'}; skipped: {skipped or 'none'}", flush=True)
    return grail_sha, applied, skipped


def boot(kernel, layers_dir, names):
    """Child entry: apply layers, then run the VERIFIED kernel bytes as __main__ (rapp/1 §11.1.5)."""
    import types
    kernel_bytes = open(kernel, "rb").read()  # read ONCE: the bytes we hash are the bytes we run
    apply_layers(kernel_bytes, layers_dir, names)
    sys.argv = [kernel]
    sys.path.insert(0, os.path.dirname(kernel))
    os.chdir(os.path.dirname(kernel))
    g = types.ModuleType("__main__"); g.__file__ = kernel; g.__builtins__ = __builtins__
    sys.modules["__main__"] = g
    exec(compile(kernel_bytes, kernel, "exec"), g.__dict__)  # the verified bytes, never a re-opened path


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print("[grail-layers] " + msg, flush=True)
    HOME.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


LAST_BOOT = HOME / "last-boot.json"


def last_boot(**fields):
    """What the owner should hear about this boot, written once per boot; the chat agent says it once (told=false->true).
    Boot messages nobody reads are not messages (journald lesson): this file is the one place the owner's chat looks."""
    rec = {"utc": hologram.utc_now(), "told": False, **fields}
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = LAST_BOOT.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1)); os.replace(tmp, LAST_BOOT)
    return rec


def http(url, body=None, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


def start(kernel, shape, port, py, report_dir):
    """Start the Brainstem the shape describes: plain grail, grail + layer parts, or a kernel part."""
    env = dict(os.environ, PORT=str(port), GRAIL_LAYERS_REPORT_DIR=str(report_dir))
    parts = [] if shape["safe_mode"] else shape["parts"]
    kernels = [p for p in parts if p["kind"] == "kernel"]
    layers = [str(hologram.part_path(HOME / "parts", p)) for p in parts if p["kind"] == "layer"]
    if kernels and layers:  # layers wrap brainstem.py; a kernel part replaces it, so they do not apply (said, never silent)
        log("kernel part present: layer parts " + ",".join(p["id"] for p in parts if p["kind"] == "layer") + " do not apply to it")
    if kernels:  # a kernel part replaces brainstem.py; the user's agents and soul (GODD data) follow every shape
        entry = hologram.part_path(HOME / "parts", kernels[0])
        env.setdefault("AGENTS_PATH", os.path.join(os.path.dirname(kernel), "agents"))
        env.setdefault("SOUL_PATH", os.path.join(os.path.dirname(kernel), "soul.md"))
        cmd, cwd = [py, str(entry)], str(entry.parent)
    elif layers:
        cmd, cwd = [py, str(HERE / "grail_layers.py"), "--boot", kernel, str(HOME / "parts"), ",".join(layers)], os.path.dirname(kernel)
    else:
        cmd, cwd = [py, kernel], os.path.dirname(kernel)
    out = open(HOME / f"brainstem-{port}.log", "a")
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)


def healthy(p, port, probe, wait=60):
    base = f"http://127.0.0.1:{port}"
    for _ in range(wait):
        if p.poll() is not None:
            return False, f"exited with code {p.returncode}"
        try:
            if http(base + "/health", timeout=3)[0] == 200:
                break
        except Exception:
            time.sleep(1)
    else:
        return False, "no /health within %ds" % wait
    if not probe:
        return True, "health ok (probe skipped)"
    try:
        code, d = http(base + "/chat", {"user_input": "Reply with the single word OK."}, timeout=180)
        if code == 200 and str(d.get("response", "")).strip():
            return True, "health ok; /chat probe answered"
        return False, f"/chat probe gave {code} with no response"
    except Exception as e:
        return False, f"/chat probe failed: {e}"


def stop(p):
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(15)
        except Exception:
            os.killpg(p.pid, signal.SIGKILL)


def evidence(report_dir, why):
    ev = {"why": why}
    try:
        rep = json.loads((Path(report_dir) / "applied.json").read_text())
        ev.update(applied=rep.get("applied", []), skipped=rep.get("skipped", {}), grail=rep.get("grail_sha256", "")[:12])
    except Exception:
        pass
    return ev


def open_chain(kernel):
    """The device's chain; genesis on first boot; migrate a legacy `enabled` list into the first tile."""
    HOME.mkdir(parents=True, exist_ok=True)
    chain = hologram.genesis(HOME / "chain.jsonl", kernel)
    enabled = HOME / "enabled"
    if len(chain.frames) == 1 and enabled.is_file():
        import hashlib, shutil
        parts = []
        for n in [l.strip() for l in enabled.read_text().splitlines() if l.strip()]:
            src = HOME / "layers" / f"{n}.py"
            if not src.is_file():
                continue
            part = {"id": f"@kody-w/{n}", "kind": "layer", "point": "provider", "file": f"{n}.py",
                    "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                    "locator": f"https://kody-w.github.io/RAR/stacks/grail-layers/{n}.py"}
            dst = hologram.part_path(HOME / "parts", part); dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            parts.append(part)
        if parts:
            t = hologram.tile(chain, {"action": "set", "parts": parts}, {"kind": "process", "name": "migrate-enabled"})
            hologram.gate(chain, t, "lab", True, {"why": "migrated from the legacy enabled list (already proven on this device)"})
            log(f"migrated legacy enabled list into tile #{t['seq']}")
    return chain


def run_live(a, kernel):
    notices = []  # plain sentences for the owner; empty = nothing worth saying
    try:
        chain = open_chain(kernel)
    except Exception as e:  # a broken or tampered chain must never leave the owner without a Brainstem
        log(f"chain refused ({e}); starting plain grail; the chain file is kept untouched for inspection")
        notices.append("I could not read my own change history, so I started as plain grail with no parts; "
                       "the history file was left untouched for inspection.")
        last_boot(what="plain grail", why="chain refused: " + str(e), port=a.port, fallback=True, notices=notices)
        p = start(kernel, {"schema": SCHEMA_PLAIN, "safe_mode": True, "parts": []}, a.port, a.python, HOME)
        signal.signal(signal.SIGTERM, lambda *_: (stop(p), sys.exit(0)))
        sys.exit(p.wait())
    safe = a.plain or os.environ.get("GRAIL_SAFE_MODE") == "1"
    shape, tile_frame = chain.authorised()
    if safe:
        shape = {**shape, "safe_mode": True}
        log("safe mode: starting plain grail, no parts")
    bad = hologram.check_parts(HOME / "parts", shape) if not shape["safe_mode"] else {}
    if bad:  # boot-time integrity (D365 lesson, hand-edit detection): never run a part whose bytes moved
        log("parts refused: " + "; ".join(f"{k} ({v})" for k, v in bad.items()) + " — starting plain grail")
        notices.append("I started as plain grail because " + "; ".join(f"{k} {v}" for k, v in bad.items())
                       + ". Say 'undo' or re-add the part to put it back.")
        shape, tile_frame = {**shape, "safe_mode": True}, None  # a device condition, not the tile's failure: no gate recorded
    if tile_frame and chain.live_failures_since_pass() >= 2:  # flap guard
        log(f"tile #{tile_frame['seq']} failed live twice; falling back to the previous authorised shape")
        notices.append(f"My latest shape (tile #{tile_frame['seq']}) failed to start twice in a row, so I went back to the one before it.")
        prev = [f for f in chain.frames if f["kind"] == "shape.gate" and f["payload"]["passed"]
                and f["payload"]["tile"] != tile_frame["frame_hash"]]
        tiles = {f["frame_hash"]: f for f in chain.frames if f["kind"] == "shape.tile"}
        if prev and prev[-1]["payload"]["tile"] in tiles:
            tile_frame = tiles[prev[-1]["payload"]["tile"]]  # the live gate below is recorded against what actually boots
            shape = tile_frame["payload"]["object"]
        else:
            tile_frame, shape = None, chain.genesis_object()
    p = start(kernel, shape, a.port, a.python, HOME)
    ok, why = healthy(p, a.port, not a.no_probe)
    if tile_frame and not safe:
        hologram.gate(chain, tile_frame, "live", ok, evidence(HOME, why))
    if not ok and (shape["parts"] and not shape["safe_mode"]):
        log(f"shape failed live ({why}); falling back to plain grail")
        notices.append(f"My parts failed to start ({why}), so I am running as plain grail until the next change passes.")
        stop(p)
        shape = {**shape, "safe_mode": True}
        p = start(kernel, shape, a.port, a.python, HOME)
        ok, why = healthy(p, a.port, False)
    what = "plain grail" if (shape["safe_mode"] or not shape["parts"]) else "grail + " + ",".join(x["id"] for x in shape["parts"])
    log(f"running {what} on port {a.port}: {why}")
    ev = evidence(HOME, why)
    if ev.get("skipped"):
        notices.append("Skipped parts this boot: " + "; ".join(f"{k} ({v})" for k, v in ev["skipped"].items()) + ".")
    last_boot(what=what, why=why, port=a.port, fallback=bool(notices), notices=notices, skipped=ev.get("skipped", {}))
    signal.signal(signal.SIGTERM, lambda *_: (stop(p), sys.exit(0)))
    try:
        sys.exit(p.wait())
    except KeyboardInterrupt:
        stop(p)


def run_apply(a, kernel):
    """lens -> tile -> lab boot -> probe -> gate. Prints one JSON line for the agent; never touches live."""
    chain = open_chain(kernel)
    params = json.loads(a.apply)
    caller = {"kind": "person" if a.by else "process", "name": a.by or "grail_layers --apply"}
    try:
        t = hologram.tile(chain, params, caller)
    except ValueError as e:
        print(json.dumps({"ok": False, "refused": str(e)})); return 2
    shape = t["payload"]["object"]
    try:
        for part in shape["parts"]:
            if part["kind"] == "layer":
                hologram.fetch_part(HOME / "parts", part)
    except Exception as e:
        hologram.gate(chain, t, "lab", False, {"why": f"part not available: {e}"})
        print(json.dumps({"ok": False, "tile": t["seq"], "refused": f"part not available: {e}"})); return 2
    bad = hologram.check_parts(HOME / "parts", shape)
    if bad:
        hologram.gate(chain, t, "lab", False, {"why": "parts missing", "parts": bad})
        print(json.dumps({"ok": False, "tile": t["seq"], "refused": bad})); return 2
    lab = HOME / "lab"; lab.mkdir(exist_ok=True)
    p = start(kernel, shape, a.lab_port, a.python, lab)
    ok, why = healthy(p, a.lab_port, not a.no_probe)
    stop(p)
    g = hologram.gate(chain, t, "lab", ok, evidence(lab, why))
    log(f"--apply tile #{t['seq']} gate #{g['seq']}: {'PASSED' if ok else 'FAILED'} ({why})")
    print(json.dumps({"ok": ok, "tile": t["seq"], "gate": g["seq"], "why": why,
                      "next": "restart the Brainstem to run it" if ok else "nothing changed; the live Brainstem is untouched"}))
    return 0 if ok else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--boot":  # child process: --boot <kernel> <layers_dir> <names-or-paths>
        boot(sys.argv[2], sys.argv[3], [n for n in sys.argv[4].split(",") if n])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7071)))
    ap.add_argument("--lab-port", type=int, default=int(os.environ.get("GRAIL_LAB_PORT", 17071)))
    ap.add_argument("--python", default=None, help="interpreter for the Brainstem (default: the venv beside the kernel, else this one)")
    ap.add_argument("--plain", action="store_true")
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--apply", help="JSON params for shape_lens (add/remove/safe_mode/set)")
    ap.add_argument("--by", help="who asked (a person's name) for the tile's caller field")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--since", help="UTC like 2026-11-03T00:00:00.000Z: frames since then")
    ap.add_argument("--undo", action="store_true", help="tile back to the previously authorised shape (then gate it)")
    a = ap.parse_args()
    kernel = str(Path(a.kernel).expanduser().resolve())
    if not a.python:  # the grail's own venv (~/.brainstem/venv) is where its dependencies live
        venv = Path(kernel).parent.parent.parent / "venv" / "bin" / "python"
        a.python = str(venv) if venv.is_file() else sys.executable
    if a.status:
        print(hologram.describe(open_chain(kernel), HOME / "parts"))
        if LAST_BOOT.is_file():
            lb = json.loads(LAST_BOOT.read_text())
            print(f"  last boot {lb['utc']}: {lb['what']} ({lb['why']})" + ("".join("\n  ! " + n for n in lb.get("notices", []))))
        latest = HOME / "chaos" / "latest.json"
        if latest.is_file():
            c = json.loads(latest.read_text())
            print(f"  last chaos drill {c['finished']}: survived {c['passed']}/{c['total']}"
                  + "".join(f"\n  ! did not survive: {r['scenario']}" for r in c["results"] if not r["passed"]))
        return
    if a.replay:
        ok, why = hologram.replay(open_chain(kernel)); print(("replay ok: " if ok else "REPLAY FAILED: ") + why); sys.exit(0 if ok else 1)
    if a.since:
        for f in open_chain(kernel).since(a.since):
            print(f"#{f['seq']} {f['utc']} {f['kind']} " + (json.dumps(f['payload'].get('params')) if f['kind'] == 'shape.tile'
                  else ("PASSED" if f['payload'].get('passed') else "failed") + f" ({f['payload'].get('gate')})" if f['kind'] == 'shape.gate' else "genesis"))
        return
    if a.undo:
        chain = open_chain(kernel)
        passing = [f for f in chain.frames if f["kind"] == "shape.gate" and f["payload"]["passed"]]
        tiles = {f["frame_hash"]: f for f in chain.frames if f["kind"] == "shape.tile"}
        seen, prev = set(), None
        for g in reversed(passing):  # the previous DISTINCT authorised tile
            seen.add(g["payload"]["tile"])
            if len(seen) == 2:
                prev = tiles.get(g["payload"]["tile"]); break
        obj = prev["payload"]["object"] if prev else chain.genesis_object()
        a.apply = json.dumps({"action": "set", "parts": obj["parts"], "safe_mode": obj["safe_mode"]})
    if a.apply:
        sys.exit(run_apply(a, kernel))
    run_live(a, kernel)


if __name__ == "__main__":
    main()
