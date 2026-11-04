#!/usr/bin/env python3
"""packs.py — add several RAR packs to your ONE local Brainstem, all or nothing.

    python3 packs.py --kernel ~/.brainstem/src/rapp_brainstem/brainstem.py personal
    python3 packs.py --kernel ... night-watch private-mode           # any mix; starter packs expand
    python3 packs.py --kernel ... --dry-run business                  # say what would happen, change nothing

A pack (RAR stacks/<name>/pack.json) brings layers (install.parts, seeded ones only), agent files (*_agent.py),
and may include other packs (install.includes: a starter pack is a pack that only includes others).
Steps: resolve every pack and its includes -> refuse the whole selection on any conflict (two different layers on
one point, a kernel pin that is not your Brainstem's) -> one shape tile with every layer, booted in the lab and
probed (the gate) -> only then agents, through the Brainstem's own /agents/import with their sha256 -> restart.
Undo the layers with the supervisor's --undo; agents stay in agents/ (your folder) and can be deleted there.
Who did this before: Debian tasks/metapackages (a starter is just a list), apt Conflicts: (refuse before touching
anything), NixOS/Silverblue (one transaction, one undo).
"""
import argparse, hashlib, json, os, subprocess, sys, time, urllib.request
from pathlib import Path

RAR = os.environ.get("RAR_PAGES", "https://kody-w.github.io/RAR").rstrip("/")
HOME = Path(os.environ.get("GRAIL_LAYERS_HOME") or Path.home() / ".brainstem" / "layers")
KEYS = ("id", "kind", "point", "file", "sha256", "locator")


def get(url, binary=False):
    with urllib.request.urlopen(url + ("&" if "?" in url else "?") + f"t={int(time.time())}", timeout=60) as r:
        b = r.read()
    return b if binary else json.loads(b)


