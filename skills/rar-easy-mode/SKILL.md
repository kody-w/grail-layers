---
name: rar-easy-mode
description: Install RAR packs, starters and store agents into the person's own Brainstem and test them, from one plain sentence. Works in GitHub Copilot Chat (Agent mode, the default) and in any AI that can run commands on this computer (Claude Code, Codex, Gemini CLI and others).
---

# RAR Easy Mode

Once this file is attached to your chat (drag it into GitHub Copilot Chat in Agent mode, or give it to any AI
that can run terminal commands), you do all the work for the person. They only send plain sentences. Never ask
them for URLs, commands, ports or file paths.

## Sources (read, never guess)
- Catalog: https://kody-w.github.io/RAR/registry.json (`stack_packs`: packs and starters; `agents`: store agents)
- Shelves: each pack's `shelf` is `starter`, `pack` (looks after you) or `app`
- Installer: https://kody-w.github.io/grail-layers/add.sh (it checks every sha256, test-boots before going live,
  never overwrites, undoes in one step)
- Rules: https://kody-w.github.io/RAR/RAPP.md

## "Show me RAR" (or any request without a pack name)
1. Fetch the catalog. List starters first, then packs, then apps: one line each, `display_name — purpose`.
   Plain words only: no JSON, no file names.
2. Ask one question: which ones they want (numbers, names or a description; several are fine).
3. Confirm the resolved names back in one line, then continue as below. Never choose for them.

## "Give me <names> and test it for me"
1. Health: `curl -s http://localhost:7071/health`. If nothing answers, install and start the Brainstem with
   `curl -fsSL https://kody-w.github.io/grail-layers/install.sh | bash` and wait for /health.
2. Plan first: run `curl -fsSL https://kody-w.github.io/grail-layers/packs.py -o /tmp/rar-packs.py` and
   `python3 /tmp/rar-packs.py --kernel ~/.brainstem/src/rapp_brainstem/brainstem.py --plan-json <names>`.
   If `refused` is not empty, stop and say why in one sentence. Mention any `warnings` in plain words.
3. Install: `curl -fsSL https://kody-w.github.io/grail-layers/add.sh | bash -s -- <names>` (store agents are
   `@publisher/name`; packs and starters by name; order does not matter).
4. Wait for /health again (the Brainstem may restart itself once).
5. Test for real: for each installed pack, send one plain question built from its `purpose` to
   `POST http://localhost:7071/chat` with `{"user_input": "<question>"}` and read `response` and `agent_logs`.
   A pack passes only if its agent appears in `agent_logs` and the answer is about the question.
6. Reply with: what was installed, one line per pack with pass or fail and the evidence (the agent that ran),
   and the next sentence they can send: `Undo it` or `Show me RAR`.

## "Undo it"
Run `python3 ~/.brainstem/layers/grail_layers.py --kernel ~/.brainstem/src/rapp_brainstem/brainstem.py --undo`
and confirm /health answers. Agents added stay in the person's agents folder (backups of replaced ones are in
`~/.brainstem/layers/agent-backups/`); say so.

## Rules
- The grail (`brainstem.py`) is never edited. Everything installs on top.
- Never invent a result, never retry until something happens to pass, never hide a failure.
- Never put secrets, tokens or keys anywhere; sign-in stays the person's own.
- If the AI you are running in cannot run commands, say so in one sentence and give the person the one line from
  step 3 to paste in Terminal.
