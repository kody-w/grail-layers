# Pack lifecycle (ALM) — how packs live for years

Packs are ripped every day by people in the wild and pile up for years. This page fixes the rules
once, at the start, so nothing that looks fine today bites later. One sentence: **a pack is an
immutable, sha-pinned bundle of parts; a cart of packs resolves to one plan, whatever the order;
the plan is gated before anything changes; and every step can be undone.**

## The lifecycle

| Stage | What happens | Rule |
|---|---|---|
| Author | A publisher writes layers (`grail-layer/1`) and agents (`*_agent.py`, factories `*_factory_agent.py`) | A use case is an agent, never a layer |
| Publish | `stacks/<pack>/pack.json` in RAR with a version and the sha256 of every file | Published bytes never change; a fix is a new version |
| Verify | RAR CI checks shas, receipts, file names, duplicate display names | A red check is the system working |
| Discover | The RAR gallery shows each pack as a sealed card; starters list what they include | Cards show real facts only |
| Cart | The person adds packs; includes resolve; the same sha shows once | Order of adding never matters |
| Rip and select | Every card can be kept or dropped; dropped cards become `--skip` | Dropping a part another pack relies on warns, never silently breaks |
| Plan | `packs.py --plan-json` gives one sorted plan | Same set of packs = same plan, byte for byte |
| Gate | One `--apply` tile with every part boots in the lab and answers a real `/chat` | Nothing live changes until the gate passes |
| Install | Agents are fetched and sha-checked first, imported only after the gate | All or nothing |
| Upgrade | Same tool name = upgrade in place, with a backup | Different bytes under a taken name install scoped (`<pack>__<file>`) |
| Undo | `--undo` re-applies the previous passing shape; agent backups restore | One step, every time |
| Retire | Old names become aliases with a status and keep being served | Delete by aliasing, never by breaking |
| Tombstone | Removal only after a measured check shows zero use | Every URL that ever worked keeps answering |

## Edge cases, settled

- **Order.** Packs resolve as a sorted set; parts sort by one point order, then id. Proven: every subset of the pool, in identity, reversed and shuffled orders with duplicates, gives one plan (`tests/permute_packs.py`).
- **1 to N packs.** Includes expand first; a starter is just a pack with `install.includes`.
- **Duplicates.** Same sha = one install. Same id on the same point merges.
- **Conflicts.** Two different exclusive parts on one point refuse the whole cart with a reason; composable (`WRAPS`) parts chain.
- **Tool-name clashes across publishers.** The kernel quarantines a duplicate tool name; the installer scopes the file name so nothing is overwritten.
- **Kernel moves.** Every layer pins the grail shas it was proven on; an unproven layer is skipped, and the grail is always the fallback.
- **Partial carts.** Skipping a part that an including pack relies on is a warning in the plan, shown before install.
- **The machine.** Disk, launchd and the drill results are watched by Night Watch; the supervisor falls back to plain grail on any failed boot.

## Publishers at scale

- Ids are publisher-scoped from day one (`@publisher/part`), so two publishers never collide by name.
- Every file is content-addressed; a version is a sha set, so a mirror or a cache can never serve different bytes under the same version.
- A pack can only add; it never edits the grail or another publisher's part.
- Receipts name who published what; critical parts get a second owner (MAINTAINERS).

## Free tuition: who paid for these lessons, and our answer

| Lesson | Who learned it | Our answer |
|---|---|---|
| Conflicts must be declared, not discovered | Debian `Conflicts:`, DLL hell | Exclusive points refuse the whole cart before install |
| Bundles of packages for a purpose | Debian tasks, metapackages, Homebrew Brewfile | Starter packs are packs with includes |
| Install is a transaction with rollback | Nix, Fedora Silverblue | Gate first, all or nothing, one-step undo |
| Never let published code vanish | npm left-pad, 2016 | Published bytes stay served; retire by alias |
| Same name, different bytes is an attack | npm typosquats, xz 2024 | sha-pinned files, scoped ids, two owners on critical parts |
| Last-wins layering hides breakage | Dynamics 365 solution layers | No silent last-wins; refusals are logged and shown |
| Order-dependent installs drift | Ansible/Puppet run order, Windows install order | Sorted set in, one plan out, proven by permutation test |
| Roll out in rings | Windows Update rings, Chrome channels | Lab gate per device, chain frames per boot |
| Review before the store shows it | App stores | RAR CI and receipts before a card appears |
| Base stays pristine | Linux kernel, out-of-tree first | Grail never edited; layers, agents and distros sit on top |
