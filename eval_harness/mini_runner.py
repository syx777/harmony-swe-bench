"""Run a mini-swe-agent DefaultAgent (2.4.x) against an Anthropic-compatible gateway.
Invoked by the driver via a python that has minisweagent installed. Edits happen
in `cwd` on disk; the driver sets up the broken repo and evaluates afterwards.
Args: <task_file> <cwd> <step_limit> <out_json>

Endpoint + model come from the env (override to point at your own gateway):
  ANTHROPIC_AUTH_TOKEN  — Bearer token
  ANTHROPIC_BASE_URL    — gateway base URL (default: https://api.anthropic.com)
  MSWEA_MODEL_NAME      — litellm model id (default: anthropic/claude-opus-4-8)
"""
import json, os, sys, yaml
from pathlib import Path
from minisweagent import package_dir
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

task = Path(sys.argv[1]).read_text()
cwd, step_limit, outp = sys.argv[2], int(sys.argv[3]), sys.argv[4]
tok = os.environ["ANTHROPIC_AUTH_TOKEN"]

model = LitellmTextbasedModel(
    model_name=os.environ.get("MSWEA_MODEL_NAME", "anthropic/claude-opus-4-8"),
    model_kwargs={
        "api_base": os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        "api_key": "x",
        "extra_headers": {"Authorization": f"Bearer {tok}"},
        "extra_body": {"thinking": {"type": "disabled"}},
        "max_tokens": 8000,
        "temperature": 1.0,
    },
)
cfg = yaml.safe_load((package_dir / "config" / "default.yaml").read_text())["agent"]
cfg["step_limit"] = step_limit
cfg["cost_limit"] = 0.0
cfg["output_path"] = "/tmp/mini_traj.json"
agent = DefaultAgent(model, LocalEnvironment(cwd=cwd), **cfg)
res = agent.run(task)
json.dump({"exit_status": res.get("exit_status", ""),
           "submission_len": len(res.get("submission", "") or ""),
           "n_calls": agent.n_calls}, open(outp, "w"))
print("AGENT DONE exit_status=%s calls=%d" % (res.get("exit_status"), agent.n_calls), flush=True)
