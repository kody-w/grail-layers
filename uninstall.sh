#!/usr/bin/env bash
# Remove grail-layers and give back plain grail exactly as its own installer set it up.
set -euo pipefail
BS="${BRAINSTEM_HOME:-$HOME/.brainstem}"; DEST="$BS/layers"
PLIST="$HOME/Library/LaunchAgents/com.brainstem.server.plist"
if [ -f "$DEST/com.brainstem.server.plist.grail" ] && [ "${GRAIL_LAYERS_NO_LAUNCHD:-0}" != "1" ]; then
  cp "$DEST/com.brainstem.server.plist.grail" "$PLIST"
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
fi
mv "$DEST" "$BS/layers.removed-$(date +%Y%m%d-%H%M%S)"
echo "Plain grail restored. The removed layers are kept beside it in case you want them back."
