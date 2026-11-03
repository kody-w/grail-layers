"""succession_drill: rehearse the hand-off on a SCRATCH chain with throwaway keys, end to end.

The product promise: "if something happens to me, my heir's Brainstem boots from my chain and carries on." A promise
that is only described is not kept, so this drill performs it every night beside the chaos drill:

  1. mint an owner key and a successor key (scratch, thrown away)
  2. genesis + a shape, then the owner names the successor (30 days silence, 7 to confirm) and pulses
  3. a stranger's hand-off is refused; the successor's hand-off BEFORE the window is refused
  4. the successor's hand-off with `at` past the window is accepted; the chain says the heir owns it
  5. the heir's Brainstem boots from that chain on a lab port and answers "who owns this" from the chain
  6. replay still holds; the shape chain is untouched by succession frames

  python3 succession_drill.py --kernel <brainstem.py> [--python venv-python] [--home dir] [--port 17078]
Report: <live home>/succession/latest.json (and a dated copy). Never runs in the live home.
"""
import argparse
import datetime
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hologram  # noqa: E402
import succession  # noqa: E402


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def step(results, name, ok, evidence):
    results.append({"step": name, "passed": bool(ok), "evidence": evidence})
    print(("PASS " if ok else "FAIL ") + name, flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--python", default=None)
    ap.add_argument("--home", default=None)
    ap.add_argument("--port", type=int, default=17078)
    a = ap.parse_args()
    kernel = str(Path(a.kernel).expanduser().resolve())
    if not a.python:
        venv = Path(kernel).parent.parent.parent / "venv" / "bin" / "python"
        a.python = str(venv) if venv.is_file() else sys.executable
    live_home = Path(os.environ.get("GRAIL_LAYERS_HOME") or Path.home() / ".brainstem" / "layers")
    home = Path(a.home) if a.home else live_home / "succession" / "home"
    assert home.resolve() != live_home.resolve(), "the drill never runs in the live home"
    if home.exists():
        shutil.rmtree(home)  # owner-checked: the drill's own scratch home, never ~/.brainstem/layers
    home.mkdir(parents=True)
    results, started = [], utc_now()

    # 1. keys (scratch)
    owner = succession.mint_key(home / "keys" / "owner.pem", "drill", "owner", "Owner")
    heir = succession.mint_key(home / "keys" / "heir.pem", "drill", "successor", "Heir")
    stranger = succession.mint_key(home / "keys" / "stranger.pem", "drill", "successor", "Stranger")
    step(results, "keys minted (private files mode 600, public records only in the chain)",
         oct((home / "keys" / "owner.pem").stat().st_mode & 0o777) == "0o600", {"owner": owner["rappid"][:40]})

    # 2. chain: genesis, name, pulse
    chain = hologram.genesis(home / "chain.jsonl", kernel)
    f_name = succession.name_successor(chain, home / "keys" / "owner.pem", owner, heir, {"silence_days": 30, "confirm_days": 7})
    f_pulse = succession.pulse(chain, home / "keys" / "owner.pem", owner)
    st = succession.state(chain.frames)
    step(results, "owner named the heir and pulsed; chain says owned", st["status"] == "owned" and st["successor"]["name"] == "Heir",
         {"frames": [f_name["seq"], f_pulse["seq"]], "says": succession.describe(st)})

    # 3. refusals
    try:
        succession.handoff(chain, home / "keys" / "stranger.pem", stranger, "silence",
                           at=succession._fmt_utc(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=60)))
        stranger_refused = False
    except ValueError:
        stranger_refused = True  # build_frame/verify may accept the frame shape; validate() must refuse the signer
    bad = hologram.Chain(home / "chain.jsonl")
    st_bad = succession.state(bad.frames)
    # if the stranger's frame got appended, the chain's own validation must refuse it; either way the heir is not displaced
    stranger_blocked = stranger_refused or (st_bad["status"] == "refused" and "not signed by the named successor" in st_bad["why"])
    step(results, "a stranger's hand-off is refused", stranger_blocked, {"state": st_bad})
    if not stranger_refused:  # drop the forged frame from the scratch chain to continue the rehearsal (a real device would refuse the file)
        lines = (home / "chain.jsonl").read_text().splitlines(True)
        (home / "chain.jsonl").write_text("".join(lines[:-1]))
        chain = hologram.Chain(home / "chain.jsonl")
    try:
        succession.handoff(chain, home / "keys" / "heir.pem", heir, "silence")  # now: before the window
        early_ok = False
    except ValueError:
        early_ok = True
    if not early_ok:
        st_e = succession.state(hologram.Chain(home / "chain.jsonl").frames)
        early_ok = st_e["status"] == "refused" and "before the window" in st_e["why"]
        lines = (home / "chain.jsonl").read_text().splitlines(True)
        (home / "chain.jsonl").write_text("".join(lines[:-1]))
        chain = hologram.Chain(home / "chain.jsonl")
    step(results, "the heir's hand-off BEFORE the window is refused", early_ok, {})

    # 4. the window closes (the heir states `at` 38 days on; the frame carries that time)
    at = succession._fmt_utc(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=38))
    f_h = succession.handoff(chain, home / "keys" / "heir.pem", heir, "silence", at=at)
    st2 = succession.state(chain.frames, now_utc=at)
    step(results, "after the window the heir takes over; chain says inherited", st2["status"] == "inherited" and st2["owner"]["name"] == "Heir",
         {"frame": f_h["seq"], "says": succession.describe(st2)})

    # 5. the heir's Brainstem boots from this chain and answers from it
    env = dict(os.environ, GRAIL_LAYERS_HOME=str(home), GRAIL_LAB_PORT=str(a.port + 1))
    p = subprocess.Popen([sys.executable, str(HERE / "grail_layers.py"), "--kernel", kernel, "--python", a.python,
                          "--port", str(a.port), "--no-probe"], env=env, stdout=open(home / "sup.log", "a"), stderr=subprocess.STDOUT)
    booted = False
    for _ in range(120):
        if (home / "last-boot.json").is_file():
            booted = True; break
        if p.poll() is not None:
            break
        time.sleep(1)
    who = subprocess.run([sys.executable, str(HERE / "grail_layers.py"), "--kernel", kernel, "--who"], env=env,
                         capture_output=True, text=True).stdout.strip()
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()
    step(results, "the heir's Brainstem boots from the chain and says who owns it", booted and "Owned by Heir" in who, {"who": who})

    # 6. replay and shape untouched
    ok, why = hologram.replay(hologram.Chain(home / "chain.jsonl"))
    shape, _ = hologram.Chain(home / "chain.jsonl").authorised()
    step(results, "replay holds and the shape is untouched by succession frames", ok and shape["parts"] == [], {"replay": why})

    passed = sum(r["passed"] for r in results)
    report = {"schema": "brainstem-succession-drill/1", "started": started, "finished": utc_now(), "kernel": kernel,
              "passed": passed, "total": len(results), "results": results}
    out = live_home / "succession" / f"{started.replace(':', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1)); (live_home / "succession" / "latest.json").write_text(json.dumps(report, indent=1))
    shutil.rmtree(home / "keys")  # owner-checked: throwaway drill keys in the drill's scratch home
    print(f"{passed}/{len(results)} rehearsed; report {out}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
