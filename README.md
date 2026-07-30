# harmony-swe-bench

**A multi-file `new_feature` benchmark for HarmonyOS Code Agents**, together with a
**fine-grained, process-level evaluation** of *how* an agent solves each task.

Two deliverables:
1. **Benchmark construction** (`harmony_bench/`) — carve a real, cohesive feature out of
   a HarmonyOS project, verify it with an execution-backed oracle, and emit
   SWE-bench-style instances. Ships **8 gate-verified multi-file instances** in `data/`.
2. **Process evaluation** (`eval_harness/`) — run an agent on an instance and score its
   *trajectory* with **9 fine-grained metrics** (see Part 1 §1.4), not just
   resolved / not-resolved.

---

# Part 1 · Highlights (what we built and why it works)

## 1.1 The idea: carve a feature, ask the agent to rebuild it

A real HarmonyOS repo (`sample_in_harmonyos`) has no PRs / issues / commit trail, so the
classic SWE-bench "PR → task" recipe does not apply. Instead we treat every existing,
working feature as the **golden answer** and **carve it out** of the codebase along the
dependency graph. Each instance:

- deletes a **cohesive feature** — an anchor ArkTS symbol plus its private closure,
  possibly spanning several files — and
- asks the agent to **re-implement it** from an issue + a runnable `@ohos/hypium` test.

We call this the **L2 "from-interface"** task: the agent gets the interface contract and
writes the feature from scratch.

## 1.2 Reference-closed multi-file carving (never removes shared code)

A feature is a **connected subgraph** of the dependency graph, not a single function.
`carve/` grows the delete set from the anchor and includes a helper **only when every
symbol that references it is also being deleted** (a symbol-level predecessor fixpoint).
Anything used from outside the deleted set stays as *interface*. This guarantees the
multi-file deletion is clean: shared code is never carved away.

The pipeline:

```
enumerate → carve → hollow → gate(3-way) → author → build → export
```

| module | role |
|---|---|
| `parser/` | tree-sitter ArkTS parser (funcs / classes / enums / interfaces, default & `export *` / `export {default as}` barrels, `@Component`) |
| `graph/` | import graph + symbol graph (fan-in) + `forward.py` closure edges |
| `carve/` | enumerate anchors; **symbol-level reference-closed** multi-file closure; single-module, `max_files` cap |
| `hollow/` | L2 multi-file removal: delete symbol spans across files + strip now-dangling imports + swallow ArkUI decorators + drop matching `routerMap` entries; golden = on-disk original |
| `author/` | generate the issue from the golden symbol's contract + hypium F2P test (LLM) |
| `runner/` | execution-backed oracle: `hvigorw test` in a container, GREEN / RED from the `UnitTestArkTS` build markers |
| `verify/` | **3-way gate** = P2P + F2P + gold, run on the real toolchain |
| `build.py`, `schema.py`, `cli.py` | assemble / emit SWE-bench-style instances |

## 1.3 Execution-backed oracle (the 3-way integrity gate)

The oracle drives the **real HarmonyOS ArkTS toolchain** (`hvigorw test` with strict
type checking) headless inside a container and reads the build's `UnitTestArkTS` markers
to decide **GREEN** (the feature resolves and its interface contract holds) or **RED**
(the feature is absent / its contract is violated).

An instance is kept **only if all three checks hold** — this is what guarantees the
multi-file deletion is clean and the task is genuinely solvable:

1. **broken + stock test → GREEN** — the rest of the module still builds (no dangling refs left behind).
2. **broken + F2P test → RED** — the feature is genuinely absent (the test can no longer resolve it).
3. **golden + F2P test → GREEN** — the on-disk original restores it and the module builds.

## 1.4 Fine-grained, process-level evaluation (9 metrics)

Binary resolve/not throws away almost everything about *how* the agent worked. We score
the trajectory on two axes (each metric's direction is listed below;
`eval_harness/proc_eval.py` is the reference implementation):

- **Axis A · progress** (how far it got, partial credit)
  - `residual_arkts_errors` — remaining compiler errors after the agent submits
  - `symbol_redefinition_rate` — fraction of the required symbols the agent re-declared
- **Axis C · process** (efficiency & discipline, extracted purely from the trace)
  - `steps`, `explore_steps`, `edit_steps`, `verify_steps`
  - `redundant_reads`, `edit_thrashing`
  - `localize_before_edit` — read-before-write discipline
  - `consecutive_dup_cmds` — stuck-in-a-loop detector
  - `anti_hack_probes` — attempts to recover the answer from git / test dirs / the benchmark's own data