def resolve(names, packs, seen=None, out=None):
    seen, out = seen if seen is not None else set(), out if out is not None else []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        if n not in packs:
            raise SystemExit(f"There is no pack called '{n}'. See {RAR}/distros.html")
        pj = get(f"{RAR}/{packs[n]['path']}/pack.json")
        resolve((pj.get("install") or {}).get("includes") or [], packs, seen, out)  # what it includes comes first
        out.append((n, pj))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7071)))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("packs", nargs="+")
    a = ap.parse_args()
    kernel = Path(a.kernel).expanduser().resolve()
    ksha = hashlib.sha256(kernel.read_bytes()).hexdigest()
    reg = get(f"{RAR}/registry.json")
    packs = {p["name"]: p for p in reg.get("stack_packs", []) if p.get("_verified") is True}
    chosen = resolve(a.packs, packs)
    print("Packs:", ", ".join(n for n, _ in chosen))

    # present shape (what the device already runs)
    sys.path.insert(0, str(HOME))
    import hologram  # noqa: E402  (installed beside the supervisor)
    chain = hologram.Chain(HOME / "chain.jsonl")
    present = []
    if chain.frames:
        shape, _ = chain.authorised()
        present = [{k: p[k] for k in KEYS} for p in shape["parts"]]

    parts, agents, refusals = list(present), [], []
    for n, pj in chosen:
        pin = (pj.get("kernel") or {}).get("sha256")
        if pin and pin != ksha:
            refusals.append(f"{n} is built for a different Brainstem kernel ({pin[:12]}; yours is {ksha[:12]})")
        inst = pj.get("install") or {}
        seed = inst.get("seed")
        for part in inst.get("parts") or []:
            if seed is not None and part["id"] not in seed:
                continue
            part = {k: part[k] for k in KEYS}
            same_point = [p for p in parts if p["point"] == part["point"] and p["id"] != part["id"]]
            if same_point:
                refusals.append(f"{n} needs {part['id']} at the {part['point']} point, which {same_point[0]['id']} already holds")
                continue
            parts = [p for p in parts if p["id"] != part["id"]] + [part]
        for f, meta in (pj.get("files") or {}).items():
            if not f.endswith("_agent.py"):
                continue
            if any(x[2] == meta["sha256"] for x in agents):  # same bytes from two packs: one file (content-addressed)
                continue
            agents.append([f, f"{RAR}/{packs[n]['path']}/{f}", meta["sha256"], n])
    # Never overwrite: a name already taken (by another selected pack, or by a different file already in your
    # agents/ folder) gets the pack's name in front; same bytes already installed are skipped.
    adir = Path(os.environ.get("AGENTS_PATH") or kernel.parent / "agents")
    taken, keep = {}, []
    for x in agents:
        f, sha, n = x[0], x[2], x[3]
        on_disk = adir / f
        if on_disk.is_file() and hashlib.sha256(on_disk.read_bytes()).hexdigest() == sha:
            print(f"  {f} is already installed (same bytes)"); continue
        if f in taken or on_disk.is_file():
            scoped = n.replace("-", "_") + "__" + f
            print(f"  {f} from {n} is a different file from the one already named {f}; installing it as {scoped}")
            x[0] = f = scoped
        taken[f] = sha; keep.append(tuple(x))
    agents = keep
    if refusals:
        print("Nothing was installed:\n  - " + "\n  - ".join(refusals)); return 2
    new_parts = [p for p in parts if p not in present]
    print(f"Layers to add: {', '.join(p['id'] for p in new_parts) or 'none'}")
    print(f"Agents to add: {', '.join(f for f, *_ in agents) or 'none'}")
    if a.dry_run:
        return 0

    bodies = {}
    for f, url, sha, n in agents:  # fetch and check every agent BEFORE changing anything
        b = get(url, binary=True)
        if hashlib.sha256(b).hexdigest() != sha:
            print(f"Nothing was installed: {f} from {n} does not match its sha256."); return 2
        bodies[f] = (b, sha)

    sup = HOME / "grail_layers.py"
    if new_parts:
        r = subprocess.run([a.python, str(sup), "--kernel", str(kernel), "--python", a.python, "--by", "packs.py",
                            "--apply", json.dumps({"action": "set", "parts": parts})], capture_output=True, text=True, timeout=600)
        try:
            res = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            res = {"ok": False, "refused": (r.stderr or r.stdout)[-400:]}
        if not res.get("ok"):
            print(f"Nothing was installed: the layers did not pass the test boot ({res.get('refused') or res.get('why')})."); return 1
        print(f"Layers passed the test boot (tile #{res['tile']}, gate #{res['gate']}).")

    import uuid
    for f, (b, sha) in bodies.items():
        bd = uuid.uuid4().hex
        body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"sha256\"\r\n\r\n{sha}\r\n"
                f"--{bd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{f}\"\r\n"
                f"Content-Type: text/x-python\r\n\r\n").encode() + b + f"\r\n--{bd}--\r\n".encode()
        req = urllib.request.Request(f"http://127.0.0.1:{a.port}/agents/import", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={bd}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            print(f"  agent {f}: {json.loads(r.read()).get('status')}")

    record = HOME / "packs.json"
    have = json.loads(record.read_text()) if record.exists() else {"packs": []}
    have["packs"] = sorted(set(have["packs"]) | {n for n, _ in chosen})
    record.write_text(json.dumps(have, indent=1))

    if new_parts and not a.no_restart and sys.platform == "darwin":
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.brainstem.server"], capture_output=True)
        for _ in range(90):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{a.port}/health", timeout=2); break
            except Exception:
                time.sleep(1)
        time.sleep(2)
        boot = json.loads((HOME / "last-boot.json").read_text())
        print(f"Restarted: {boot.get('what')} ({'FELL BACK: ' if boot.get('fallback') else ''}{boot.get('why')})")
    elif new_parts:
        print("Restart your Brainstem to run the new layers.")
    print("Done. Your Brainstem now has: " + ", ".join(have["packs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
