"""chaos: break the Brainstem on purpose, in a scratch home, and prove the owner still gets a Brainstem.

Netflix paid for this lesson (Chaos Monkey, 2011): the only way to know a system survives failure is to cause
failure on purpose, bounded and scheduled. Bounds here: a SCRATCH home (never the live chain), the lab port,
no probe (no model calls), one scenario at a time, every result a line in a report the owner's chat can read.

  python3 chaos.py --kernel ~/.brainstem/src/rapp_brainstem/brainstem.py [--python venv-python] [--home dir]

Each scenario answers one question with evidence: after this break, what boots, and what will the owner hear?
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PART = {"file": "responses_models.py", "id": "@kody-w/responses_models", "kind": "layer", "point": "provider",
        "locator": "https://kody-w.github.io/RAR/stacks/grail-layers/responses_models.py",
        "sha256": "77343ad7f8edfdf42ecddc2727ccb2919f15bd75f944ec7b260d439ed1f06e60"}


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Lab:
    def __init__(self, kernel, py, home, port):
        self.kernel, self.py, self.home, self.port = kernel, py, Path(home), port
        self.env = dict(os.environ, GRAIL_LAYERS_HOME=str(self.home), GRAIL_LAB_PORT=str(port + 1))

    def sup(self, *args, timeout=300):
        r = subprocess.run([sys.executable, str(HERE / "grail_layers.py"), "--kernel", self.kernel, "--python", self.py, *args],
                           capture_output=True, text=True, timeout=timeout, env=self.env)
        return r.returncode, (r.stdout + r.stderr).strip()

    def boot(self, wait=90):
        """Start the supervisor live (no probe), wait for its last-boot record, return (proc, record)."""
        lb = self.home / "last-boot.json"
        before = lb.stat().st_mtime if lb.is_file() else 0
        p = subprocess.Popen([sys.executable, str(HERE / "grail_layers.py"), "--kernel", self.kernel, "--python", self.py,
                              "--port", str(self.port), "--no-probe"], env=self.env,
                             stdout=open(self.home / "chaos-sup.log", "a"), stderr=subprocess.STDOUT)
        for _ in range(wait * 2):
            if lb.is_file() and lb.stat().st_mtime > before:
                time.sleep(0.3)
                return p, json.loads(lb.read_text())
            if p.poll() is not None:
                return p, {"what": "supervisor exited", "why": f"code {p.returncode}", "notices": [], "exited": True}
            time.sleep(0.5)
        return p, {"what": "timeout", "why": "no last-boot record", "notices": []}

    def stop(self, p):
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(20)
            except subprocess.TimeoutExpired:
                p.kill()

    def head(self):
        return json.loads(self.home.joinpath("chain.jsonl").read_text().splitlines()[-1])

    def part_file(self):
        return self.home / "parts" / "kody-w" / "responses_models" / "responses_models.py"


def scenario(name, question):
    def deco(fn):
        fn.scenario, fn.question = name, question
        return fn
    return deco


@scenario("clean boot", "With one good part, does the shape boot and the owner hear nothing?")
def s_clean(lab):
    p, lb = lab.boot(); lab.stop(p)
    return (PART["id"] in lb["what"] and not lb["notices"]), lb


@scenario("part bytes changed on disk", "If someone hand-edits a part, do I refuse it, run plain grail, and say so?")
def s_tamper_part(lab):
    f = lab.part_file(); orig = f.read_bytes()
    f.write_bytes(orig + b"\n# hand edit\n")
    try:
        p, lb = lab.boot(); lab.stop(p)
    finally:
        f.write_bytes(orig)
    ok = lb["what"] == "plain grail" and any(PART["id"] in n and "plain grail" in n for n in lb["notices"])
    return ok, lb


@scenario("part file deleted", "If a part's file vanishes, do I run plain grail and say which part is missing?")
def s_missing_part(lab):
    f = lab.part_file(); orig = f.read_bytes(); f.unlink()
    try:
        p, lb = lab.boot(); lab.stop(p)
    finally:
        f.write_bytes(orig)
    return (lb["what"] == "plain grail" and any("missing" in n for n in lb["notices"])), lb


@scenario("chain tampered", "If my history file is corrupted, do I still boot (plain grail) without touching the file?")
def s_tamper_chain(lab):
    c = lab.home / "chain.jsonl"; orig = c.read_bytes()
    lines = orig.splitlines(True)
    lines[-1] = lines[-1].replace(b'"passed": true', b'"passed": false', 1) if b'"passed": true' in lines[-1] else lines[-1].replace(b'"seq"', b'"seq "', 1)
    c.write_bytes(b"".join(lines))
    try:
        p, lb = lab.boot(); lab.stop(p)
        untouched = c.read_bytes() == b"".join(lines)
    finally:
        c.write_bytes(orig)
    return (lb["what"] == "plain grail" and untouched and any("history" in n for n in lb["notices"])), lb


@scenario("brainstem process killed", "If the Brainstem dies under me, does the supervisor exit so the OS restarts it, and does the next boot restore the shape?")
def s_kill_child(lab):
    p, lb = lab.boot()
    # the child is the only other python holding the lab port; kill it by port owner
    pids = subprocess.run(["lsof", "-ti", f"tcp:{lab.port}"], capture_output=True, text=True).stdout.split()
    for pid in pids:  # owner-checked: lab port in a scratch home, started by this script moments ago
        if int(pid) != p.pid:
            os.kill(int(pid), signal.SIGKILL)
    try:
        code = p.wait(30)
    except subprocess.TimeoutExpired:
        lab.stop(p); return False, {"why": "supervisor kept running after its child died"}
    p2, lb2 = lab.boot(); lab.stop(p2)
    return (code != 0 and PART["id"] in lb2["what"]), {"exit_after_kill": code, "next_boot": lb2}


@scenario("network down while adding a part", "If a new part cannot be fetched, is the live shape untouched and the refusal plain?")
def s_no_network(lab):
    head = lab.head()["frame_hash"]
    bad = {**PART, "id": "@kody-w/unreachable_part", "file": "unreachable_part.py",
           "locator": "https://127.0.0.1:9/never.py", "point": "storage"}
    code, out = lab.sup("--apply", json.dumps({"action": "add", "part": bad}), "--by", "chaos", timeout=120)
    last = json.loads(out.splitlines()[-1]) if out else {}
    p, lb = lab.boot(); lab.stop(p)
    return (code != 0 and not last.get("ok") and PART["id"] in lb["what"]), {"apply": last, "boot": lb, "head_before": head[:12]}


@scenario("clock moved back", "If the clock goes backwards, does the chain still append in order (position, never the clock)?")
def s_clock_back(lab):
    sys.path.insert(0, str(HERE))
    import hologram
    chain = hologram.Chain(lab.home / "chain.jsonl")
    real = hologram.utc_now
    hologram.utc_now = lambda: "2001-01-01T00:00:00.000Z"
    try:
        shape, t = chain.authorised()
        f = hologram.tile(chain, {"action": "safe_mode", "on": False}, {"kind": "process", "name": "chaos-clock"})
        hologram.gate(chain, f, "lab", True, {"why": "chaos: clock moved back"})
    finally:
        hologram.utc_now = real
    ok, why = hologram.replay(hologram.Chain(lab.home / "chain.jsonl"))
    return ok, {"replay": why, "tile_utc": f["utc"], "seq": f["seq"]}


SCENARIOS = [s_clean, s_tamper_part, s_missing_part, s_tamper_chain, s_kill_child, s_no_network, s_clock_back]


def seed(lab):
    """A fresh scratch chain with the one real part, gated in the lab the normal way."""
    if lab.home.exists():
        shutil.rmtree(lab.home)  # owner-checked: the chaos scratch home this script owns, never ~/.brainstem/layers
    lab.home.mkdir(parents=True)
    code, out = lab.sup("--no-probe", "--apply", json.dumps({"action": "add", "part": PART}), "--by", "chaos", timeout=300)
    last = json.loads(out.splitlines()[-1])
    if not last.get("ok"):
        raise SystemExit(f"seed failed: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--python", default=None)
    ap.add_argument("--home", default=None, help="scratch home (default: <GRAIL_LAYERS_HOME or ~/.brainstem/layers>/chaos/home)")
    ap.add_argument("--port", type=int, default=17072)
    ap.add_argument("--only", help="comma-separated scenario names to run")
    a = ap.parse_args()
    kernel = str(Path(a.kernel).expanduser().resolve())
    if not a.python:
        venv = Path(kernel).parent.parent.parent / "venv" / "bin" / "python"
        a.python = str(venv) if venv.is_file() else sys.executable
    live_home = Path(os.environ.get("GRAIL_LAYERS_HOME") or Path.home() / ".brainstem" / "layers")
    home = Path(a.home) if a.home else live_home / "chaos" / "home"
    assert home.resolve() != live_home.resolve(), "chaos never runs in the live home"
    lab = Lab(kernel, a.python, home, a.port)
    seed(lab)
    results, started = [], utc_now()
    for fn in SCENARIOS:
        if a.only and fn.scenario not in a.only.split(","):
            continue
        t0 = time.time()
        try:
            ok, ev = fn(lab)
        except Exception as e:
            ok, ev = False, {"error": repr(e)}
        results.append({"scenario": fn.scenario, "question": fn.question, "passed": bool(ok), "seconds": round(time.time() - t0, 1), "evidence": ev})
        print(("PASS " if ok else "FAIL ") + fn.scenario + f" ({results[-1]['seconds']}s)", flush=True)
    passed = sum(r["passed"] for r in results)
    report = {"schema": "brainstem-chaos/1", "started": started, "finished": utc_now(), "kernel": kernel,
              "passed": passed, "total": len(results), "results": results}
    out = live_home / "chaos" / f"{started.replace(':', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    (live_home / "chaos" / "latest.json").write_text(json.dumps(report, indent=1))
    print(f"{passed}/{len(results)} survived; report {out}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
