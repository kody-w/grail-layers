"""BrainstemShape: the owner changes what their Brainstem is made of, through chat, with no work.

Drop this file into the Brainstem's agents/ (userland route 1: an agent, never a kernel change). It drives the
grail-layers supervisor (~/.brainstem/layers/grail_layers.py), which keeps the device's rapp/1 chain and gates
every change in a lab before anything goes live. Plain words in, plain words out.

  "what am I running"            -> status  (the shape is the SBOM)
  "update my brainstem with X"   -> add     (a published part: id @publisher/name, sha256, locator)
  "remove X" / "safe mode"       -> remove / safe_mode
  "undo" / "put it back"         -> undo    (Time Machine: previous authorised shape, gated again)
  "what changed since Tuesday"   -> since   (receipts: who asked, what passed)
  "check the history"            -> replay
  "who gets this if I'm gone"    -> who_inherits
  "if something happens to me, Jane gets this" -> name_successor (mints Jane's key pair; the owner hands her the
                                    private file out of band; only public keys ever enter the chain)
  "hand it over to Jane now"     -> handoff
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from agents.basic_agent import BasicAgent

LAYERS = Path(os.environ.get("GRAIL_LAYERS_HOME") or Path.home() / ".brainstem" / "layers")
KERNEL = Path(os.environ.get("GRAIL_KERNEL") or Path.home() / ".brainstem" / "src" / "rapp_brainstem" / "brainstem.py")


class BrainstemShapeAgent(BasicAgent):
    def __init__(self):
        self.name = "BrainstemShape"
        self.metadata = {
            "name": self.name,
            "description": (
                "Change or inspect what this Brainstem is made of: what am I running (status), update my "
                "brainstem with a published part (add), remove a part, safe mode, undo (put it back the way it "
                "was), what changed since a time, or check the history (replay). Every change is tried in a lab "
                "first and recorded; nothing goes live until it passes. Also succession: who owns this Brainstem and "
                "who inherits it (who_inherits), name who gets it if the owner is gone (name_successor, with the "
                "person's name; a key pair is minted for them), or hand it over now (handoff)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["status", "add", "remove", "safe_mode", "undo", "since", "replay",
                                        "who_inherits", "name_successor", "handoff"]},
                    "part": {"type": "object",
                             "description": "for add: {id '@publisher/name', kind 'layer'|'kernel', point, file, sha256, locator https url}"},
                    "id": {"type": "string", "description": "for remove: the part id"},
                    "on": {"type": "boolean", "description": "for safe_mode"},
                    "since": {"type": "string", "description": "for since: UTC like 2026-11-03T00:00:00.000Z"},
                    "by": {"type": "string", "description": "who asked (the owner's name), recorded on the tile"},
                    "successor": {"type": "string", "description": "for name_successor: the person's name"},
                    "silence_days": {"type": "integer", "description": "for name_successor: days of silence before the hand-off opens (default 30)"},
                    "confirm_days": {"type": "integer", "description": "for name_successor: extra days to confirm (default 7)"},
                },
                "required": ["action"],
            },
        }
        super().__init__(self.name, self.metadata)

    def system_context(self):
        """Say a boot notice ONCE, in the owner's next chat, then mark it told. Journald lesson: a boot message
        nobody reads is not a message; the owner's chat is the one place they already look."""
        f = LAYERS / "last-boot.json"
        try:
            lb = json.loads(f.read_text())
        except (OSError, ValueError):
            return None
        if lb.get("told") or not lb.get("notices"):
            return None
        try:
            lb["told"] = True
            f.write_text(json.dumps(lb, indent=1))
        except OSError:
            pass
        return ("<brainstem_boot_notice>Before answering, tell the owner this in one plain sentence (once; it has not "
                "been said yet), then answer their message: " + " ".join(lb["notices"]) + "</brainstem_boot_notice>")

    def _run(self, *args, timeout=240):
        sup = Path(os.environ.get("GRAIL_LAYERS_SUPERVISOR") or LAYERS / "grail_layers.py")
        if not sup.is_file():
            return None, "The layer supervisor is not installed on this device (curl -fsSL https://kody-w.github.io/grail-layers/install.sh | bash)."
        env = dict(os.environ, GRAIL_LAYERS_HOME=str(LAYERS))
        r = subprocess.run([sys.executable, str(sup), "--kernel", str(KERNEL), *args],
                           capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout or r.stderr).strip()

    def perform(self, action="status", part=None, id=None, on=None, since=None, by=None,
                successor=None, silence_days=None, confirm_days=None, **_):
        if action == "who_inherits":
            code, out = self._run("--who")
            return out
        if action == "name_successor":
            if not successor:
                return "Tell me the person's name, e.g. 'if something happens to me, Jane gets this'."
            code, out = self._run("--mint-key", successor)
            if code:
                return out
            try:
                rec = json.loads(out.splitlines()[-1])
            except (ValueError, IndexError):
                return out
            args = ["--name-successor", successor, "--successor-pub", rec["public_record"]]
            if silence_days:
                args += ["--silence-days", str(silence_days)]
            if confirm_days is not None:
                args += ["--confirm-days", str(confirm_days)]
            code, out = self._run(*args)
            try:
                d = json.loads(out.splitlines()[-1])
            except (ValueError, IndexError):
                return out
            if not d.get("ok"):
                return f"Not recorded. {d.get('refused')}"
            return (f"Recorded in frame #{d['frame']}: {d['says']} {successor}'s private key is at {rec['private_key']}; "
                    f"give that file to {successor} out of band and keep your own owner key safe. Only public keys are in the chain.")
        if action == "handoff":
            code, out = self._run("--handoff")
            try:
                d = json.loads(out.splitlines()[-1])
            except (ValueError, IndexError):
                return out
            return (f"Handed over in frame #{d['frame']}: {d['says']}" if d.get("ok") else f"Not handed over. {d.get('refused')}")
        if action == "status":
            code, out = self._run("--status")
            return out
        if action == "since":
            code, out = self._run("--since", since or "1970-01-01T00:00:00.000Z")
            return out or "Nothing has changed since then."
        if action == "replay":
            code, out = self._run("--replay")
            return out
        if action == "undo":
            args = ["--undo"]
        elif action == "add":
            if not isinstance(part, dict):
                return "To add a part I need its published record: id (@publisher/name), kind, point, file, sha256 and locator."
            args = ["--apply", json.dumps({"action": "add", "part": part})]
        elif action == "remove":
            args = ["--apply", json.dumps({"action": "remove", "id": id})]
        elif action == "safe_mode":
            args = ["--apply", json.dumps({"action": "safe_mode", "on": bool(on)})]
        else:
            return "I can do: status, add, remove, safe_mode, undo, since, replay."
        if by:
            args += ["--by", by]
        code, out = self._run(*args)
        if code is None:
            return out
        try:
            d = json.loads(out.splitlines()[-1])
        except (ValueError, IndexError):
            return out
        if d.get("ok"):
            return (f"Tried it in the lab and it passed ({d.get('why')}). Recorded as tile #{d.get('tile')}, "
                    f"gate #{d.get('gate')}. It will run from the next restart of the Brainstem; say 'undo' to put it back.")
        return f"Not applied. {d.get('refused') or d.get('why')} Your live Brainstem is untouched."
