#!/usr/bin/env bash
# Add RAR packs to your ONE Brainstem, all or nothing:
#   curl -fsSL https://kody-w.github.io/grail-layers/add.sh | bash -s -- starter-personal
#   curl -fsSL https://kody-w.github.io/grail-layers/add.sh | bash -s -- night-watch private-mode
# Installs grail-layers first if this machine does not have it yet. Layers are tried in a test boot before your
# Brainstem uses them; agents are added only after that passes. Undo layers: say "undo" to your Brainstem.
set -euo pipefail
SELF="${GRAIL_LAYERS_URL:-https://kody-w.github.io/grail-layers}"
BS="${BRAINSTEM_HOME:-$HOME/.brainstem}"; DEST="$BS/layers"
[ "$#" -gt 0 ] || { echo "Name at least one pack, e.g. starter-personal. See https://kody-w.github.io/RAR/distros.html"; exit 2; }
if [ ! -f "$DEST/grail_layers.py" ]; then
  echo "First time: installing grail + layers (your Brainstem, unchanged, with a safety net)"
  curl -fsSL "$SELF/install.sh" | bash
fi
curl -fsSL "$SELF/packs.py" -o "$DEST/packs.py.new" && mv "$DEST/packs.py.new" "$DEST/packs.py"
PY="$BS/venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" "$DEST/packs.py" --kernel "$BS/src/rapp_brainstem/brainstem.py" --python "$PY" "$@"
