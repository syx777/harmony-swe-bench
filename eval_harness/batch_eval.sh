#!/bin/bash
# Serial (concurrency=1) run over ALL instances + per-instance proc_eval.
# Scaffold: bring your own — this driver uses mini-swe-agent only as a worked example.
# Container engine: set HB_CONTAINER_ENGINE=docker (recommended) or udocker (default).
set -u
cd "$(dirname "$0")/.."          # harmony-swe-bench/
export PYTHONPATH=.
export HB_CONTAINER_ENGINE=${HB_CONTAINER_ENGINE:-udocker}
[ "$HB_CONTAINER_ENGINE" = udocker ] && export UDOCKER_DIR=${UDOCKER_DIR:-/tmp/udocker}
: "${MSWEA_VENV_PYTHON:?set MSWEA_VENV_PYTHON to a python that has your scaffold installed (example: mini-swe-agent)}"
: "${ANTHROPIC_AUTH_TOKEN:?set ANTHROPIC_AUTH_TOKEN}"
STEPS=${STEPS:-75}
OUT=/tmp/eval_results; mkdir -p "$OUT"
SUM="$OUT/summary.tsv"
echo -e "instance\tfiles\tdel_syms\tverdict\tresid_err\tsym_redef\tsteps\texplore\tedit\tredund_read\tedit_thrash\tloc_before_edit\tdup\tanti_hack" > "$SUM"

for f in data/instances/*.json; do
  id=$(basename "$f" .json | sed 's/sample_in_harmonyos__//')
  echo "############ $id ############" | tee -a "$OUT/run.log"
  # 1) solve + oracle re-grade (writes /tmp/mini_traj.json, /tmp/agent_out, restores repo)
  timeout 1500 python3 eval_harness/mini_drive.py "$f" "$STEPS" >>"$OUT/run.log" 2>&1
  verdict=$(grep -aoE "RESOLVED=(True|False)" "$OUT/run.log" | tail -1)
  # 2) preserve artifacts before next run overwrites
  cp /tmp/mini_traj.json "$OUT/$id.traj.json" 2>/dev/null
  cp -r /tmp/agent_out "$OUT/$id.agent_out" 2>/dev/null
  # 3) process scorecard
  python3 eval_harness/proc_eval.py "$f" /tmp/mini_traj.json /tmp/agent_out > "$OUT/$id.scorecard.txt" 2>&1
  # 4) append one summary row (parse the JSON line)
  python3 - "$f" "$OUT/$id.scorecard.txt" "$verdict" "$SUM" <<'PY'
import sys, json, re
inst=json.load(open(sys.argv[1])); sc=open(sys.argv[2]).read()
verdict=sys.argv[3].split("=")[-1] if "=" in sys.argv[3] else "?"
m=re.search(r"^JSON (.+)$", sc, re.MULTILINE)
d=json.loads(m.group(1)) if m else {"axisA":{},"axisC":{}}
a,c=d["axisA"],d["axisC"]
# safety net: void any run that reached the benchmark's own answer files
if c.get("_anti_hack_breakdown",{}).get("bench_data",0) > 0:
    verdict = "VOID(cheat)"
h=inst["harmony"]; diff=h["difficulty"]
row=[inst["instance_id"].split("__")[1], diff.get("files_touched"), diff.get("deleted_symbols"),
     verdict, a.get("residual_arkts_errors"), a.get("symbol_redefinition_rate"),
     c.get("steps"), c.get("explore_steps"), c.get("edit_steps"), c.get("redundant_reads"),
     c.get("edit_thrashing"), c.get("localize_before_edit"), c.get("consecutive_dup_cmds"),
     c.get("anti_hack_probes")]
open(sys.argv[4],"a").write("\t".join(str(x) for x in row)+"\n")
print("row:", row)
PY
done
echo "===== SUMMARY =====" | tee -a "$OUT/run.log"
column -t -s $'\t' "$SUM" | tee -a "$OUT/run.log"
