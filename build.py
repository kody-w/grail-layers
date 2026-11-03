#!/usr/bin/env python3
"""One build step (rapp-static-api/1.1): registry.json, api/v1/status.json, llms.txt. Deterministic + stable-write."""
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
R = Path(__file__).resolve().parent
BASE, RAW = "https://kody-w.github.io/grail-layers", "https://raw.githubusercontent.com/kody-w/grail-layers/main"
SERVED = ["install.sh", "uninstall.sh", "grail_layers.py", "README.md", "llms.txt"]
llms = f"""# grail-layers

> Every grail (Brainstem kernel) update ships as a layer over the unchanged grail; if a layer fails, plain grail runs.

- [Install (grail, then layers)]({BASE}/install.sh): `curl -fsSL {BASE}/install.sh | bash`
- [Uninstall (plain grail)]({BASE}/uninstall.sh)
- [Supervisor]({BASE}/grail_layers.py)
- [The pattern and the layer contract]({BASE}/README.md)
- [Layer files in RAR](https://kody-w.github.io/RAR/stacks/grail-layers/pack.json)
- [Registry]({BASE}/registry.json)
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
