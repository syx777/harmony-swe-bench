from __future__ import annotations
import glob
import json
import os

import click

from harmony_bench.graph import build_repo_index, build_symbol_graph
from harmony_bench.carve import carve
from harmony_bench.carve.closure import CarveError
from harmony_bench.carve.enumerate import enumerate_candidates
from harmony_bench.hollow import hollow_l2
from harmony_bench.author import author_issue, author_test, module_root
from harmony_bench.verify import gate
from harmony_bench.build import build_instance
from harmony_bench.schema import read_instance, write_instance
from harmony_bench.llm.client import MockLLMClient, build_client, load_config
from harmony_bench.paths import toolchain_root

_SUBDIRS = ["common", "features", "products", "AppScope"]


def _head_commit(repo_root: str) -> str:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_root).decode().strip()
    except Exception:
        return "unknown"


def _default_toolchain(repo_root: str) -> str:
    return toolchain_root(repo_root)


@click.group()
def cli() -> None:
    """harmony-swe-bench: carve HarmonyOS features into compile-verified new_feature tasks."""


@cli.command()
@click.option("--repo-root", envvar="HB_REPO_ROOT", required=True)
@click.option("--module", "module_dir", default="common",
              help="Module directory under the repo (e.g. common, features/mine)")
@click.option("--num", default=3)
@click.option("--out", default="data/instances")
@click.option("--toolchain", envvar="HB_TOOLCHAIN_ROOT", default=None, help="harmony_toolchain dir (default: repo/../harmony_toolchain)")
@click.option("--config", "config_path", default=None)
@click.option("--mock", is_flag=True, default=False, help="Canned LLM + skip the oracle gate")
@click.option("--no-verify", is_flag=True, default=False, help="Skip the compile-level gate")
def gen(repo_root, module_dir, num, out, toolchain, config_path, mock, no_verify):
    repo_root = os.path.abspath(repo_root)
    toolchain = toolchain or _default_toolchain(repo_root)
    commit = _head_commit(repo_root)
    module_path = os.path.join(repo_root, module_dir)

    idx = build_repo_index(repo_root, _SUBDIRS)
    sg = build_symbol_graph(idx)
    cands = enumerate_candidates(idx, sg, module_path)
    click.echo(f"{len(cands)} candidates in {module_dir}")

    if mock:
        client = MockLLMClient(responses=[
            "## Mock feature\n### Core Functionality\nmock",
            "import { describe } from '@ohos/hypium';\nexport default function localUnitTest(){}"])
        no_verify = True
    else:
        cfg_path = config_path or os.path.join(os.path.dirname(__file__), "..", "config", "default.yaml")
        client = build_client(load_config(cfg_path))

    count = 0
    for cand in cands:
        if count >= num:
            break
        try:
            cv = carve(idx, sg, cand.name, cand.file)
        except CarveError as exc:
            click.echo(f"  skip {cand.name}: {exc}")
            continue
        try:
            hr = hollow_l2(idx, cv)
            issue = author_issue(client, cv)
            f2p = author_test(client, cv, repo_root)
            inst = build_instance(cv, hr, issue, f2p, repo_root, commit)
            if not no_verify:
                mroot = module_root(cv.anchor_file, repo_root)
                files = [(fh.target_rel, fh.broken_source, fh.golden_source)
                         for fh in hr.files]
                gr = gate(repo_root, toolchain, mroot, files, f2p)
                inst.validation.integrity_green = gr.integrity_green
                inst.validation.broken_red = gr.broken_red
                inst.validation.gold_green = gr.gold_green
                if not gr.passed:
                    click.echo(f"  drop {cand.name}: gate integrity={gr.integrity_verdict} "
                               f"broken={gr.broken_verdict} gold={gr.gold_verdict}")
                    continue
            path = write_instance(inst, out)
            count += 1
            click.echo(f"  wrote {os.path.basename(path)} [{cand.name} "
                       f"files={len(cv.extract_files)} impl={cand.impl_lines}L fan_in={cand.fan_in}]")
        except Exception as exc:
            click.echo(f"  skip {cand.name}: {exc}")
    click.echo(f"generated {count} instances")


@cli.command()
@click.option("--instances", default="data/instances")
@click.option("--output", default="data/dataset.jsonl")
@click.option("--require-gate/--no-require-gate", default=True,
              help="Only export instances that passed the compile-level gate.")
def export(instances, output, require_gate):
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    total = kept = 0
    skips = {}
    with open(output, "w", encoding="utf-8") as out_fh:
        for path in sorted(glob.glob(os.path.join(instances, "*.json"))):
            try:
                inst = read_instance(path)
            except Exception:
                skips["unparseable"] = skips.get("unparseable", 0) + 1
                continue
            total += 1
            v = inst.validation
            if require_gate and not (v.integrity_green and v.broken_red and v.gold_green):
                skips["gate"] = skips.get("gate", 0) + 1
                continue
            out_fh.write(inst.model_dump_json() + "\n")
            kept += 1
    click.echo(f"exported {kept}/{total} to {output}")
    if skips:
        click.echo("; ".join(f"skipped {n}: {r}" for r, n in skips.items()))


@cli.command()
@click.option("--instances", default="data/instances")
def stats(instances):
    total = 0
    by_module = {}
    gated = 0
    for path in sorted(glob.glob(os.path.join(instances, "*.json"))):
        try:
            inst = read_instance(path)
        except Exception:
            continue
        total += 1
        by_module[inst.harmony.module] = by_module.get(inst.harmony.module, 0) + 1
        if inst.validation.integrity_green and inst.validation.broken_red and inst.validation.gold_green:
            gated += 1
    click.echo(f"total: {total}  gate-passed: {gated}")
    for k, v in sorted(by_module.items()):
        click.echo(f"  {k}: {v}")


if __name__ == "__main__":
    cli()
