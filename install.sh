#!/usr/bin/env bash
# grail-layers one-liner:  curl -fsSL https://kody-w.github.io/grail-layers/install.sh | bash
#
# 1. Runs the grail's OWN one-liner, unchanged (the base every user keeps).
# 2. Installs the layer supervisor and the layers published in RAR (stacks/grail-layers),
#    each checked against the sha256 in RAR's pack.json.
# 3. Points the Brainstem's launchd job at the supervisor (backup kept). The supervisor starts
#    grail + layers, probes it, and falls back to plain grail if anything fails.
# Undo everything: curl -fsSL https://kody-w.github.io/grail-layers/uninstall.sh | bash
set -euo pipefail
GRAIL_ONE_LINER="${GRAIL_ONE_LINER:-https://kody-w.github.io/rapp-installer/install.sh}"
SELF="${GRAIL_LAYERS_URL:-https://kody-w.github.io/grail-layers}"
RAR="${GRAIL_LAYERS_RAR:-https://kody-w.github.io/RAR/stacks/grail-layers}"
BS="${BRAINSTEM_HOME:-$HOME/.brainstem}"
DEST="$BS/layers"
say(){ printf '\033[1m%s\033[0m\n' "$*"; }

if [ "${SKIP_GRAIL:-0}" != "1" ]; then
  say "Step 1 of 3: installing the grail (the base), with its own one-liner"
  curl -fsSL "$GRAIL_ONE_LINER" | bash
fi
KERNEL="$BS/src/rapp_brainstem/brainstem.py"
[ -f "$KERNEL" ] || { echo "The grail is not installed at $KERNEL; nothing to layer over."; exit 1; }
PY="$BS/venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"

say "Step 2 of 3: installing layers from RAR"
mkdir -p "$DEST/parts"
for f in grail_layers.py hologram.py shape_lens.py rapp.py brainstem_shape_agent.py chaos.py; do   # supervisor + hologram (rapp/1 chain, the one lens) + chat agent
  curl -fsSL "$SELF/$f" -o "$DEST/$f.new" && mv "$DEST/$f.new" "$DEST/$f"
done
# The chat agent goes where every agent goes (userland route 1: agents/ is the user's folder, never the kernel).
AGENTS_DIR="${AGENTS_PATH:-$(dirname "$KERNEL")/agents}"
[ -d "$AGENTS_DIR" ] && cp "$DEST/brainstem_shape_agent.py" "$AGENTS_DIR/brainstem_shape_agent.py" && echo "chat agent installed: say 'what am I running' to your Brainstem"
curl -fsSL "$RAR/pack.json" -o "$DEST/pack.json"
"$PY" - "$DEST" "$RAR" <<'PY'
import hashlib, json, sys, urllib.request, pathlib
dest, rar = pathlib.Path(sys.argv[1]), sys.argv[2]
pack = json.loads((dest / "pack.json").read_text())
inst = pack["install"]
KEYS = ("id", "kind", "point", "file", "sha256", "locator")  # a part is exactly these (brainstem-shape/1)
parts = inst.get("parts")
if not parts:  # pack < 1.2: the legacy layers list, lifted into part records (alias path; same bytes, same pins)
    parts = [{"id": f"@kody-w/{p[:-3]}", "kind": "layer", "point": "provider", "file": p,
              "sha256": pack["files"][p]["sha256"], "locator": f"{rar}/{p}"}
             for p in inst["layers"] if p[:-3] in inst.get("default_enabled", [p[:-3] for p in inst["layers"]])]
seed = inst.get("seed")  # pack >= 1.2.1: which parts the installer seeds; others are published for "update my brainstem with …"
if seed is not None:
    parts = [p for p in parts if p["id"] in seed]
seeded = []
for part in parts:
    part = {k: part[k] for k in KEYS}
    pub, name = part["id"][1:].split("/", 1)
    b = urllib.request.urlopen(part["locator"], timeout=60).read()
    if hashlib.sha256(b).hexdigest() != part["sha256"]:
        sys.exit(f"sha256 mismatch for {part['id']}; refusing to install it")
    target = dest / "parts" / pub / name / part["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b)
    seeded.append(part)
(dest / "parts.json").write_text(json.dumps(seeded, indent=1) + "\n")
print("parts installed:", ", ".join(p["id"] for p in seeded) or "none",
      "| the device's chain is seeded from them at first boot; after that, change the shape through chat")
PY

say "Step 3 of 3: starting the Brainstem as grail + layers"
PLIST="$HOME/Library/LaunchAgents/com.brainstem.server.plist"
if [ "$(uname)" = "Darwin" ] && [ -f "$PLIST" ] && [ "${GRAIL_LAYERS_NO_LAUNCHD:-0}" != "1" ]; then
  # Keep the grail's own job as the restore point (refreshed whenever the grail installer rewrote it).
  if ! /usr/libexec/PlistBuddy -c "Print :ProgramArguments:1" "$PLIST" 2>/dev/null | grep -q grail_layers.py; then
    cp "$PLIST" "$DEST/com.brainstem.server.plist.grail"
  fi
  /usr/libexec/PlistBuddy -c "Delete :ProgramArguments" "$PLIST"
  /usr/libexec/PlistBuddy -c "Add :ProgramArguments array" \
    -c "Add :ProgramArguments:0 string $PY" -c "Add :ProgramArguments:1 string $DEST/grail_layers.py" \
    -c "Add :ProgramArguments:2 string --kernel" -c "Add :ProgramArguments:3 string $KERNEL" \
    -c "Add :ProgramArguments:4 string --python" -c "Add :ProgramArguments:5 string $PY" "$PLIST"
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  # Nightly chaos drill (Chaos Monkey lesson: bounded, scheduled, scratch copy only, never the live chain).
  CHAOS_PLIST="$HOME/Library/LaunchAgents/com.brainstem.chaos.plist"
  mkdir -p "$DEST/chaos"
  cat > "$CHAOS_PLIST" <<EOP
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.brainstem.chaos</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$DEST/chaos.py</string><string>--kernel</string><string>$KERNEL</string><string>--python</string><string>$PY</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>$DEST/chaos/launchd.log</string>
  <key>StandardErrorPath</key><string>$DEST/chaos/launchd.log</string>
</dict></plist>
EOP
  launchctl bootout "gui/$(id -u)" "$CHAOS_PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$CHAOS_PLIST"
  say "Done. Your Brainstem now runs grail + layers; if a layer ever fails it starts plain grail by itself. A chaos drill runs nightly at 03:30 on a scratch copy."
else
  say "Done. Start it with: $PY $DEST/grail_layers.py --kernel $KERNEL --python $PY"
fi
echo "Log of what ran: $DEST/layers.log   Undo: curl -fsSL $SELF/uninstall.sh | bash"
