#!/usr/bin/env python3
"""packs.py — install and reversibly remove RAR packages from your local Brainstem.

    python3 packs.py --kernel ~/.brainstem/src/rapp_brainstem/brainstem.py personal
    python3 packs.py --kernel ... night-watch private-mode           # any mix; starter packs expand
    python3 packs.py --kernel ... --dry-run business                  # say what would happen, change nothing
    python3 packs.py --kernel ... --remove personal                  # keep shared files and user layers

A pack (RAR stacks/<name>/pack.json) brings layers (install.parts, seeded ones only), agent files (*_agent.py),
and may include other packs (install.includes: a starter pack is a pack that only includes others).
Steps: resolve every pack and its includes -> refuse the whole selection on any conflict (two different layers on
one point, a kernel pin that is not your Brainstem's) -> one shape tile with every layer, booted in the lab and
probed (the gate) -> only then agents, through the Brainstem's own /agents/import with their sha256 -> restart.
Successful installs record ownership in HOME/packs.json. Remove uses that ledger, restores replaced files,
and moves unshared files to HOME/removed/<UTC stamp>/; changed files and legacy, untracked packs are refused.
Named packages are manual; includes are auto and removed when no remaining package needs them.
Who did this before: Debian tasks/metapackages (a starter is just a list), apt Conflicts: (refuse before touching
anything), NixOS/Silverblue (one transaction, one undo).
"""
import argparse, hashlib, json, os, re, signal, subprocess, sys, time, urllib.request, uuid
from datetime import datetime, timezone
from pathlib import Path
import urllib.error

RAR = os.environ.get("RAR_PAGES", "https://kody-w.github.io/RAR").rstrip("/")
KEYS = ("id", "kind", "point", "file", "sha256", "locator")
EXTRA = ("_sense.py", "_page.py", "_organ.py")  # organ is an alias of page
BASE = Path(os.environ.get("BRAINSTEM_HOME") or os.path.expanduser("~/.brainstem"))
HOME = Path(os.environ.get("GRAIL_LAYERS_HOME") or BASE / "layers")


def extra_dir(f):
    if f.endswith("_sense.py"):
        return Path(os.environ.get("BRAINSTEM_SENSES_DIR") or BASE / "senses")
    return Path(os.environ.get("BRAINSTEM_PAGES_DIR") or BASE / "pages")


def get(url, binary=False):
    target = url if url.startswith("file:") else url + ("&" if "?" in url else "?") + f"t={int(time.time())}"
    with urllib.request.urlopen(target, timeout=60) as r:
        b = r.read()
    return b if binary else json.loads(b)


_peek = {}


def bodies_peek(url):
    if url not in _peek:
        _peek[url] = get(url, binary=True)
    return _peek[url]


def _tool(b):
    """The agent's tool name (what the kernel registers): same name = same agent, so a new file upgrades it."""
    import re
    m = re.search(rb'self\.name\s*=\s*["\']([^"\']+)["\']', b)
    return m.group(1) if m else None


