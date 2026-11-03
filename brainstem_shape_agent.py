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
                "first and recorded; nothing goes live until it passes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["status", "add", "remove", "safe_mode", "undo", "since", "replay"]},
                    "part": {"type": "object",
                             "description": "for add: {id '@publisher/name', kind 'layer'|'kernel', point, file, sha256, locator https url}"},
                    "id": {"type": "string", "description": "for remove: the part id"},
                    "on": {"type": "boolean", "description": "for safe_mode"},
                    "since": {"type": "string", "description": "for since: UTC like 2026-11-03T00:00:00.000Z"},
                    "by": {"type": "string", "description": "who asked (the owner's name), recorded on the tile"},
                },
                "required": ["action"],
            },
        }
        super().__init__(self.name, self.metadata)

    def _run(self, *args, timeout=240):
        sup = Path(os.environ.get("GRAIL_LAYERS_SUPERVISOR") or LAYERS / "grail_layers.py")
        if not sup.is_file():
            return None, "The layer supervisor is not installed on this device (curl -fsSL https://kody-w.github.io/grail-layers/install.sh | bash)."
        env = dict(os.environ, GRAIL_LAYERS_HOME=str(LAYERS))
        r = subprocess.run([sys.executable, str(sup), "--kernel", str(KERNEL), *args],
                           capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout or r.stderr).strip()

    def perform(self, action="status", part=None, id=None, on=None, since=None, by=None, **_):
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
