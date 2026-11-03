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
mkdir -p "$DEST/layers"
for f in grail_layers.py hologram.py shape_lens.py rapp.py brainstem_shape_agent.py; do   # supervisor + hologram (rapp/1 chain, the one lens) + chat agent
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
names = []
for path in pack["install"]["layers"]:
    b = urllib.request.urlopen(f"{rar}/{path}", timeout=60).read()
    if hashlib.sha256(b).hexdigest() != pack["files"][path]["sha256"]:
        sys.exit(f"sha256 mismatch for {path}; refusing to install it")
    (dest / "layers" / path).write_bytes(b)
    names.append(path[:-3])
want = [n for n in pack["install"].get("default_enabled", names) if n in names]
(dest / "enabled").write_text("\n".join(want) + "\n")
print("layers installed:", ", ".join(names), "| enabled:", ", ".join(want) or "none")
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
  say "Done. Your Brainstem now runs grail + layers; if a layer ever fails it starts plain grail by itself."
else
  say "Done. Start it with: $PY $DEST/grail_layers.py --kernel $KERNEL --python $PY"
fi
echo "Log of what ran: $DEST/layers.log   Undo: curl -fsSL $SELF/uninstall.sh | bash"
