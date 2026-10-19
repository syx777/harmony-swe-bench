# harmony-swe-bench

**Windows / WSL2:** see [WSL2 + udocker setup](docs/wsl-udocker.md). The runner
supports separate source/toolchain checkouts through `HB_REPO_ROOT` and
`HB_TOOLCHAIN_ROOT`; `python -m harmony_bench.doctor` checks the host setup.

A multi-file `new_feature` benchmark for HarmonyOS Code Agents, plus a process-level
evaluation of *how* an agent solves each task.

There are two deliverables:

1. Benchmark construction, in `harmony_bench/`. It carves a real, cohesive feature out of
   a HarmonyOS project, verifies the carve with an execution-backed oracle, and emits
   SWE-bench-style instances. 8 gate-verified multi-file instances ship in `data/`.
2. Process evaluation, in `eval_harness/`. It runs an agent on an instance and scores the
   resulting trajectory with 9 fine-grained metrics, described in §1.4, rather than a
   single resolved / not-resolved bit.

---

# Part 1. Highlights

## 1.1 The idea: carve a feature, ask the agent to rebuild it

The HarmonyOS repo we work from, `sample_in_harmonyos`, has no PRs, no issues and no
commit trail, so the classic SWE-bench recipe of turning a PR into a task does not apply.
We take a different route: every existing, working feature is already a golden answer, so
we carve it out of the codebase along the dependency graph. Each instance then

- deletes one cohesive feature, meaning an anchor ArkTS symbol plus its private closure,
  which may span several files, and
- asks the agent to re-implement that feature from an issue and a runnable `@ohos/hypium`
  test.

We call this an L2 "from-interface" task. The agent is handed the interface contract and
has to write the feature from scratch.

## 1.2 Reference-closed multi-file carving

A feature is a connected subgraph of the dependency graph, not a single function. `carve/`
grows the delete set outward from the anchor and pulls in a helper only when every symbol
that references it is also being deleted, which is a fixpoint over symbol-level
predecessors. Anything still used from outside the delete set stays behind as interface.
That property is what keeps the multi-file deletion clean: shared code is never carved
away.

The pipeline:

```
enumerate → carve → hollow → gate(3-way) → author → build → export
```

| module | role |
|---|---|
| `parser/` | tree-sitter ArkTS parser. Handles funcs, classes, enums and interfaces, default and `export *` / `export {default as}` barrels, and `@Component` |
| `graph/` | import graph, symbol graph with fan-in, and closure edges in `forward.py` |
| `carve/` | enumerates anchors and computes the symbol-level reference-closed multi-file closure, single-module, capped by `max_files` |
| `hollow/` | L2 multi-file removal. Deletes symbol spans across files, strips imports that just went dangling, swallows ArkUI decorators and drops matching `routerMap` entries. The golden answer is the original on disk |
| `author/` | generates the issue from the golden symbol's contract, and the hypium F2P test, via an LLM |
| `runner/` | execution-backed oracle. Runs `hvigorw test` in a container and reads GREEN / RED off the `UnitTestArkTS` build markers |
| `verify/` | the 3-way gate, P2P plus F2P plus gold, all on the real toolchain |
| `build.py`, `schema.py`, `cli.py` | assemble and emit SWE-bench-style instances |

## 1.3 The execution-backed oracle and the 3-way integrity gate

The oracle drives the real HarmonyOS ArkTS toolchain, `hvigorw test` with strict type
checking, headless inside a container, and reads the build's `UnitTestArkTS` markers.
GREEN means the feature resolves and its interface contract holds; RED means the feature
is absent or the contract is violated.

An instance is only kept if all three of these checks hold. Together they are what
guarantees the multi-file deletion is clean and the task is genuinely solvable.

1. Broken tree plus the stock test must go GREEN, so the rest of the module still builds
   and no dangling references were left behind.
2. Broken tree plus the F2P test must go RED, so the feature really is gone and the test
   can no longer resolve it.
3. Golden tree plus the F2P test must go GREEN, so restoring the original brings the
   module back.

## 1.4 Process-level evaluation with 9 metrics

A binary resolved / not-resolved score throws away almost everything about how the agent
worked. We score the trajectory along two axes instead. Each metric's direction is listed
below, and `eval_harness/proc_eval.py` is the reference implementation.

Axis A, progress, which is where partial credit comes from:

- `residual_arkts_errors`, the compiler errors still left after the agent submits
- `symbol_redefinition_rate`, the fraction of required symbols the agent re-declared

Axis C, process, meaning efficiency and discipline, extracted purely from the trace:

- `steps`, `explore_steps`, `edit_steps`, `verify_steps`
- `redundant_reads`, `edit_thrashing`
- `localize_before_edit`, i.e. read-before-write discipline
- `consecutive_dup_cmds`, a stuck-in-a-loop detector
- `anti_hack_probes`, attempts to recover the answer from git, test dirs, or the
  benchmark's own data

## 1.5 The anti-cheat sandbox

The biggest risk in a carve-and-rebuild benchmark is that the agent recovers the answer
from a reference instead of solving the task. Two concrete holes showed up during
evaluation, and both are now closed:

- Git history. An agent ran `git show <sha>:<file>` to read the golden code and copy it.
- The benchmark's own data. `data/instances/*.json` carry the gold `patch`, and an agent
  read one directly.

While the agent is solving, the eval driver now hides every source of the answer: `.git`
history, build caches, and the benchmark's `data` and `_archive` dirs, which are moved
outside the repo tree. The `anti_hack_probes` metric records any attempt, and the batch
runner voids any run that reaches an answer file, as a safety net on top of the sandbox.

## 1.6 Results

We ran mini-swe-agent with Claude Opus 4.8, a step limit of 75, serially, with the
anti-cheat sandbox on, over all 8 instances:

| instance (anchor) | files | del syms | RESOLVED | resid. err | sym. redef | steps | explore | edit | redund. read | localize |
|---|---|---|---|---|---|---|---|---|---|---|
| SourceCodeLinkPageBuilder | 2 | 3 | ✅ True | 0 | 3/3 | 35 | 27 | 4 | 6 | 4/4 |
| MainPageBuilder (pc) | 2 | 3 | False | 3 | 3/3 | 41 | 34 | 2 | 13 | 2/2 |
| AboutViewBuilder | 4 | 8 | False | 3 | 8/8 | 52 | 40 | 6 | 20 | 4/7 |
| NewContentBuilder | 4 | 11 | False | 3 | 11/11 | 67 | 57 | 5 | 20 | 3/7 |
| ComponentDetailBuilder | 4 | 5 | False | 5 | 2/5 | 66 | 61 | 2 | 36 | 2/2 |
| MineViewBuilder | 7 | 14 | False | 3 | 14/14 | 41 | 26 | 6 | 5 | 3/7 |
| ExplorationViewBuilder | 7 | 8 | False | 8 | 7/8 | 72 | 6 | 6 | 0 | 0/6 |
| MainPageBuilder (wearable) | 8 | 12 | False | 2 | 12/12 | 63 | 47 | 9 | 24 | 3/10 |

The real, cheat-free resolve rate is 1/8, or 12.5%, and the one success is a 2-file
instance.

Three things the process metrics show that a binary score would hide:

1. Difficulty rises sharply with file count. The 2-file instance is solved and every
   instance spanning 4 to 8 files fails. Agents can make progress but struggle to finish,
   and a flat 12.5% says none of that.
2. Partial credit becomes legible. Most failures redefine every required symbol, 8/8,
   11/11, 12/12, 14/14, and sit only 2 or 3 residual errors short of passing, so they are
   almost there. `ComponentDetail`, at 2/5 with 5 residual errors, barely started. The
   scorecard separates the two cases cleanly.
3. Discipline and efficiency form a gradient. The successful run localized before every
   edit, 4/4, while failures range from blind editing, `Exploration` at 0/6, to read
   thrashing, `ComponentDetail` at 36.

Raw trajectories and scorecards live in `data/eval_runs/claude-vertex-8inst/`.

## 1.7 What ships in `data/`

8 gate-verified multi-file instances spanning 2 to 8 files and 3 to 14 deleted symbols
each, drawn from 5 modules: common, mine, exploration, componentlibrary and wearable.
`dataset.jsonl` is included too.

---

# Part 2. Step-by-step tutorial

## 2.0 Before you start: two choices are yours to make

The agent scaffold is out of scope. This benchmark evaluates any Code Agent and does not
prescribe one. The `eval_harness/` scripts drive
[mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) purely as a worked example
of the solve-then-oracle loop. Pick whatever scaffold you like, be it OpenHands,
SWE-agent or your own in-house harness. To plug it in, reuse the same two contract points
the example uses: set up the broken tree for an instance, then grade the result with
`harmony_bench.runner.type_check`. See §2.5.

For the container engine, docker is recommended. The oracle runs the HarmonyOS toolchain
inside a container, and docker is the smoother path if it is available to you. `udocker`
is supported and is in fact the default, for rootless or no-daemon environments, but it is
not required. One env var selects the engine:

```bash
export HB_CONTAINER_ENGINE=docker    # or: udocker (default)
```

Both engines expose the same `run -v <mnt>:<mnt> <image> bash <script>` surface, so
nothing else changes. With `udocker`, use a persistent image store:
`export UDOCKER_DIR="$HOME/.udocker"` (or your existing udocker repository).

## 2.1 Install

```bash
pip install -e .
```

Dependencies are `tree-sitter`, `json5`, `click`, `pydantic`, `pyyaml`, `anthropic` and
`openai`; see `pyproject.toml`.

## 2.2 Prepare the container image and toolchain

The oracle needs a container image, named `hosbench` by default, carrying the HarmonyOS
Command Line Tools and JDK17, with the toolchain mounted from the host. Point the runner
at your toolchain root, which defaults to `<repo>/../harmony_toolchain`:

```
harmony_toolchain/
  jdk17/                 # Temurin 17
  command-line-tools/    # HarmonyOS CLT (hvigor)
  env.sh
```

You can sanity-check the oracle on its own. It is slow, since it builds a module once:

```bash
export HB_CONTAINER_ENGINE=docker          # or udocker + UDOCKER_DIR
python tests/test_runner_oracle.py         # asserts GREEN when a symbol is present, RED when deleted
```

## 2.3 Configure the authoring LLM

Authoring an instance, which means generating the issue and the hypium test, uses an LLM.
The `data/` directory was already generated, so you can skip this step unless you want to
build new instances. Config lives in `config/default.yaml` and auth comes from the
environment:

```bash
export ANTHROPIC_AUTH_TOKEN=...            # Bearer token for the Anthropic gateway
# ANTHROPIC_BASE_URL is read from the env if set; see config/default.yaml
```

## 2.4 Generate instances

This step is optional, since `data/` already ships 8 instances.

```bash
export HB_CONTAINER_ENGINE=docker
export ANTHROPIC_AUTH_TOKEN=...

# generate + gate with the real LLM and real oracle; toolchain expected at ../harmony_toolchain
python -m harmony_bench.cli gen   --repo-root .. --module common --num 5 --out data/instances
python -m harmony_bench.cli export --instances data/instances --output data/dataset.jsonl
python -m harmony_bench.cli stats  --instances data/instances

# batch the known multi-file candidates:
python eval_harness/gen_multi.py

# dev / CI wiring only, with a canned LLM and no oracle:
python -m harmony_bench.cli gen --repo-root .. --module common --num 3 --mock --out /tmp/out
```

A generated instance is only written out if it passes the 3-way gate from §1.3.

## 2.5 Evaluate an agent on an instance

The example driver sets up the broken tree, runs the example scaffold under the anti-cheat
sandbox, then re-grades with the oracle. Swap in your own scaffold here; the driver's
structure shows exactly where the two contract points are.

```bash
# The example scaffold is mini-swe-agent, installed in its own venv:
export MSWEA_VENV_PYTHON=/path/to/venv-with-mini-swe-agent/bin/python
export HB_CONTAINER_ENGINE=docker
export ANTHROPIC_AUTH_TOKEN=...

# solve one instance + oracle re-grade, step limit 75:
python eval_harness/mini_drive.py data/instances/<inst>.json 75
```

While solving, the driver hides `.git`, build caches and the benchmark's `data` and
`_archive` dirs, so the agent has to implement from the problem statement and cannot
recover the golden code.

## 2.6 Produce the 9-metric scorecard

```bash
python eval_harness/proc_eval.py data/instances/<inst>.json /tmp/mini_traj.json /tmp/agent_out
```

This prints the Axis A and Axis C scorecard, along with a JSON line for programmatic use.

## 2.7 Run the whole batch

```bash
export MSWEA_VENV_PYTHON=/path/to/venv/bin/python
export ANTHROPIC_AUTH_TOKEN=...
export HB_CONTAINER_ENGINE=docker
bash eval_harness/batch_eval.sh            # serial solve + scorecard for every instance, VOIDs any cheat
```

You get a `summary.tsv` plus per-instance trajectories and scorecards.

## 2.8 Fast unit tests, no container needed

```bash
python -m pytest tests/test_graph.py tests/test_carve_hollow.py   # 7 fast tests, no container
python tests/test_runner_oracle.py                                # slow: real oracle in a container
```

---

## Scope notes

- Task level is L2: re-implement a whole symbol from its interface.
- Single-module only. Cross-module features are rejected, and runaway closures are capped
  by `max_files`.
- The agent scaffold is not prescribed, so bring your own. See §2.0.
- Docker is recommended for the container engine, and udocker is supported. See §2.0.