## 1.5 Anti-cheat sandbox (a real, closed attack surface)

The biggest risk of a "carve-and-rebuild" benchmark is that the agent **recovers the
answer from a reference** instead of solving. During evaluation we caught and closed two
concrete holes:

- **git history** — an agent used `git show <sha>:<file>` to read the golden and copy it.
- **the benchmark's own data** — `data/instances/*.json` carry the gold `patch`; an agent
  read it directly.

The eval driver now, during solving, **hides every source of the answer**: `.git`
history, build caches, and the benchmark's `data` / `_archive` dirs (moved outside the
repo tree). The `anti_hack_probes` metric records any attempt, and the batch runner
**voids** any run that reaches an answer file — a safety net on top of the sandbox.

## 1.6 Results we measured

Running **mini-swe-agent** (Claude Opus 4.8, `step_limit=75`, serial, anti-cheat sandbox
on) over all 8 instances:

| instance (anchor) | files | del syms | **RESOLVED** | resid. err | sym. redef | steps | explore | edit | redund. read | localize |
|---|---|---|---|---|---|---|---|---|---|---|
| SourceCodeLinkPageBuilder | 2 | 3 | ✅ **True** | 0 | 3/3 | 35 | 27 | 4 | 6 | 4/4 |
| MainPageBuilder (pc) | 2 | 3 | False | 3 | 3/3 | 41 | 34 | 2 | 13 | 2/2 |
| AboutViewBuilder | 4 | 8 | False | 3 | 8/8 | 52 | 40 | 6 | 20 | 4/7 |
| NewContentBuilder | 4 | 11 | False | 3 | 11/11 | 67 | 57 | 5 | 20 | 3/7 |
| ComponentDetailBuilder | 4 | 5 | False | 5 | 2/5 | 66 | 61 | 2 | 36 | 2/2 |
| MineViewBuilder | 7 | 14 | False | 3 | 14/14 | 41 | 26 | 6 | 5 | 3/7 |
| ExplorationViewBuilder | 7 | 8 | False | 8 | 7/8 | 72 | 6 | 6 | 0 | 0/6 |
| MainPageBuilder (wearable) | 8 | 12 | False | 2 | 12/12 | 63 | 47 | 9 | 24 | 3/10 |

**Real, cheat-free resolve rate = 1/8 = 12.5%** (only the 2-file instance).

What the process metrics reveal that a binary score cannot:

1. **Difficulty rises sharply with file count** — the 2-file instance is solved; every
   4–8-file instance fails. "Can make progress, hard to finish." A binary 12.5% hides all
   of this.
