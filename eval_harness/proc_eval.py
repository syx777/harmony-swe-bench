"""Process-level scorecard for a harmony-swe-bench trajectory (9 selected metrics).

Axis A (progress):  residual_arkts_errors, symbol_redefinition_rate
Axis C (process):   steps, {explore,edit,verify}_steps, redundant_reads,
                    edit_thrashing, localize_before_edit, consecutive_dup_cmds,
                    anti_hack_probes

All Axis-C metrics come purely from the mini-swe-agent trace (zero extra cost, no LLM).
Axis-A comes from the gate's compile.log + a static parse of the agent's final files.

Usage: python3 proc_eval.py <instance.json> <traj.json> <agent_out_dir> [compile.log]
Prints a human table and (last line) a JSON blob for programmatic use.
"""
import json, os, re, sys, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harmony_bench.parser.ets_parser import parse_source

INST = json.load(open(sys.argv[1]))
TRAJ = json.load(open(sys.argv[2]))
AOUT = sys.argv[3]
CLOG = sys.argv[4] if len(sys.argv) > 4 else os.path.join(AOUT, "compile.log")

# ---------- pull executed commands from the trace ----------
_CMD = re.compile(r"```mswea_bash_command\s*\n(.*?)\n```", re.DOTALL)
def _txt(m):
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c or ""
cmds = [ _CMD.search(_txt(m)).group(1).strip()
         for m in TRAJ.get("messages", [])
         if m.get("role") == "assistant" and _CMD.search(_txt(m)) ]

# ---------- command classification + file extraction ----------
_ETS = re.compile(r"[\w./-]+\.(?:ets|ts|json5|json)")
def files_in(c):  return [os.path.basename(f) for f in _ETS.findall(c)]
def is_edit(c):   return ("cat >" in c) or bool(re.search(r">\s*\S+\s*<<", c)) or bool(re.search(r"\btee\s+\S", c))
def is_read(c):   return (not is_edit(c)) and bool(re.match(r"\s*(cat|sed -n|head|tail|nl|less)\b", c))
def is_search(c): return bool(re.match(r"\s*(grep|find|ls|rg|egrep|fgrep)\b", c))
def is_verify(c): return bool(re.search(r"\b(hvigorw|tsc|npm run|node |ohpm)\b", c))
def is_git(c):    return bool(re.search(r"\bgit\b", c))
def touches_test(c): return bool(re.search(r"/test/|ohosTest|LocalUnit\.test|List\.test|\.test/", c))
# attempts to recover the answer from the benchmark's own data (instances carry the
# gold patch) — match concrete answer paths/ops, not a bare mention of the bench dir.
def touches_bench(c): return bool(re.search(r"data/instances|data\.hidden|\.hsb_answers|dataset\.jsonl|/instances/|git apply|gold[_.]?patch|\bd\[.patch.\]|\.patch\b", c))

reads, edits = collections.Counter(), collections.Counter()
first_read, first_edit = {}, {}
n_explore = n_edit = n_verify = n_git = n_test = n_bench = 0
for i, c in enumerate(cmds):
    if is_edit(c):
        n_edit += 1
        for b in files_in(c):
            edits[b] += 1; first_edit.setdefault(b, i)
    elif is_read(c) or is_search(c):
        n_explore += 1
        for b in files_in(c):
            reads[b] += 1; first_read.setdefault(b, i)
    if is_verify(c): n_verify += 1
    if is_git(c):    n_git += 1
    if touches_test(c): n_test += 1
    if touches_bench(c): n_bench += 1

edited = set(edits)
localize_ok = sum(1 for b in edited if b in first_read and first_read[b] < first_edit[b])
dup = sum(1 for a, b in zip(cmds, cmds[1:]) if a == b)

# ================= AXIS A (progress) =================
declared = set()
for fn in os.listdir(AOUT):
    if not fn.endswith(".ets"): continue
    pf = parse_source(open(os.path.join(AOUT, fn)).read())
    declared |= {e.name for e in pf.exports} | {f.name for f in pf.functions} | {s.name for s in pf.structs}
    if pf.default_export_name: declared.add(pf.default_export_name)
need = set(INST["harmony"].get("closure", [])) - set(INST["harmony"].get("keep_interface", []))
redef = len(need & declared)
clog = open(CLOG).read() if os.path.exists(CLOG) else ""
resid_err = len(re.findall(r"ArkTS Compiler Error", clog))

axisA = {
    "residual_arkts_errors": resid_err,
    "symbol_redefinition_rate": f"{redef}/{len(need)}",
    "_symbol_redef_frac": round(redef / max(1, len(need)), 3),
}
axisC = {
    "steps": len(cmds),
    "explore_steps": n_explore,
    "edit_steps": n_edit,
    "verify_steps": n_verify,
    "redundant_reads": sum(v - 1 for v in reads.values() if v > 1),
    "edit_thrashing": sum(v - 1 for v in edits.values() if v > 1),
    "localize_before_edit": f"{localize_ok}/{len(edited)}",
    "consecutive_dup_cmds": dup,
    "anti_hack_probes": n_git + n_test + n_bench,
    "_anti_hack_breakdown": {"git": n_git, "test_access": n_test, "bench_data": n_bench},
}

print("instance:", INST["instance_id"],
      "| deleted symbols:", INST["harmony"]["difficulty"].get("deleted_symbols"),
      "| files:", INST["harmony"]["difficulty"].get("files_touched"))
print("\n== AXIS A · progress ==")
for k, v in axisA.items():
    if not k.startswith("_"): print(f"  {k:26} {v}")
print("\n== AXIS C · process ==")
for k, v in axisC.items():
    if not k.startswith("_"): print(f"  {k:26} {v}")
print("\nJSON " + json.dumps({"axisA": axisA, "axisC": axisC}, ensure_ascii=False))