def resolve(names, packs, seen=None, out=None):
    seen, out = seen if seen is not None else set(), out if out is not None else []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        if n not in packs:
            raise SystemExit(f"There is no pack called '{n}'. See {RAR}/distros.html")
        pj = get(f"{RAR}/{packs[n]['path']}/pack.json")
        resolve(sorted((pj.get("install") or {}).get("includes") or []), packs, seen, out)  # includes first, sorted
        out.append((n, pj))
    return out


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_path(name, directory):
    if Path(name).name != name or not name.endswith(("_agent.py",) + EXTRA):
        raise ValueError(f"unsafe package file name: {name}")
    path = Path(directory).expanduser().absolute() / name
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ValueError(f"package file uses a symbolic link: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"package file is not a regular file: {path}")
    return path


class Changes:
    """Keep displaced bytes on disk; reverse every completed move on failure."""
    def __init__(self):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.archive = HOME / "removed" / (stamp + "-" + uuid.uuid4().hex[:8])
        self.moves = []
        self.temporary = set()

    def move(self, source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        self.moves.append((source, target))

    def save(self, path):
        target = self.archive / path.name
        if target.exists():
            target = self.archive / str(len(self.moves)) / path.name
        self.move(path, target)
        return target

    def write(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.save(path)
        temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
        self.temporary.add(temporary)
        try:
            temporary.write_bytes(body)
            self.move(temporary, path)
        except OSError:
            if temporary.exists():
                self.save(temporary)
            raise

    def rollback(self):
        for source, target in reversed(self.moves):
            source.parent.mkdir(parents=True, exist_ok=True)
            target.rename(source)
            # A staged write has no original owner; preserve it in the removal archive.
            if source in self.temporary:
                destination = self.archive / "rolled-back" / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.rename(destination)


def apply_layers(a, kernel, parts):
    result = subprocess.run(
        [a.python, str(HOME / "grail_layers.py"), "--kernel", str(kernel), "--python", a.python,
         "--by", "packs.py", "--apply", json.dumps({"action": "set", "parts": parts})],
        capture_output=True, text=True, timeout=600)
    try:
        reply = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise ValueError(f"the layer test boot returned no valid result: {(result.stderr or result.stdout)[-400:]}") from error
    if result.returncode or not isinstance(reply, dict) or not reply.get("ok"):
        raise ValueError(f"the layers did not pass the test boot: {reply}")


def handover(a):
    if a.no_restart:
        print("Restart your Brainstem to run the new layers.")
        return
    pidf = HOME / "supervisor.pid"
    if not pidf.exists():
        print("The new layers are saved; they take effect the next time the Brainstem starts (nothing was restarted).")
        return
    pid = int(pidf.read_text())
    if pid <= 1:
        raise ValueError("the supervisor pid must name one running process")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        print("The supervisor is not running; the saved layers take effect at the next start.")
        return
    boot = HOME / "last-boot.json"
    before = boot.stat().st_mtime_ns if boot.exists() else 0
    os.kill(pid, signal.SIGHUP)
    for _ in range(300):
        if boot.exists() and boot.stat().st_mtime_ns > before:
            result = json.loads(boot.read_text())
            if result.get("fallback"):
                raise ValueError(f"the handover failed: {result.get('why')}")
            print("Brainstem: " + " ".join(result.get("notices", [])))
            return
        time.sleep(1)
    raise TimeoutError("the supervisor did not finish its handover within 300 seconds")


def snapshot_state():
    return {name: (HOME / name).read_bytes() if (HOME / name).exists() else None
            for name in ("chain.jsonl", "last-boot.json")}


def restore_state(before):
    changes = Changes()
    for name, body in before.items():
        path = HOME / name
        if body is None:
            if path.exists():
                changes.save(path)
        elif not path.exists() or path.read_bytes() != body:
            changes.write(path, body)


def failed(verb, error, changes, before, a, switched):
    try:
        changes.rollback()
        restore_state(before)
        if switched and not a.no_restart:
            try:
                handover(a)
            finally:
                restore_state(before)
    except (OSError, ValueError, subprocess.SubprocessError) as recovery_error:
        print(f"Could not finish: {error}; rollback needs attention: {recovery_error}")
        return 1
    print(f"Nothing was {verb}: {error}")
    return 1


def required_by(installed):
    users = {name: [] for name in installed}
    for name, entry in installed.items():
        for dependency in entry.get("includes", []):
            if dependency in users:
                users[dependency].append(name)
    return {name: sorted(set(parents)) for name, parents in users.items()}


def install_ledger(chosen, catalog, reg, agents, extras, adir, skip, present, have, changes, requested):
    installed = have.setdefault("installed", {})
    previous = [(f, meta) for entry in installed.values() for kind in ("agents", "extras")
                for f, meta in entry.get(kind, {}).items()]
    owned_layers = {p for entry in installed.values() for p in entry.get("layers", [])}
    present_ids = {p["id"] for p in present}
    sources = [(n, pj.get("files", {}), pj.get("install") or {}) for n, pj in chosen]
    for n in catalog:
        e = next(e for e in reg["agents"] if e["name"] == n)
        sources.append((n, {e["_file"].rsplit("/", 1)[-1]: {"sha256": e["_sha256"]}}, {}))
    backups = {}
    for name, files, inst in sources:
        old = installed.get(name, {})
        entry = {"agents": dict(old.get("agents", {})), "extras": dict(old.get("extras", {})),
                 "layers": list(old.get("layers", [])),
                 "mark": "manual" if name in requested or (
                     name in have.get("packs", []) and old.get("mark", "manual") == "manual") else "auto",
                 "includes": sorted(set(inst.get("includes") or []))}
        for part in inst.get("parts") or []:
            pid = part["id"]
            if (pid not in skip and (inst.get("seed") is None or pid in inst["seed"])
                    and (pid not in present_ids or pid in owned_layers)):
                if pid not in entry["layers"]:
                    entry["layers"].append(pid)
        for f, info in files.items():
            if f in skip or not f.endswith(("_agent.py",) + EXTRA):
                continue
            kind = "agents" if f.endswith("_agent.py") else "extras"
            candidates = agents if kind == "agents" else extras
            match = next((x for x in candidates if x[2] == info["sha256"]), None)
            actual = match[0] if match else f
            path = file_path(actual, adir if kind == "agents" else extra_dir(actual))
            sha = info["sha256"]
            if not match and (not path.is_file() or digest(path) != sha):
                continue
            key = (path, sha)
            if key not in backups:
                shared = next((meta for old_f, meta in previous if old_f == actual
                               and Path(meta["dir"]) == path.parent and meta["sha256"] == sha), None)
                backup = shared.get("replaced_backup") if shared else None
                if not shared and path.exists():
                    # Preserve even byte-identical user files; adopting them must not make them disposable.
                    backup_path = HOME / ("agent-backups" if kind == "agents" else "extra-backups") / (
                        actual + "." + digest(path) + "." + uuid.uuid4().hex)
                    changes.write(backup_path, path.read_bytes())
                    backup = str(backup_path)
                backups[key] = backup
            entry[kind][actual] = {"sha256": sha, "dir": str(path.parent), "replaced_backup": backups[key]}
        installed[name] = entry
    for name, parents in required_by(installed).items():
        if "mark" in installed[name]:
            installed[name]["required_by"] = parents
    have["packs"] = sorted(set(have.get("packs", [])) | {n for n, _, _ in sources})
    return have


def remove(a, kernel, present=None):
    record = HOME / "packs.json"
    have = json.loads(record.read_text()) if record.exists() else {"packs": []}
    installed = have.get("installed", {})
    names = sorted(set(a.remove))
    for name in names:
        if name not in installed:
            print(f"Cannot remove {name}: no install ledger is available; nothing changed.")
            return 2
    remaining = {n: entry for n, entry in installed.items() if n not in names}
    auto_removed = {}
    original_users = required_by(installed)
    while True:
        users = required_by(remaining)
        orphans = sorted(n for n, entry in remaining.items()
                         if entry.get("mark") == "auto" and not users[n])
        if not orphans:
            break
        for name in orphans:
            auto_removed[name] = original_users[name]
            remaining.pop(name)
    names = sorted(set(names) | set(auto_removed))
    for name, parents in users.items():
        if "mark" in remaining[name]:
            remaining[name]["required_by"] = parents
    if present is None:
        sys.path.insert(0, str(HOME))
        import hologram
        chain = hologram.Chain(HOME / "chain.jsonl")
        shape, _ = chain.authorised()
        present = [{k: p[k] for k in KEYS} for p in shape["parts"]] if shape else []
    owners = {}
    for name, entry in remaining.items():
        for kind in ("agents", "extras"):
            for f, meta in entry.get(kind, {}).items():
                path = file_path(f, meta["dir"])
                owners.setdefault(path, []).append((name, meta))
    removed_files, actions = {}, {}
    kept = [f"{n}: still needed by {', '.join(users[n])}" for n, entry in remaining.items()
            if entry.get("mark") == "auto" and users[n]]
    for name in names:
        for kind in ("agents", "extras"):
            for f, meta in installed[name].get(kind, {}).items():
                path = file_path(f, meta["dir"])
                removed_files.setdefault(path, {})[meta["sha256"]] = meta

    def original_backup(path, meta):
        backup, seen = meta.get("replaced_backup"), set()
        while backup:
            bpath = Path(backup)
            if (not bpath.is_file() or bpath.is_symlink()
                    or not bpath.resolve().is_relative_to(HOME.resolve())):
                raise ValueError(f"{path.name}'s original backup is missing or unsafe")
            prior_sha = digest(bpath)
            if any(m["sha256"] == prior_sha for _, m in owners.get(path, [])):
                break
            prior = removed_files[path].get(prior_sha)
            if not prior:
                break
            if backup in seen:
                raise ValueError(f"{path.name}'s backup history contains a cycle")
            seen.add(backup)
            # A byte-identical pre-existing user file is its own original, not an upgrade.
            if prior_sha == meta["sha256"]:
                break
            meta, backup = prior, prior.get("replaced_backup")
        return backup

    for path, versions in removed_files.items():
        if not path.is_file():
            raise ValueError(f"{path.name} has changed or is missing; nothing was removed")
        current = digest(path)
        users = owners.get(path, [])
        if any(m["sha256"] == current for _, m in users):
            kept.append(f"{path.name}: still used by {', '.join(sorted(n for n, m in users if m['sha256'] == current))}")
            for _, meta in users:
                meta["replaced_backup"] = original_backup(path, meta)
        elif current in versions:
            meta = dict(versions[current])
            meta["replaced_backup"] = original_backup(path, meta)
            actions[path] = meta
        else:
            raise ValueError(f"{path.name} has changed or is missing; nothing was removed")
    owned_layers = {p for entry in installed.values() for p in entry.get("layers", [])}
    remaining_layers = {p for entry in remaining.values() for p in entry.get("layers", [])}
    parts = [p for p in present if p["id"] not in owned_layers or p["id"] in remaining_layers]
    for p in present:
        if p["id"] in remaining_layers:
            users = sorted(n for n, entry in remaining.items() if p["id"] in entry.get("layers", []))
            kept.append(f"{p['id']}: still used by {', '.join(users)}")
        elif p["id"] not in owned_layers:
            kept.append(f"{p['id']}: kept because no pack installed it")
        else:
            import hologram
            path = hologram.part_path(HOME / "parts", p)
            if path.is_symlink() or not path.resolve().is_relative_to((HOME / "parts").resolve()):
                raise ValueError(f"{p['id']} has an unsafe part path")
            if path.exists():
                if not path.is_file() or digest(path) != p["sha256"]:
                    raise ValueError(f"{p['id']} has changed; nothing was removed")
                actions[path] = {"sha256": p["sha256"], "replaced_backup": None}
    plan = {"remove": names, "files": [
        {"file": str(path), "action": "restore" if meta.get("replaced_backup") else "move",
         "replaced_backup": meta.get("replaced_backup")} for path, meta in sorted(actions.items())],
        "layers": [p["id"] for p in parts], "kept": sorted(set(kept)),
        "auto_removed": auto_removed}
    if a.plan_json:
        print(json.dumps(plan, sort_keys=True))
        return 0
    if a.dry_run:
        print("Would remove: " + ", ".join(names))
        for item in plan["files"]:
            print(f"  Would {item['action']} {item['file']}")
        for message in plan["kept"]:
            print("  " + message)
        return 0
    changes, before = Changes(), snapshot_state()
    switched = False
    try:
        if parts != present:
            apply_layers(a, kernel, parts)
        for path, meta in actions.items():
            changes.save(path)
            if meta.get("replaced_backup"):
                changes.move(Path(meta["replaced_backup"]), path)
        if parts != present:
            switched = True
            handover(a)
        have["packs"] = [n for n in have.get("packs", []) if n not in names]
        have["installed"] = remaining
        changes.write(record, json.dumps(have, indent=1).encode())
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        return failed("removed", error, changes, before, a, switched)
    print("Removed: " + ", ".join(names))
    for name, parents in sorted(auto_removed.items()):
        reason = "only " + ", ".join(parents) + " needed it" if parents else "no remaining pack needed it"
        print(f"{name}: removed ({reason})")
    for path, meta in actions.items():
        print(f"  {path.name}: " + ("original restored" if meta.get("replaced_backup") else f"moved to {changes.archive}"))
    for message in plan["kept"]:
        print("  " + message)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7071)))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-restart", action="store_true")
    ap.add_argument("--skip", action="append", default=[], help="a card to leave out: an agent file or a layer id (repeatable)")
    ap.add_argument("--plan-json", action="store_true", help="print the plan as JSON and change nothing")
    ap.add_argument("--remove", nargs="+", metavar="PACK", help="remove only these named packages, retaining shared files")
    ap.add_argument("packs", nargs="*")
    a = ap.parse_args()
    if bool(a.remove) == bool(a.packs):
        ap.error("choose either packs to install or --remove PACK [PACK ...]")
    kernel = Path(a.kernel).expanduser().resolve()
    if a.remove:
        try:
            return remove(a, kernel)
        except (OSError, ValueError) as error:
            print(f"Nothing was removed: {error}")
            return 1
    ksha = hashlib.sha256(kernel.read_bytes()).hexdigest()
    reg = get(f"{RAR}/registry.json")
    packs = {p["name"]: p for p in reg.get("stack_packs", []) if p.get("_verified") is True}
    catalog = sorted({x for x in a.packs if x.startswith("@")})  # one cart: store agents by name (@publisher/name_agent)
    chosen = resolve(sorted(set(a.packs) - set(catalog)), packs)  # any order, any duplicates in -> one canonical order out
    skip = set(a.skip)
    if a.plan_json:
        a.dry_run = True
    say = (lambda *x, **k: None) if a.plan_json else print
    say("Packs:", ", ".join(n for n, _ in chosen))

    # present shape (what the device already runs)
    sys.path.insert(0, str(HOME))
    import hologram  # noqa: E402  (installed beside the supervisor)
    chain = hologram.Chain(HOME / "chain.jsonl")
    present = []
    if chain.frames:
        shape, _ = chain.authorised()
        present = [{k: p[k] for k in KEYS} for p in shape["parts"]]

    parts, agents, refusals, extras = list(present), [], [], []
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
            if part["id"] in skip:
                continue
            same_point = [p for p in parts if p["point"] == part["point"] and p["id"] != part["id"]]
            if same_point:
                refusals.append(f"{n} needs {part['id']} at the {part['point']} point, which {same_point[0]['id']} already holds")
                continue
            parts = [p for p in parts if p["id"] != part["id"]] + [part]
        for f, meta in (pj.get("files") or {}).items():
            if f.endswith(EXTRA):  # senses and pages: files in their own folders, same checks as agents
                if f not in skip and not any(x[2] == meta["sha256"] for x in extras):
                    extras.append([f, f"{RAR}/{packs[n]['path']}/{f}", meta["sha256"], n])
                continue
            if not f.endswith("_agent.py"):
                continue
            if f in skip or any(x[2] == meta["sha256"] for x in agents):  # same bytes from two packs: one file (content-addressed)
                continue
            agents.append([f, f"{RAR}/{packs[n]['path']}/{f}", meta["sha256"], n])
    by_name = {x.get("name"): x for x in reg.get("agents", [])}
    for name in catalog:
        e = by_name.get(name)
        if not e or not e.get("_file") or not e.get("_sha256"):
            refusals.append(f"{name} is not in the RAR catalog"); continue
        f = e["_file"].rsplit("/", 1)[1]
        if f in skip or any(x[2] == e["_sha256"] for x in agents):
            continue
        agents.append([f, f"{RAR}/{e['_file']}", e["_sha256"], name])
    # Never overwrite: a name already taken (by another selected pack, or by a different file already in your
    # agents/ folder) gets the pack's name in front; same bytes already installed are skipped.
    adir = Path(os.environ.get("AGENTS_PATH") or kernel.parent / "agents")
    taken, keep = {}, []
    for x in agents:
        f, sha, n = x[0], x[2], x[3]
        on_disk = adir / f
        if on_disk.is_file() and hashlib.sha256(on_disk.read_bytes()).hexdigest() == sha:
            say(f"  {f} is already installed (same bytes)"); continue
        if on_disk.is_file() and f not in taken and _tool(on_disk.read_bytes()) == _tool(bodies_peek(x[1])):
            back = HOME / "agent-backups"
            say(f"  {f}: newer version of the same agent; the old copy is kept in {back}")
        elif f in taken or on_disk.is_file():
            scoped = re.sub(r"\W", "_", n).strip("_") + "__" + f
            say(f"  {f} from {n} is a different file from the one already named {f}; installing it as {scoped}")
            x[0] = f = scoped
        taken[f] = sha; keep.append(tuple(x))
    agents = keep
    POINT_ORDER = ["kernel", "auth", "provider", "storage", "agents", "transport"]  # one canonical layer order
    parts = sorted(parts, key=lambda p: (POINT_ORDER.index(p["point"]) if p["point"] in POINT_ORDER else 99, p["id"]))
    warn = []
    for n, pj in chosen:  # a kept card that needs a skipped one still installs, with a plain warning
        for part in (pj.get("install") or {}).get("parts") or []:
            if part["id"] in skip and any(i for i in (pj.get("install") or {}).get("includes") or []):
                warn.append(f"you left out {part['id']}; packs that include {n} may not work fully without it")
    for n, pj in chosen:
        for inc in (pj.get("install") or {}).get("includes") or []:
            dpj = dict(chosen).get(inc) or {}
            for part in (dpj.get("install") or {}).get("parts") or []:
                if part["id"] in skip:
                    warn.append(f"{n} relies on {part['id']} from {inc}, which you left out")
    warn = sorted(set(warn))
    if a.plan_json:
        print(json.dumps({"packs": [n for n, _ in chosen], "refused": refusals, "warnings": warn,
                          "layers": [p["id"] + "@" + p["sha256"][:12] for p in parts],
                          "agents": sorted([f, sha[:12], n] for f, _, sha, n in agents),
                          "extras": sorted([f, sha[:12], n] for f, _, sha, n in extras)}, sort_keys=True))
        return 2 if refusals else 0
    for w in warn:
        print("  Note:", w)
    if refusals:
        print("Nothing was installed:\n  - " + "\n  - ".join(refusals)); return 2
    new_parts = [p for p in parts if p not in present]
    print(f"Layers to add: {', '.join(p['id'] for p in new_parts) or 'none'}")
    print(f"Agents to add: {', '.join(f for f, *_ in agents) or 'none'}")
    print(f"Senses and pages to add: {', '.join(f for f, *_ in extras) or 'none'}")
    if a.dry_run:
        return 0

    bodies = {}
    for f, url, sha, n in agents:  # fetch and check every agent BEFORE changing anything
        b = bodies_peek(url)
        if hashlib.sha256(b).hexdigest() != sha:
            print(f"Nothing was installed: {f} from {n} does not match its sha256."); return 2
        if f in bodies and bodies[f][1] != sha:
            print(f"Nothing was installed: two packages supply different bytes for {f}."); return 2
        bodies[f] = (b, sha)
    xbodies = {}
    for f, url, sha, n in extras:
        b = bodies_peek(url)
        if hashlib.sha256(b).hexdigest() != sha:
            print(f"Nothing was installed: {f} from {n} does not match its sha256."); return 2
        if f in xbodies and xbodies[f] != b:
            print(f"Nothing was installed: two packages supply different bytes for {f}."); return 2
        xbodies[f] = b

    record = HOME / "packs.json"
    have = json.loads(record.read_text()) if record.exists() else {"packs": []}
    changes, before = Changes(), snapshot_state()
    switched = False
    try:
        have = install_ledger(chosen, catalog, reg, agents, extras, adir, skip, present, have, changes,
                              set(a.packs))
        if new_parts:
            apply_layers(a, kernel, parts)
        for f, (b, sha) in bodies.items():
            path = file_path(f, adir)
            if path.exists():
                changes.save(path)
            bd = uuid.uuid4().hex
            body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"sha256\"\r\n\r\n{sha}\r\n"
                    f"--{bd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{f}\"\r\n"
                    f"Content-Type: text/x-python\r\n\r\n").encode() + b + f"\r\n--{bd}--\r\n".encode()
            req = urllib.request.Request(f"http://127.0.0.1:{a.port}/agents/import", data=body, method="POST",
                                         headers={"Content-Type": f"multipart/form-data; boundary={bd}"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    reply = json.loads(r.read())
                if (not isinstance(reply, dict) or reply.get("status") in ("error", "failed", "refused")
                        or reply.get("ok") is False):
                    raise ValueError(f"{f}: agent import refused: {reply}")
                if not path.is_file() or digest(path) != sha:
                    raise ValueError(f"{f}: the agent import did not write the expected bytes to {path}")
                print(f"  agent {f}: {reply.get('status')}")
            finally:
                # The endpoint writes outside Changes, including when its response fails.
                if path.exists():
                    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
                    changes.temporary.add(temporary)
                    changes.moves.append((temporary, path))
        for f, b in xbodies.items():
            path = file_path(f, extra_dir(f))
            if path.is_file() and path.read_bytes() == b:
                print(f"  {f}: already installed")
                continue
            changes.write(path, b)
            print(f"  {f}: installed in {path.parent}")
        if new_parts:
            switched = True
            handover(a)
        changes.write(record, json.dumps(have, indent=1).encode())
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        return failed("installed", error, changes, before, a, switched)
    print("Done. Your Brainstem now has: " + ", ".join(have["packs"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # never a traceback, never a half plan
        print(f"Nothing was changed: could not reach the catalog or your Brainstem ({getattr(e, 'reason', e)}). Try again in a minute.")
        sys.exit(3)
