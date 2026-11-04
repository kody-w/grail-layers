"""Offline package lifecycle tests using fixture RAR files and a multipart kernel stub."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "remove"
SPEC = importlib.util.spec_from_file_location("packs_under_test", ROOT / "packs.py")
packs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packs)
sys.path.insert(0, str(ROOT))
import hologram


def sha(body):
    return hashlib.sha256(body).hexdigest()


def hashes(directory):
    return {str(p.relative_to(directory)): sha(p.read_bytes())
            for p in directory.rglob("*") if p.is_file()}


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "layers"
    home.mkdir()
    agents = tmp_path / "agents"
    agents.mkdir()
    senses, pages = tmp_path / "senses", tmp_path / "pages"
    senses.mkdir()
    pages.mkdir()
    kernel = tmp_path / "brainstem.py"
    kernel.write_text("# offline kernel stub\n")
    (tmp_path / "soul.md").write_bytes(b"my soul\x00\n")
    (tmp_path / ".brainstem_data").mkdir()
    (tmp_path / ".brainstem_data" / "user.json").write_bytes(b'{"keep": true}\n')
    (tmp_path / ".brainstem_model").write_bytes(b"my model\n")
    (tmp_path / ".env").write_bytes(b"NOT_A_SECRET=fixture\n")
    files = {
        "A": {"x_agent.py": "x_agent.py", "pulse_sense.py": "pulse_sense.py"},
        "B": {"x_agent.py": "x_agent.py", "pulse_sense.py": "pulse_sense.py"},
        "C": {"x_agent.py": "replacement_agent.py", "view_page.py": "view_page.py"},
        "D": {"x_agent.py": "x_agent.py", "view_page.py": "view_page.py"},
        "Starter": {},
        "Layer": {"x_agent.py": "x_agent.py"},
        "LayerB": {},
        "private-mode": {"private_mode_agent.py": "x_agent.py"},
        "money-watch": {"money_agent.py": "replacement_agent.py"},
        "starter-personal": {},
        "senses": {"pulse_sense.py": "pulse_sense.py"},
        "Nested": {},
    }
    part = {"id": "@fixture/layer", "kind": "layer", "point": "agents",
            "file": "layer.py", "sha256": sha(b"layer"), "locator": "https://fixture.invalid/layer.py"}
    manifests = {}
    for name, members in files.items():
        inst = {}
        if name == "Starter":
            inst["includes"] = ["A", "B"]
        if name in ("money-watch", "starter-personal"):
            inst["includes"] = ["private-mode"]
        if name == "Nested":
            inst["includes"] = ["money-watch"]
        if name in ("Layer", "LayerB"):
            inst["parts"] = [part]
        manifests[name] = {"files": {f: {"sha256": sha((FIXTURES / source).read_bytes())}
                                    for f, source in members.items()}, "install": inst}
    state = SimpleNamespace(home=home, agents=agents, senses=senses, pages=pages, root=tmp_path,
                            kernel=kernel, manifests=manifests, part=part, imports=0, fail_import=0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, body, code=200):
            body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0].strip("/")
            if path == "health":
                self.reply({"status": "ok"})
            elif path == "registry.json":
                self.reply({"stack_packs": [{"name": n, "path": n, "_verified": True} for n in files],
                            "agents": [{"name": "@fixture/x_agent", "_file": "A/x_agent.py",
                                        "_sha256": manifests["A"]["files"]["x_agent.py"]["sha256"]}]})
            else:
                name, filename = path.split("/", 1)
                if filename == "pack.json":
                    self.reply(manifests[name])
                else:
                    body = (FIXTURES / files[name][filename]).read_bytes()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body)

        def do_POST(self):
            assert self.path == "/agents/import"
            body = self.rfile.read(int(self.headers["Content-Length"]))
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
            fields = {}
            for field in message.iter_parts():
                fields[field.get_param("name", header="content-disposition")] = field
            content = fields["file"].get_payload(decode=True)
            filename = fields["file"].get_filename()
            assert sha(content) == fields["sha256"].get_content()
            (agents / filename).write_bytes(content)
            state.imports += 1
            if state.fail_import == state.imports:
                self.reply({"status": "error"}, 500)
            else:
                self.reply({"status": "imported"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    env = dict(os.environ, RAR_PAGES=f"http://127.0.0.1:{port}", BRAINSTEM_HOME=str(tmp_path),
               GRAIL_LAYERS_HOME=str(home), AGENTS_PATH=str(agents),
               BRAINSTEM_SENSES_DIR=str(senses), BRAINSTEM_PAGES_DIR=str(pages))
    for key in ("HOME", "BASE"):
        monkeypatch.setattr(packs, key, home if key == "HOME" else tmp_path)
    for key, value in env.items():
        if key.startswith(("BRAINSTEM_", "GRAIL_", "AGENTS_")):
            monkeypatch.setenv(key, value)
    state.env = env
    state.args = SimpleNamespace(remove=[], plan_json=False, dry_run=False, no_restart=True,
                                 python=sys.executable)

    def run(*args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / "packs.py"), "--kernel", str(kernel),
                                 "--port", str(port), *args], env=env, capture_output=True, text=True)
        if ok:
            assert result.returncode == 0, result.stdout + result.stderr
        else:
            assert result.returncode != 0, result.stdout + result.stderr
        return result

    state.run = run
    state.ledger = lambda: json.loads((home / "packs.json").read_text())
    state.protected = lambda: {str(p.relative_to(tmp_path)): p.read_bytes() for p in (
        tmp_path / "soul.md", tmp_path / ".brainstem_data" / "user.json",
        tmp_path / ".brainstem_model", tmp_path / ".env")}
    # Real shape-chain operations, without booting a model or accessing the network.
    (home / "grail_layers.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import hologram\n"
        "home = Path(__file__).parent\n"
        "args = sys.argv\n"
        "assert args[args.index('--by') + 1] == 'packs.py'\n"
        "params = json.loads(args[args.index('--apply') + 1])\n"
        "kernel = Path(args[args.index('--kernel') + 1])\n"
        "chain = hologram.genesis(home / 'chain.jsonl', kernel)\n"
        "tile = hologram.tile(chain, params, {'kind': 'process', 'name': 'fixture'})\n"
        "for part in params['parts']:\n"
        "    target = hologram.part_path(home / 'parts', part)\n"
        "    target.parent.mkdir(parents=True, exist_ok=True)\n"
        "    target.write_bytes(b'layer')\n"
        "ok = not (home / 'fail-apply').exists()\n"
        "gate = hologram.gate(chain, tile, 'lab', ok, {'why': 'fixture'})\n"
        "print(json.dumps({'ok': ok, 'tile': tile['seq'], 'gate': gate['seq']}))\n"
        "sys.exit(0 if ok else 1)\n")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_shared_agent_and_extra_reference_counts(world):
    w = world
    w.run("A")
    w.run("B")
    ledger = w.ledger()["installed"]
    assert ledger["A"]["agents"] == ledger["B"]["agents"]
    assert ledger["A"]["extras"] == ledger["B"]["extras"]
    result = w.run("--remove", "A")
    assert "still used by B" in result.stdout
    assert (w.agents / "x_agent.py").read_bytes() == (FIXTURES / "x_agent.py").read_bytes()
    assert (w.senses / "pulse_sense.py").exists()
    w.run("--remove", "B")
    assert not list(w.agents.iterdir())
    assert not list(w.senses.iterdir())
    removed = list((w.home / "removed").rglob("x_agent.py"))
    assert any(p.read_bytes() == (FIXTURES / "x_agent.py").read_bytes() for p in removed)
    assert w.ledger() == {"packs": [], "installed": {}}


def test_replacement_restores_original_exactly(world):
    w = world
    original = (FIXTURES / "x_agent.py").read_bytes()
    (w.agents / "x_agent.py").write_bytes(original)
    (w.pages / "view_page.py").write_bytes(b"original page\x00\r\n")
    before = hashes(w.agents), hashes(w.pages)
    w.run("C")
    entry = w.ledger()["installed"]["C"]["agents"]["x_agent.py"]
    assert Path(entry["replaced_backup"]).read_bytes() == original
    assert (w.agents / "x_agent.py").read_bytes() != original
    w.run("--remove", "C")
    assert (hashes(w.agents), hashes(w.pages)) == before


def test_round_trip_leaves_user_files_and_data_untouched(world):
    w = world
    (w.agents / "user_agent.py").write_bytes(b"user supplied bytes\n")
    before, protected = hashes(w.agents), w.protected()
    w.run("D")
    w.run("--remove", "D")
    assert hashes(w.agents) == before
    assert w.protected() == protected


@pytest.mark.parametrize("legacy", [False, True])
def test_unknown_or_ledgerless_refuses_one_sentence_without_changes(world, legacy):
    w = world
    if legacy:
        (w.home / "packs.json").write_text('{"packs": ["legacy"]}\n')
    before = hashes(w.root)
    result = w.run("--remove", "legacy", ok=False)
    assert result.stdout == "Cannot remove legacy: no install ledger is available; nothing changed.\n"
    assert not result.stderr
    assert hashes(w.root) == before


@pytest.mark.parametrize("flag", ["--plan-json", "--dry-run"])
def test_remove_preview_is_read_only(world, flag):
    w = world
    w.run("A")
    before = hashes(w.root)
    result = w.run(flag, "--remove", "A")
    if flag == "--plan-json":
        plan = json.loads(result.stdout)
        assert plan["remove"] == ["A"]
        assert {Path(f["file"]).name for f in plan["files"]} == {"x_agent.py", "pulse_sense.py"}
    else:
        assert "Would remove: A" in result.stdout
    assert hashes(w.root) == before


def test_failed_layer_gate_rolls_back_chain_files_and_ledger(world):
    w = world
    w.run("--no-restart", "Layer")
    (w.home / "fail-apply").touch()
    before = hashes(w.root)
    result = w.run("--remove", "Layer", ok=False)
    assert "Nothing was removed" in result.stdout
    after = hashes(w.root)
    assert {p: v for p, v in after.items() if not p.startswith("layers/removed/")} == before
    assert (w.agents / "x_agent.py").exists()
    assert w.ledger()["packs"] == ["Layer"]


def test_layer_refcounts_preserve_user_shape(world):
    w = world
    user = {**w.part, "id": "@user/own", "point": "storage", "file": "user_layer.py"}
    chain = hologram.genesis(w.home / "chain.jsonl", w.kernel)
    tile = hologram.tile(chain, {"action": "set", "parts": [user]}, {"kind": "process", "name": "user"})
    hologram.gate(chain, tile, "lab", True, {})
    w.run("--no-restart", "Layer")
    w.run("--no-restart", "LayerB")
    w.run("--remove", "Layer")
    shape, _ = hologram.Chain(w.home / "chain.jsonl").authorised()
    assert {p["id"] for p in shape["parts"]} == {w.part["id"], user["id"]}
    w.run("--remove", "LayerB")
    shape, _ = hologram.Chain(w.home / "chain.jsonl").authorised()
    assert shape["parts"] == [user]
    assert not hologram.part_path(w.home / "parts", w.part).exists()


def test_includes_do_not_remove_named_packages(world):
    w = world
    w.run("A", "B")
    w.run("Starter")
    w.run("--remove", "Starter")
    assert set(w.ledger()["packs"]) == {"A", "B"}
    assert (w.agents / "x_agent.py").exists()
    w.run("--remove", "A", "B")
    assert not (w.agents / "x_agent.py").exists()


def test_changed_agent_refuses_entire_removal(world):
    w = world
    w.run("A")
    (w.agents / "x_agent.py").write_bytes(b"user edit\n")
    before = hashes(w.root)
    result = w.run("--remove", "A", ok=False)
    assert "has changed" in result.stdout
    assert hashes(w.root) == before


def test_failed_file_move_rolls_back_prior_moves(world, monkeypatch):
    w = world
    w.run("A")
    before = hashes(w.agents), hashes(w.senses), (w.home / "packs.json").read_bytes()
    move = packs.Changes.move

    def fail_second(self, source, target):
        if source == w.senses / "pulse_sense.py":
            raise OSError("fixture disk failure")
        return move(self, source, target)

    monkeypatch.setattr(packs.Changes, "move", fail_second)
    w.args.remove = ["A"]
    assert packs.remove(w.args, w.kernel, []) == 1
    assert (hashes(w.agents), hashes(w.senses), (w.home / "packs.json").read_bytes()) == before


def test_failed_import_restores_overwritten_file_and_ledger(world):
    w = world
    w.run("A")
    w.fail_import = w.imports + 1
    before = hashes(w.agents), hashes(w.senses), hashes(w.pages), (w.home / "packs.json").read_bytes()
    w.run("C", ok=False)
    assert (hashes(w.agents), hashes(w.senses), hashes(w.pages), (w.home / "packs.json").read_bytes()) == before


def test_identical_preexisting_user_file_is_preserved(world):
    w = world
    (w.agents / "x_agent.py").write_bytes((FIXTURES / "x_agent.py").read_bytes())
    before = hashes(w.agents)
    w.run("A")
    w.run("B")
    w.run("--remove", "A", "B")
    assert hashes(w.agents) == before


def test_reinstall_keeps_original_backup(world):
    w = world
    (w.agents / "x_agent.py").write_bytes((FIXTURES / "x_agent.py").read_bytes())
    before = hashes(w.agents)
    w.run("C")
    backup = w.ledger()["installed"]["C"]["agents"]["x_agent.py"]["replaced_backup"]
    w.run("C")
    assert w.ledger()["installed"]["C"]["agents"]["x_agent.py"]["replaced_backup"] == backup
    w.run("--remove", "C")
    assert hashes(w.agents) == before


def test_catalog_agent_has_ledger_and_can_be_removed(world):
    w = world
    w.run("@fixture/x_agent")
    assert w.ledger()["installed"]["@fixture/x_agent"]["agents"]["x_agent.py"]
    w.run("--remove", "@fixture/x_agent")
    assert not list(w.agents.iterdir())


def test_handover_uses_sighup_and_accepts_fresh_boot(world, monkeypatch):
    w = world
    (w.home / "supervisor.pid").write_text("12345")
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        if sig == signal.SIGHUP:
            (w.home / "last-boot.json").write_text('{"fallback": false, "notices": ["new shape"]}')

    monkeypatch.setattr(packs.os, "kill", kill)
    w.args.no_restart = False
    packs.handover(w.args)
    assert calls == [(12345, 0), (12345, signal.SIGHUP)]


def test_file_rar_url_does_not_add_cache_query(world):
    assert packs.get((FIXTURES / "x_agent.py").as_uri(), binary=True) == (FIXTURES / "x_agent.py").read_bytes()


@pytest.mark.parametrize("order", [("A", "C"), ("C", "A"), ("both",)])
def test_upgrade_ownership_does_not_resurrect_removed_versions(world, order):
    w = world
    w.run("A")
    w.run("C")
    if order == ("both",):
        w.run("--remove", "A", "C")
    else:
        w.run("--remove", order[0])
        expected = "replacement_agent.py" if order[0] == "A" else "x_agent.py"
        assert (w.agents / "x_agent.py").read_bytes() == (FIXTURES / expected).read_bytes()
        w.run("--remove", order[1])
    assert not list(w.agents.iterdir())


def test_upgrade_shared_original_survives_first_owner_removal(world):
    w = world
    (w.agents / "x_agent.py").write_bytes((FIXTURES / "x_agent.py").read_bytes() + b"# user copy\n")
    before = hashes(w.agents)
    w.run("A")
    w.run("C")
    w.run("--remove", "A")
    w.run("--remove", "C")
    assert hashes(w.agents) == before


def test_user_layer_already_present_is_never_claimed(world):
    w = world
    chain = hologram.genesis(w.home / "chain.jsonl", w.kernel)
    tile = hologram.tile(chain, {"action": "set", "parts": [w.part]}, {"kind": "process", "name": "user"})
    hologram.gate(chain, tile, "lab", True, {})
    w.run("Layer")
    assert w.ledger()["installed"]["Layer"]["layers"] == []
    before = (w.home / "chain.jsonl").read_bytes()
    w.run("--remove", "Layer")
    assert (w.home / "chain.jsonl").read_bytes() == before


def test_mixed_unknown_request_does_not_remove_known_pack(world):
    w = world
    w.run("A")
    before = hashes(w.root)
    w.run("--remove", "A", "unknown", ok=False)
    assert hashes(w.root) == before


def test_agent_and_extra_removal_never_calls_handover(world):
    w = world
    (w.home / "supervisor.pid").write_text("deliberately not a pid")
    w.run("A")
    w.run("--remove", "A")
    assert not list(w.agents.iterdir())
    assert not list(w.senses.iterdir())


def test_failed_handover_restores_files_ledger_and_shape(world, monkeypatch):
    w = world
    w.run("--no-restart", "Layer")
    before = hashes(w.agents), hashes(w.home / "parts"), (w.home / "packs.json").read_bytes()
    chain_before = (w.home / "chain.jsonl").read_bytes()
    (w.home / "last-boot.json").write_text('{"notices": ["original"]}\n')
    boot_before = (w.home / "last-boot.json").read_bytes()
    calls = []

    def switch(_):
        calls.append(True)
        (w.home / "last-boot.json").write_text('{"fallback": true}')
        if len(calls) == 1:
            raise ValueError("fixture failed handover")

    monkeypatch.setattr(packs, "handover", switch)
    w.args.no_restart = False
    w.args.remove = ["Layer"]
    shape, _ = hologram.Chain(w.home / "chain.jsonl").authorised()
    assert packs.remove(w.args, w.kernel, shape["parts"]) == 1
    assert len(calls) == 2
    assert (hashes(w.agents), hashes(w.home / "parts"), (w.home / "packs.json").read_bytes()) == before
    assert (w.home / "chain.jsonl").read_bytes() == chain_before
    assert (w.home / "last-boot.json").read_bytes() == boot_before


def test_install_plan_stays_canonical_and_read_only(world):
    w = world
    before = hashes(w.root)
    plans = [json.loads(w.run("--plan-json", *order).stdout) for order in (
        ("A", "B"), ("B", "A", "B"), ("B", "A"))]
    assert plans[0] == plans[1] == plans[2] == {
        "packs": ["A", "B"], "refused": [], "warnings": [], "layers": [],
        "agents": [["x_agent.py", sha((FIXTURES / "x_agent.py").read_bytes())[:12], "A"]],
        "extras": [["pulse_sense.py", sha((FIXTURES / "pulse_sense.py").read_bytes())[:12], "A"]]}
    assert hashes(w.root) == before


def test_reinstall_with_skip_does_not_forget_previous_ownership(world):
    w = world
    w.run("A")
    w.run("--skip", "x_agent.py", "A")
    assert "x_agent.py" in w.ledger()["installed"]["A"]["agents"]
    w.run("--remove", "A")
    assert not list(w.agents.iterdir())


def test_failed_ledger_write_restores_consumed_backup(world, monkeypatch):
    w = world
    (w.agents / "x_agent.py").write_bytes((FIXTURES / "x_agent.py").read_bytes())
    w.run("C")
    before = hashes(w.agents), hashes(w.pages), (w.home / "packs.json").read_bytes()
    backup = Path(w.ledger()["installed"]["C"]["agents"]["x_agent.py"]["replaced_backup"])
    backup_before = backup.read_bytes()
    write = packs.Changes.write

    def fail_record(self, path, body):
        if path == w.home / "packs.json":
            raise OSError("fixture ledger write failure")
        return write(self, path, body)

    monkeypatch.setattr(packs.Changes, "write", fail_record)
    w.args.remove = ["C"]
    assert packs.remove(w.args, w.kernel, []) == 1
    assert (hashes(w.agents), hashes(w.pages), (w.home / "packs.json").read_bytes()) == before
    assert backup.read_bytes() == backup_before


def test_unknown_pack_refuses_even_if_shape_chain_is_unreadable(world):
    w = world
    (w.home / "chain.jsonl").write_text("unreadable chain\n")
    before = hashes(w.root)
    result = w.run("--remove", "unknown", ok=False)
    assert result.stdout == "Cannot remove unknown: no install ledger is available; nothing changed.\n"
    assert not result.stderr
    assert hashes(w.root) == before


@pytest.mark.parametrize("with_senses", [False, True])
def test_removing_money_watch_autoremoves_private_mode(world, with_senses):
    w = world
    before, protected = hashes(w.agents), w.protected()
    names = ["money-watch", "senses"] if with_senses else ["money-watch"]
    w.run(*names)
    entries = w.ledger()["installed"]
    assert entries["money-watch"]["mark"] == "manual"
    assert entries["private-mode"]["mark"] == "auto"
    assert entries["private-mode"]["required_by"] == ["money-watch"]
    result = w.run("--remove", *names)
    assert "private-mode: removed (only money-watch needed it)" in result.stdout
    assert hashes(w.agents) == before
    assert w.protected() == protected
    assert w.ledger() == {"packs": [], "installed": {}}


def test_manual_private_mode_is_not_autoremoved(world):
    w = world
    w.run("private-mode")
    w.run("money-watch")
    result = w.run("--remove", "money-watch")
    assert "private-mode: removed" not in result.stdout
    assert w.ledger()["packs"] == ["private-mode"]
    assert w.ledger()["installed"]["private-mode"]["mark"] == "manual"
    assert w.ledger()["installed"]["private-mode"]["required_by"] == []
    assert (w.agents / "private_mode_agent.py").exists()


@pytest.mark.parametrize("separate_installs", [False, True])
def test_shared_auto_include_stays_for_remaining_parent(world, separate_installs):
    w = world
    if separate_installs:
        w.run("money-watch")
        w.run("starter-personal")
    else:
        w.run("money-watch", "starter-personal")
    assert w.ledger()["installed"]["private-mode"]["required_by"] == ["money-watch", "starter-personal"]
    result = w.run("--remove", "money-watch")
    assert "private-mode: still needed by starter-personal" in result.stdout
    assert w.ledger()["packs"] == ["private-mode", "starter-personal"]
    assert w.ledger()["installed"]["private-mode"]["required_by"] == ["starter-personal"]
    assert (w.agents / "private_mode_agent.py").exists()
    w.run("--remove", "starter-personal")
    assert not list(w.agents.iterdir())
    assert w.ledger()["installed"] == {}


def test_explicit_install_promotes_auto_pack_to_manual(world):
    w = world
    w.run("money-watch")
    w.run("private-mode")
    assert w.ledger()["installed"]["private-mode"]["mark"] == "manual"
    w.run("--remove", "money-watch")
    assert w.ledger()["packs"] == ["private-mode"]
    assert (w.agents / "private_mode_agent.py").exists()


def test_autoremove_repeats_until_nested_includes_are_orphaned(world):
    w = world
    w.run("Nested")
    assert w.ledger()["installed"]["money-watch"]["required_by"] == ["Nested"]
    result = w.run("--remove", "Nested")
    assert "money-watch: removed (only Nested needed it)" in result.stdout
    assert "private-mode: removed (only money-watch needed it)" in result.stdout
    assert w.ledger() == {"packs": [], "installed": {}}
    assert not list(w.agents.iterdir())


@pytest.mark.parametrize("flag", ["--plan-json", "--dry-run"])
def test_autoremove_previews_are_read_only(world, flag):
    w = world
    w.run("money-watch")
    before = hashes(w.root)
    result = w.run(flag, "--remove", "money-watch")
    if flag == "--plan-json":
        plan = json.loads(result.stdout)
        assert plan["remove"] == ["money-watch", "private-mode"]
        assert plan["auto_removed"] == {"private-mode": ["money-watch"]}
    else:
        assert "Would remove: money-watch, private-mode" in result.stdout
    assert hashes(w.root) == before


def test_autoremoval_failure_restores_all_files_and_marks(world, monkeypatch):
    w = world
    w.run("money-watch")
    before = hashes(w.agents), (w.home / "packs.json").read_bytes()
    move = packs.Changes.move

    def fail_private_mode(self, source, target):
        if source == w.agents / "private_mode_agent.py":
            raise OSError("fixture auto dependency removal failure")
        return move(self, source, target)

    monkeypatch.setattr(packs.Changes, "move", fail_private_mode)
    w.args.remove = ["money-watch"]
    assert packs.remove(w.args, w.kernel, []) == 1
    assert (hashes(w.agents), (w.home / "packs.json").read_bytes()) == before


def test_unmarked_legacy_entry_is_not_autoremoved(world):
    w = world
    w.run("private-mode")
    have = w.ledger()
    for key in ("mark", "required_by", "includes"):
        have["installed"]["private-mode"].pop(key)
    (w.home / "packs.json").write_text(json.dumps(have))
    w.run("money-watch")
    assert w.ledger()["installed"]["private-mode"]["mark"] == "manual"
    w.run("--remove", "money-watch")
    assert w.ledger()["packs"] == ["private-mode"]
    assert (w.agents / "private_mode_agent.py").exists()


def test_ledgerless_pack_is_not_reclassified_as_auto(world):
    w = world
    (w.home / "packs.json").write_text('{"packs": ["private-mode"]}')
    w.run("money-watch")
    assert w.ledger()["installed"]["private-mode"]["mark"] == "manual"
    w.run("--remove", "money-watch")
    assert w.ledger()["packs"] == ["private-mode"]


def test_auto_layer_gate_failure_preserves_dependencies_and_marks(world):
    w = world
    w.manifests["money-watch"]["install"]["includes"].append("Layer")
    w.run("--no-restart", "money-watch")
    assert w.ledger()["installed"]["Layer"]["mark"] == "auto"
    (w.home / "fail-apply").touch()
    before = hashes(w.agents), (w.home / "packs.json").read_bytes(), (w.home / "chain.jsonl").read_bytes()
    w.run("--remove", "money-watch", ok=False)
    assert (hashes(w.agents), (w.home / "packs.json").read_bytes(),
            (w.home / "chain.jsonl").read_bytes()) == before