2. **Partial credit is legible** — most failures show `symbol_redefinition = full`
   (8/8, 11/11, 12/12, 14/14) and are only **2–3 residual errors** away from passing
   ("almost there") vs. `ComponentDetail` at 2/5 with 5 residual errors ("barely
   started"). The scorecard separates them precisely.
3. **Discipline / efficiency form a gradient** — the solved run localizes before every
   edit (`4/4`); failures include blind editing (`Exploration 0/6`) and thrashing reads
   (`ComponentDetail 36`).

Raw trajectories and scorecards are in `data/eval_runs/claude-vertex-8inst/`.

## 1.7 What ships in `data/`

8 gate-verified multi-file instances (2–8 files, 3–14 deleted symbols each) across 5
modules (common / mine / exploration / componentlibrary / wearable) + `dataset.jsonl`.

---

# Part 2 · Step-by-step tutorial (hands-on)

## 2.0 Before you start — two choices that are *yours* to make

- **Agent scaffold is out of scope.** This benchmark evaluates *any* Code Agent; it does
  **not** prescribe one. The `eval_harness/` scripts drive
  [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) purely as a **worked
  example** of the solve → oracle loop. **Pick your own scaffold** (OpenHands, SWE-agent,
  your in-house harness, …). To plug it in, reuse the two contract points the example
  already uses: (a) set up the *broken* tree for an instance, and (b) grade the result
  with `harmony_bench.runner.type_check` (see §2.5).
- **Container engine — docker recommended.** The oracle runs the HarmonyOS toolchain
  inside a container. **Use `docker` if you can** — it is the smoother path. `udocker` is
  supported (and is the default) for rootless / no-daemon environments, but is **not
  required**. Select the engine with one env var:

  ```bash
  export HB_CONTAINER_ENGINE=docker    # or: udocker (default)
  ```

  Both use the same `run -v <mnt>:<mnt> <image> bash <script>` surface, so nothing else
  changes. With `udocker` also set `export UDOCKER_DIR=/tmp/udocker`.

## 2.1 Install

```bash
pip install -e .
```

Dependencies: `tree-sitter`, `json5`, `click`, `pydantic`, `pyyaml`, `anthropic`,
`openai` (see `pyproject.toml`).

## 2.2 Prepare the container image + toolchain

The oracle needs a container image (default name `hosbench`) that carries the HarmonyOS
Command Line Tools + JDK17, plus the toolchain mounted from the host. Point the runner at
your toolchain root (defaults to `<repo>/../harmony_toolchain`):

```
harmony_toolchain/
  jdk17/                 # Temurin 17
  command-line-tools/    # HarmonyOS CLT (hvigor)
  env.sh
```

Sanity-check the oracle in isolation (slow: builds a module once):

```bash
export HB_CONTAINER_ENGINE=docker          # or udocker + UDOCKER_DIR
python tests/test_runner_oracle.py         # asserts GREEN when a symbol is present, RED when deleted
```

## 2.3 Configure the authoring LLM (only for generating new instances)

Instance **authoring** (generating the issue + hypium test) uses an LLM. The shipped
`data/` was already generated, so you can **skip this** unless you want to build new
instances. Config lives in `config/default.yaml`; auth comes from the environment:

```bash
export ANTHROPIC_AUTH_TOKEN=...            # Bearer token for the Anthropic gateway
# ANTHROPIC_BASE_URL is read from the env if set; see config/default.yaml
```

## 2.4 Generate instances (optional — `data/` already ships 8)

```bash
export HB_CONTAINER_ENGINE=docker
export ANTHROPIC_AUTH_TOKEN=...

# generate + gate (real LLM + real oracle); toolchain expected at ../harmony_toolchain
python -m harmony_bench.cli gen   --repo-root .. --module common --num 5 --out data/instances
python -m harmony_bench.cli export --instances data/instances --output data/dataset.jsonl
python -m harmony_bench.cli stats  --instances data/instances

# batch the known multi-file candidates:
python eval_harness/gen_multi.py

# dev / CI wiring only (canned LLM, skip the oracle):
python -m harmony_bench.cli gen --repo-root .. --module common --num 3 --mock --out /tmp/out
```

Each generated instance is written only if it passes the **3-way gate** (§1.3).

## 2.5 Evaluate an agent on an instance

The example driver sets up the broken tree, runs the (example) scaffold under the
anti-cheat sandbox, then re-grades with the oracle. **Swap in your own scaffold here** —
the driver's structure shows exactly the two contract points.

```bash
# The example scaffold is mini-swe-agent, installed in its own venv:
export MSWEA_VENV_PYTHON=/path/to/venv-with-mini-swe-agent/bin/python
export HB_CONTAINER_ENGINE=docker
export ANTHROPIC_AUTH_TOKEN=...

# solve one instance + oracle re-grade (step limit 75):
python eval_harness/mini_drive.py data/instances/<inst>.json 75
```

The driver, during solving, **hides** `.git`, build caches, and the benchmark's
`data` / `_archive` dirs, so the agent must implement from the problem statement — it
cannot recover the golden.

## 2.6 Produce the 9-metric scorecard

```bash
python eval_harness/proc_eval.py data/instances/<inst>.json /tmp/mini_traj.json /tmp/agent_out
```

This emits the Axis A + Axis C scorecard (and a JSON line for programmatic use).

## 2.7 Run the whole batch

```bash
export MSWEA_VENV_PYTHON=/path/to/venv/bin/python
export ANTHROPIC_AUTH_TOKEN=...
export HB_CONTAINER_ENGINE=docker
bash eval_harness/batch_eval.sh            # serial solve + scorecard for every instance (VOIDs any cheat)
```

Outputs a `summary.tsv` and per-instance trajectories / scorecards.

## 2.8 Fast unit tests (no container)

```bash
python -m pytest tests/test_graph.py tests/test_carve_hollow.py   # 7 fast tests, no container
python tests/test_runner_oracle.py                                # slow: real oracle in a container
```

---

## Scope notes

- **Task level:** L2 (whole-symbol re-implementation from the interface).
- **Single-module:** cross-module features are rejected; runaway closures are capped by `max_files`.
- **Agent scaffold:** not prescribed — bring your own (§2.0).
- **Container engine:** docker recommended, udocker supported (§2.0).

