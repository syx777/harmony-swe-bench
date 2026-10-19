"""Driver: set up the exact broken repo for one instance (deterministic carve+hollow on
a clean tree), run mini-swe-agent (via .venv_rl) to re-implement the removed feature,
then evaluate with the compile-level oracle. git restores the touched files afterwards.
Usage: python3 mini_drive.py <instance.json> [step_limit]
"""
import json, os, shutil, subprocess, sys, tempfile
from harmony_bench.graph import build_repo_index, build_symbol_graph
from harmony_bench.carve import carve
from harmony_bench.hollow import hollow_l2
from harmony_bench.author import module_root
from harmony_bench.runner import type_check, Verdict
from harmony_bench.paths import BENCH_ROOT, repo_root, toolchain_root

REPO = repo_root()
TC = toolchain_root(REPO)
# path to a python that has mini-swe-agent installed (see README); override via env
VENV = os.environ.get("MSWEA_VENV_PYTHON", "python3")
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mini_runner.py")
INST = json.load(open(sys.argv[1]))
STEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 40
ANCHOR = INST["harmony"]["anchor"]

if not os.path.isfile(os.path.join(REPO, "hvigor", "hvigor-config.json5")):
    raise SystemExit("Set HB_REPO_ROOT to the Linux path of a disposable sample_in_harmonyos checkout")

def git(*a, check=False):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True, check=check)

anchor_file = os.path.join(REPO, INST["harmony"]["target_file"])
mroot = module_root(anchor_file, REPO)
module_name = os.path.basename(mroot)
local_test_rel = os.path.relpath(os.path.join(mroot, "src", "test", "LocalUnit.test.ets"), REPO)
patch_files = [l[6:] for l in INST["patch"].splitlines() if l.startswith("+++ b/")]

# 0) clean the files we will touch, so carve reads the pristine tree
for rel in patch_files + [local_test_rel]:
    git("checkout", "--", rel)

# 1) deterministic broken state (carve + hollow), matches the instance
idx = build_repo_index(REPO, ["common", "features", "products"])
sg = build_symbol_graph(idx)
cv = carve(idx, sg, ANCHOR, anchor_file)
hr = hollow_l2(idx, cv)
code_files = [fh for fh in hr.files if fh.target_rel.endswith(".ets")]
local_test = os.path.join(REPO, local_test_rel)

# hidden F2P test = NEW side of the stored test_patch
f2p = "\n".join(l[1:] for l in INST["test_patch"].splitlines()
                if (l.startswith("+") or l.startswith(" ")) and not l.startswith("+++")) + "\n"

files_hint = "\n".join(f"  - {fh.target_rel}" for fh in code_files)
task = (
    f"{INST['problem_statement']}\n\n"
    f"A feature was removed from this HarmonyOS ArkTS module (`{module_name}`). "
    f"Re-implement the missing symbol(s) [{', '.join(hr.deleted_names)}] so the module "
    f"builds and its tests pass. The feature spanned these files (their feature code is currently gone):\n"
    f"{files_hint}\n\nDo not modify any test files.")
open("/tmp/mini_task.txt", "w").write(task)

bench_hidden = []          # (stashed_path_outside_repo, original_path) for restore
try:
    for fh in hr.files:                       # write broken sources (code + router_map)
        open(os.path.join(REPO, fh.target_rel), "w").write(fh.broken_source)

    # --- ANTI-HACK sandbox: hide every source of the answer so the agent must
    #     implement from the problem statement, not recover it.
    #     (1) git history  (2) build caches  (3) the benchmark's OWN data dirs —
    #     data/instances/*.json carry the gold `patch`; _archive has old golds.
    import shutil
    for d in [os.path.join(mroot, ".test"), os.path.join(mroot, "build"),
              os.path.join(REPO, ".hvigor")]:
        shutil.rmtree(d, ignore_errors=True)
    git_dir, git_hidden = os.path.join(REPO, ".git"), os.path.join(REPO, ".git_hidden_eval")
    if os.path.isdir(git_dir):
        os.rename(git_dir, git_hidden)
    BENCH = str(BENCH_ROOT)
    # Keep rename operations on the benchmark's filesystem, including /mnt/c.
    # This parent must be outside the material checkout; see docs/wsl-udocker.md.
    _stash_parent = os.path.dirname(REPO) if os.path.commonpath([REPO, BENCH]) == REPO else os.path.dirname(BENCH)
    _stash = tempfile.mkdtemp(prefix=".hsb_answers_", dir=_stash_parent)
    for sub in ("data", "_archive"):
        p = os.path.join(BENCH, sub)
        if os.path.isdir(p):
            h = os.path.join(_stash, sub); os.rename(p, h); bench_hidden.append((h, p))

    env = dict(os.environ); env["MSWEA_COST_TRACKING"] = "ignore_errors"
    print(f"=== broken ready ({len(code_files)} code files; git+caches+bench-data hidden). "
          f"mini-swe-agent on {ANCHOR}, step_limit={STEPS} ===", flush=True)
    subprocess.run([VENV, RUNNER, "/tmp/mini_task.txt", REPO, str(STEPS),
                    "/tmp/mini_result.json"], env=env, cwd=os.getcwd())

    for h, p in bench_hidden:                 # restore benchmark data
        if os.path.isdir(h) and not os.path.isdir(p): os.rename(h, p)
    if os.path.isdir(git_hidden):             # restore git for eval/restore
        os.rename(git_hidden, git_dir)
    info = json.load(open("/tmp/mini_result.json")) if os.path.exists("/tmp/mini_result.json") else {}
    print("agent:", info, flush=True)

    os.makedirs("/tmp/agent_out", exist_ok=True)
    for old in os.listdir("/tmp/agent_out"):   # clear stale files from prior instances
        try: os.remove(os.path.join("/tmp/agent_out", old))
        except IsADirectoryError: shutil.rmtree(os.path.join("/tmp/agent_out", old), ignore_errors=True)
    for fh in code_files:
        p = os.path.join(REPO, fh.target_rel)
        open(f"/tmp/agent_out/{os.path.basename(fh.target_rel)}", "w").write(open(p).read())

    # EVAL: hidden F2P test + agent's solution -> compile-level oracle
    open(local_test, "w").write(f2p)
    r = type_check(module_name, REPO, TC)
    open("/tmp/agent_out/compile.log", "w").write(r.log_tail)
    print("\n==== EVAL ====")
    print(f"exit_status={info.get('exit_status')}  verdict={r.verdict.value}  "
          f"RESOLVED={r.verdict is Verdict.GREEN}")
    if r.verdict is not Verdict.GREEN:
        print("compile tail:", r.log_tail[-600:])
finally:
    # restore benchmark data dirs (stashed outside the repo) if still hidden
    for h, p in bench_hidden:
        if os.path.isdir(h) and not os.path.isdir(p):
            os.rename(h, p)
    gh = os.path.join(REPO, ".git_hidden_eval")
    if os.path.isdir(gh) and not os.path.isdir(os.path.join(REPO, ".git")):
        os.rename(gh, os.path.join(REPO, ".git"))
    for rel in patch_files + [local_test_rel]:
        git("checkout", "--", rel)
    print("(git-restored)", flush=True)
