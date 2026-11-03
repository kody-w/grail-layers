#!/usr/bin/env python3
"""grail_layers: run the UNCHANGED grail with update layers over it, and fall back to plain grail.

Kody, 2026-11-03: "grail should still be the base and updates should be layered over it so if
something breaks when layering over it doesnt break grail and they still have grail".

Precedents: Linux livepatch/kpatch (patch a running kernel in memory, the image on disk unchanged,
revertible), Fedora Silverblue/ostree (immutable base + layered packages + one-step rollback),
ChromeOS A/B (a bad update boots back into the known-good slot).

How it works
  1. The grail file (brainstem.py) is never edited. Layers are small Python files in layers/,
     applied in memory inside the Brainstem process before the grail runs.
  2. Supervisor: start grail+layers, wait for /health, then send one real /chat probe.
     If anything fails (crash, no health, probe error), stop it and start plain grail on the
     same port, with the reason written to layers.log. The user always ends up with a Brainstem.
  3. Inside a layer, every hook falls back to the grail's own behaviour on any exception.

Usage
  python3 grail_layers.py --kernel /path/to/brainstem.py [--layers responses_models] [--port 7071] [--no-probe]
  python3 grail_layers.py --kernel ... --plain        # just the grail, no layers
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
LOG = HERE / "layers.log"

def apply_layers(kernel_bytes, layers_dir, names, report_dir=None):
    """Apply layers in THIS process before the kernel runs. Returns (grail_sha, applied, skipped).
    Importable so a conformance harness (rapp/1 conformance/grail) can run the exact same code path."""
    import ast, hashlib, importlib.util, traceback
    grail_sha = hashlib.sha256(kernel_bytes).hexdigest()
    unpinned = os.environ.get("GRAIL_LAYERS_UNPINNED") == "1"
    applied, skipped, taken = [], {}, set()
    for n in names:
        try:
            path = os.path.join(layers_dir, n + ".py")
            proven, touches = set(), set()
            for node in ast.parse(open(path).read()).body:  # the layer's own sets, read without running it
                if isinstance(node, ast.Assign):
                    ids = {getattr(t, "id", "") for t in node.targets}
                    if "PROVEN_GRAIL" in ids:
                        proven = set(ast.literal_eval(node.value))
                    if "TOUCHES" in ids:
                        touches = set(ast.literal_eval(node.value))
            if grail_sha not in proven and not unpinned:
                skipped[n] = "unproven for this grail"
                print(f"[grail-layers] layer {n} is not proven for this grail ({grail_sha[:12]}); skipped", flush=True)
                continue
            clash = touches & taken
            if clash:  # D365 lesson: never silent last-wins; the pack's order decides and the loser is logged
                skipped[n] = "conflict on " + ",".join(sorted(clash))
                print(f"[grail-layers] layer {n} conflicts on {sorted(clash)} with an earlier layer; skipped", flush=True)
                continue
            taken |= touches
            spec = importlib.util.spec_from_file_location("grail_layer_" + n, path)
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            m.apply()
            applied.append(n)
        except Exception:
            skipped[n] = "failed to apply"
            print(f"[grail-layers] layer {n} failed to apply, skipped:\n{traceback.format_exc()}", flush=True)
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
    with open(LOG, "a") as f:
        f.write(line + "\n")


def http(url, body=None, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read() or b"{}")


def start(kernel, layers, port, py):
    env = dict(os.environ, PORT=str(port), GRAIL_LAYERS_REPORT_DIR=str(HERE))
    if layers:
        cmd = [py, str(HERE / "grail_layers.py"), "--boot", kernel, str(Path(os.environ.get("GRAIL_LAYERS_DIR") or (HERE / "layers"))), ",".join(layers)]
    else:
        cmd = [py, kernel]
    out = open(HERE / f"brainstem-{port}.log", "a")
    return subprocess.Popen(cmd, cwd=os.path.dirname(kernel), env=env, stdout=out, stderr=subprocess.STDOUT,
                            start_new_session=True)


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


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--boot":  # child process: --boot <kernel> <layers_dir> <names>
        boot(sys.argv[2], sys.argv[3], [n for n in sys.argv[4].split(",") if n])
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    enabled = HERE / "enabled"  # one layer name per line, written by install.sh
    ap.add_argument("--layers", default=",".join(l.strip() for l in enabled.read_text().splitlines() if l.strip()) if enabled.is_file() else "")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7071)))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--plain", action="store_true")
    ap.add_argument("--no-probe", action="store_true")
    a = ap.parse_args()
    kernel = str(Path(a.kernel).expanduser().resolve())
    safe = a.plain or os.environ.get("GRAIL_SAFE_MODE") == "1"  # safe mode: grail with no layers, always available
    layers = [] if safe else [x for x in a.layers.split(",") if x]
    if safe:
        log("safe mode: starting plain grail, no layers")
    p = start(kernel, layers, a.port, a.python)
    ok, why = healthy(p, a.port, not a.no_probe)
    if layers and not ok:
        log(f"layers {layers} failed ({why}); falling back to plain grail")
        stop(p)
        p = start(kernel, [], a.port, a.python)
        ok, why = healthy(p, a.port, False)
        layers = []
    if layers:
        try:
            rep = json.loads((HERE / "applied.json").read_text())
            layers, skipped = rep.get("applied", layers), rep.get("skipped", {})
            why += f"; grail {rep.get('grail_sha256', '')[:12]}" + (("; skipped: " + "; ".join(f"{k} ({v})" for k, v in skipped.items())) if skipped else "")
        except Exception:
            pass
    log((("running grail + " + ",".join(layers)) if layers else "running plain grail") + f" on port {a.port}: {why}")
    signal.signal(signal.SIGTERM, lambda *_: (stop(p), sys.exit(0)))
    try:
        sys.exit(p.wait())
    except KeyboardInterrupt:
        stop(p)


if __name__ == "__main__":
    main()
