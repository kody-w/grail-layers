"""Any 1..N packs, in any order, with duplicates, must give ONE plan (Debian/Nix lesson: install order never decides
the result). Runs packs.py --plan-json for every subset of POOL in several orders against live RAR; changes nothing."""
import itertools, json, random, subprocess, sys
KERNEL = sys.argv[1]
POOL = sys.argv[2:] or ["night-watch", "private-mode", "inheritance", "judge", "starter-personal"]
rng = random.Random(7)
runs = bad = 0
for k in range(1, len(POOL) + 1):
    for subset in itertools.combinations(POOL, k):
        orders = {tuple(subset), tuple(reversed(subset))}
        for _ in range(2):
            o = list(subset) + [rng.choice(subset)]  # a duplicate thrown in
            rng.shuffle(o); orders.add(tuple(o))
        plans = set()
        for o in orders:
            r = subprocess.run([sys.executable, "packs.py", "--kernel", KERNEL, "--plan-json", *o], capture_output=True, text=True)
            plans.add(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "ERR " + r.stderr[-200:])
            runs += 1
        if len(plans) != 1:
            bad += 1
            print("ORDER CHANGED THE PLAN for", subset, *plans, sep="\n  ")
print(f"{runs} runs over {2 ** len(POOL) - 1} combinations; {bad} combinations where order changed the plan")
sys.exit(1 if bad else 0)
