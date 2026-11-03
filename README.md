# grail-layers: how every grail update ships

**The rule (Kody, 2026-11-03): the grail stays the base. Every update is a layer over it, and if a
layer breaks, people still have grail.** This repo is separate from the grail repo
(`kody-w/rapp-installer`) and never edits it. The two work together at run time.

```bash
curl -fsSL https://kody-w.github.io/grail-layers/install.sh | bash     # grail, then layers over it
curl -fsSL https://kody-w.github.io/grail-layers/uninstall.sh | bash   # back to plain grail
```

## How it works

1. **The grail installs itself, unchanged.** The one-liner runs the grail's own one-liner first.
2. **Layers come from RAR, checked.** Layer files are published in RAR at `stacks/grail-layers/`
   (https://kody-w.github.io/RAR/stacks/grail-layers/pack.json). Every file is checked against its sha256
   before it is saved.
3. **Applied in memory, never on disk.** `grail_layers.py` starts the Brainstem by applying each layer
   inside the process, then running the untouched `brainstem.py`. The grail file is byte-identical to
   what the grail installer wrote.
4. **Three safety nets:**
   - a layer that throws while applying is skipped;
   - every hook inside a layer falls back to the grail's own call on any error;
   - the supervisor sends one real `/chat` probe, and if it fails, it restarts plain grail on the
     same port and writes why to `~/.brainstem/layers/layers.log`.
5. **One-step undo.** The installer keeps the grail's own launchd job as the restore point, and
   `uninstall.sh` puts it back exactly.

Who did this before: Linux livepatch and kpatch (patch a running kernel in memory; the image on disk
never changes), Fedora Silverblue/ostree (immutable base plus layered packages, rollback in one
step), and ChromeOS A/B updates (a bad update boots back into the known-good slot).


## The Brainstem is a living hologram (2026-11-03)

The supervisor now keeps a **rapp/1 chain per device** (`~/.brainstem/layers/chain.jsonl`,
canon: https://kody-w.github.io/living-holograms/paper.html). Frame #0 is the grail's sha256. The object carried
is the shape (`brainstem-shape/1`: the grail pin, safe mode, and an ordered list of parts pinned by sha256 with
publisher-scoped ids). The shape changes only through one pure lens (`shape_lens.py`), as a tile; **a tile takes
effect only after a passing gate frame**, and every boot appends a live gate frame. Two live failures in a row
fall back to the previous passing shape, then to plain grail.

| You say (through chat, `brainstem_shape_agent.py`) | The supervisor does |
|---|---|
| what am I running | `--status`: the shape is the SBOM, plus any part whose bytes moved |
| update my brainstem with X | `--apply`: lens → tile → boot in the lab on a spare port → real `/chat` probe → gate frame; live untouched until restart |
| undo / put it back | `--undo`: the previous authorised shape, gated the same way (Time Machine: any earlier tile can be re-applied) |
| what changed since Tuesday | `--since`: every frame, who asked, what passed (receipts) |
| check the history | `--replay`: every tile re-run through the recorded lens must give the same head |

Two kinds of part: `layer` (a Python file applied in memory over the unchanged grail) and `kernel` (a whole
Brainstem that replaces `brainstem.py`, e.g. the LisPy kernel from kody-w/lisppy; it gets the user's `agents/` and
`soul.md`, because GODD data follows every shape, and must pass the same rapp-1 oracle). Proven on 2026-11-03: a
layer part and the LisPy kernel part both passed the lab gate and ran live through the chain; replay reproduced the head.

Dynamics 365 lessons applied: publisher-scoped ids from day one, no unmanaged change can sit above published parts
(the lens is the only door), parts never hold user data, boot-time sha integrity of every part. Knight Capital: no
feature flags, a part is on by being in the shape. Everything is one page for an AI: `llms.txt`.

## The layer contract (`grail-layer/1`)

A layer is one Python file in RAR `stacks/grail-layers/`, with one function:

```python
def apply():
    """Called once, before the grail runs, inside the Brainstem process."""
```

Rules:
- **Pinned to the grail it was proven on.** The file declares `PROVEN_GRAIL = {"<sha256 of brainstem.py>", ...}`.
  The supervisor computes the running grail's sha256 and skips any layer not in that set (logged as
  "skipped as unproven"), so a new grail release can never run an unproven layer. Re-prove and add the
  sha for each grail release; `GRAIL_LAYERS_UNPINNED=1` lifts this only in a lab.
- **Declare what you touch.** `TOUCHES = {"requests.post", ...}` names the hook points the layer wraps.
  Two enabled layers touching the same point conflict: the earlier one in the pack's order applies, the
  later is skipped and logged. Never silent last-wins (the Dynamics 365 solution-layering lesson).
  A layer that composes instead (it calls through to whatever an earlier layer left at the point) declares
  `WRAPS = {"requests.post", ...}`: it chains onto the earlier layer, and a later `TOUCHES` on that point is
  still refused, so no layer can silently replace the chain.
- **Published layers only.** A layer loads only if it came from RAR with a matching sha256. A local file
  loads only in lab mode (`GRAIL_LAYERS_UNPINNED=1`) and is logged as such, so no ad hoc layer can sit
  on top of published ones unseen (the D365 "unmanaged layer always wins" lesson).
- **Layers own no user data.** Uninstalling or disabling a layer never deletes anything the user made.
- **Wrap, never replace.** Hold a reference to the original call and fall back to it on any exception.
- **Additive only.** Old requests behave exactly as before. A layer may add capability, never remove it.
- **No files written into the grail's folders.** Layer state lives under `~/.brainstem/layers/`.
- **A layer serves every user, or it is an agent.** Use-case features are agents in `agents/`, not layers.
- **Removable.** Deleting its name from `~/.brainstem/layers/enabled` turns it off at the next start.
- **Proven before publishing.** Grail+layer answers a real `/chat`, and a deliberately broken copy
  falls back to plain grail.

## Shipping a new grail update (the only path from now on)

1. Write the layer, and test it over a lab copy of the grail: `python3 grail_layers.py --kernel <lab>/brainstem.py --port <free>`.
2. Prove both: a real `/chat` through the layer, and a broken copy falling back to plain grail.
3. Publish the file in RAR `stacks/grail-layers/` and update `pack.json` (sha256, `default_enabled`).
4. Users get it the next time they run the one-liner, or through any distro that runs it.
5. Only if a layer has proven itself for every user, and the kernel truly cannot work without it,
   does Kody open a grail release window to fold it in. That's Linux's "out-of-tree first,
   upstream when proven".

## Safe mode and distros

- **Safe mode is always one step away.** `grail_layers.py --plain` (or `GRAIL_SAFE_MODE=1`) starts plain
  grail with no layers, whatever distro is installed. Layers are files beside the grail; not loading them is
  plain grail. The grail is the ultimate fallback regardless of distro.
- **Distros include layers the same way.** A distro's stack pack lists its layers (`install.layers`) and the
  grail sha it pins; the same supervisor applies them, and the same fallback protects them. The mainline
  path (this one-liner) and a distro differ only in which pack the layers come from.
- **See what's active.** `~/.brainstem/layers/applied.json` and `layers.log` say, for every start: the grail
  sha, which layers applied, which were skipped and why (unproven, conflict, failed).

## Layers today

| Layer | Adds | Proven |
|---|---|---|
| `responses_models` | Responses-API-only models (gpt-6.1-sol, gpt-6-luna and others) in the unchanged grail | Proven on grail 0.6.16 (public) and 0.6.17: model list 21 → 35; a gpt-6.1-sol chat called an agent through it on both; a broken layer fell back to plain grail; an unproven grail copy skipped the layer and ran plain (each 1 of 1) |
| `journal_tap` | Every chat turn (plain and streamed) is recorded in the owner's Brainstem Journal on this device, by default; the chat page keeps talking to :7071. Pause with a file (the JournalRecall agent toggles it in chat). No journal running: one line in the log, nothing else | Proven on grail 0.6.16 and 0.6.17 on lab ports: 2 of 2 turns recorded on each (one `/chat`, one `/chat/stream`), replies byte-identical to the kernel's, a paused store recorded 0 of 1, a missing journal left 2 of 2 answers untouched |
