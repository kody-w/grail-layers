#!/usr/bin/env python3
"""One build step (rapp-static-api/1.1): registry.json, api/v1/status.json, llms.txt. Deterministic + stable-write."""
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
R = Path(__file__).resolve().parent
BASE, RAW = "https://kody-w.github.io/grail-layers", "https://raw.githubusercontent.com/kody-w/grail-layers/main"
SERVED = ["install.sh", "uninstall.sh", "grail_layers.py", "hologram.py", "shape_lens.py", "rapp.py", "brainstem_shape_agent.py", "README.md", "llms.txt"]
llms = f"""# grail-layers

> Every grail (Brainstem kernel) update ships as a layer over the unchanged grail; if a layer fails, plain grail runs.

- [Install (grail, then layers)]({BASE}/install.sh): `curl -fsSL {BASE}/install.sh | bash`
- [Uninstall (plain grail)]({BASE}/uninstall.sh)
- [Supervisor]({BASE}/grail_layers.py)
- [The pattern and the layer contract]({BASE}/README.md)
- [Layer files in RAR](https://kody-w.github.io/RAR/stacks/grail-layers/pack.json)
- [Registry]({BASE}/registry.json)
- [Hologram core]({BASE}/hologram.py) · [the one lens]({BASE}/shape_lens.py) · [rapp/1]({BASE}/rapp.py) · [chat agent]({BASE}/brainstem_shape_agent.py)

## A Brainstem is a living hologram (canon: https://kody-w.github.io/living-holograms/paper.html over rapp/1)

WHAT IS RUNNING   Every device keeps an append-only chain of rapp/1 frames at ~/.brainstem/layers/chain.jsonl.
  Frame #0 (shape.genesis) is the sha256 of the unchanged grail (brainstem.py). The object carried is the SHAPE:
  {{"schema":"brainstem-shape/1","grail":{{sha256,bytes}},"safe_mode":bool,"parts":[...]}}
  A part is exactly {{id "@publisher/name", kind "layer"|"kernel", point, file, sha256, locator}}. One occupant per
  point (provider auth storage agents transport kernel). The shape IS the SBOM: "what am I running" = read it.
HOW IT CHANGES    Only through the one pure lens (shape_lens.perform): add | remove | safe_mode | set.
  A pass appends a shape.tile {{object, derived_from, lens{{name,sha256}}, params, params_hash, slush_in, caller, lease}}.
  A tile does NOTHING until a shape.gate {{tile, gate:"lab"|"live", passed, evidence}} passes. The supervisor boots
  whatever the newest PASSING gate authorises; if nothing passes, plain grail. Two live failures in a row = the
  previous passing shape, then plain grail. The user always ends up with a Brainstem.
THE AGENT PATH    python3 grail_layers.py --kernel ~/.brainstem/src/rapp_brainstem/brainstem.py ...
  --status                      plain words: grail, parts, head, who authorised, any part whose bytes moved
  --apply '{{"action":"add","part":{{...}}}}' [--by name]   lens -> tile -> boot it in the LAB (spare port) -> real
                                /chat probe -> gate frame. Prints one JSON line {{ok, tile, gate, why, next}}.
                                Never touches the live Brainstem; a restart takes the authorised shape.
  --undo                        tile back to the previously authorised shape, gated the same way (Time Machine:
                                any earlier passing tile can be re-applied with "set")
  --since 2026-11-03T00:00:00.000Z   every frame since: what changed, who asked, what passed
  --replay                      re-run every tile through the recorded lens; the head must come back identical
  --plain                       safe mode (grail, no parts) for this boot
  In chat: drop brainstem_shape_agent.py into agents/ and say "what am I running", "update my brainstem with …",
  "undo", "what changed since Tuesday".
KINDS             layer = a Python file applied in memory over the unchanged grail (declares PROVEN_GRAIL shas and
  TOUCHES hook points; unproven or clashing layers are skipped and said so). kernel = a whole Brainstem that
  replaces brainstem.py (e.g. the LisPy kernel, examples/brainstem-kernel in kody-w/lisppy); it gets the user's
  agents/ and soul.md (GODD data follows every shape) and must pass the same oracle. Layer parts wrap brainstem.py,
  so when a kernel part is present they do not apply (the supervisor says so in the log).
RULES             parts are pinned by sha256 and byte-identical on every device; device-specific facts are GODD
  data, never in a part. Order is chain position, never the clock. Chains are private to the device. Nothing is
  deleted: history is the product (receipts: every frame says who asked and what passed). No pip, no settings.
WHO PAID          Linux livepatch, Silverblue/ostree, ChromeOS A/B, Kubernetes reconcile, Git, Dynamics 365
  solution layering (publisher-scoped ids, no unmanaged-in-prod, no user data in a layer), Knight Capital (no flags).
"""
(R / "llms.txt").write_text(llms)
entries = [{"name": n, "sha256": hashlib.sha256((R / n).read_bytes()).hexdigest(), "url": f"{BASE}/{n}", "raw": f"{RAW}/{n}"} for n in SERVED]
for e in entries: e["sha8"] = e["sha256"][:12]
reg = {"schema": "grail-layers-registry/1.0", "spec": "rapp-static-api/1.1", "generated": None, "pages": BASE + "/", "raw": RAW + "/",
       "summary": {"files": len(entries), "layers_source": "https://kody-w.github.io/RAR/stacks/grail-layers/pack.json"}, "entries": entries}
old = R / "registry.json"; prev = json.loads(old.read_text()) if old.is_file() else {}
reg["generated"] = prev.get("generated") if prev and {**prev, "generated": None} == reg else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
old.write_text(json.dumps(reg, indent=2) + "\n")
(R / "api/v1").mkdir(parents=True, exist_ok=True)
(R / "api/v1/status.json").write_text(json.dumps({"schema": "grail-layers-status/1.0", "generated": reg["generated"], "ok": True, **reg["summary"]}, indent=2) + "\n")
print("built")
