# WSL2 + udocker

Run the benchmark from Linux Python in an x86_64 WSL2 distribution with udocker.
Ubuntu 24.04 with Python 3.12 is a suitable starting environment. Native Windows
Python and WSL1 are not supported. HarmonyOS GREEN/RED validation requires the
external material repository, Linux toolchain and prepared container; these assets
are not distributed by this repository.

## 1. Install the Python environment in WSL

Run the following in Ubuntu, not PowerShell. Prefer checkouts under the Linux
filesystem (`~/hosbench`) for build performance and Linux executable permissions.
A Windows checkout can also be accessed through `/mnt/c/...` for editing.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv git curl ca-certificates
cd /path/to/harmony-swe-bench
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]' udocker
export UDOCKER_DIR="$HOME/.udocker"
udocker install
```

Use Python 3.10-3.12 for this setup. Do not reuse a Windows or Anaconda environment
inside WSL. After switching branches in an existing Windows checkout, check the
shell files use LF; `.gitattributes` enforces LF for future checkouts.

## 2. Prepare the external assets

Obtain a disposable checkout of the original `sample_in_harmonyos` material project
matching the dataset's `base_commit` (`be9d7da`). The benchmark repository's own
commits are unrelated to that revision. Place the Linux x86_64 HarmonyOS Command
Line Tools, matching SDK and Linux JDK17 at:

```text
~/hosbench/
  sample_in_harmonyos/       # common/, features/, products/, hvigor/
  harmony_toolchain/
    jdk17/bin/java
    command-line-tools/
      bin/hvigorw
      tool/node/bin/node
      sdk/
```

A Windows DevEco toolchain cannot substitute for these Linux executables.
Follow the toolchain's licenses and SDK setup instructions. This branch does not
download the SDK, invent the original source URL, or rebuild the missing `hosbench`
image automatically.

Load an existing Docker-save tar archive of the prepared Linux image without Docker
Desktop, then use the image reference printed by `udocker images`:

```bash
udocker load -i /path/to/hosbench-image.tar
udocker images
udocker create --name=hosbench IMAGE_REFERENCE_FROM_ABOVE
udocker setup --execmode=P1 hosbench
```

Alternatively obtain the prepared image from your image registry with
`udocker pull REGISTRY/IMAGE:TAG`. The original image needs Bash, glibc and the
toolchain's Linux system libraries. A generic Ubuntu image alone is not evidence
that the HarmonyOS build works. P1 is a starting mode; if PRoot fails under your
WSL kernel, try `udocker setup --execmode=P2 hosbench` and repeat the smoke test.
Avoid F2-F4 for this layout: udocker documents limitations on executing host-mounted
binaries in those modes. udocker is not a security boundary for untrusted agents.

## 3. Configure and check

```bash
source config/wsl.env.example
# Override HB_REPO_ROOT / HB_TOOLCHAIN_ROOT if your assets live elsewhere.
python -m harmony_bench.doctor

# Verify the actual container can execute the host-mounted Linux JDK and Node.
udocker run \
  -v "$HB_TOOLCHAIN_ROOT:$HB_TOOLCHAIN_ROOT" \
  --env="HB_TC=$HB_TOOLCHAIN_ROOT" \
  "$HB_CONTAINER" bash -c \
  '"$HB_TC/jdk17/bin/java" -version && "$HB_TC/command-line-tools/tool/node/bin/node" --version'

python -m pytest tests/test_runtime.py -q
python -m pytest tests/test_graph.py tests/test_carve_hollow.py -q
python -m pytest tests/test_runner_oracle.py -s
```

The last command temporarily edits the disposable source checkout, runs the real
toolchain twice, and restores those source/test files. The oracle enables
`execution.typeCheck` in the source project's hvigor config. A successful setup must
produce GREEN with the symbol present and RED after deletion. Host checks or a
container `echo` do not establish this.

The runner mounts the material checkout, toolchain and its own script separately,
so the benchmark may live outside the material checkout. Paths with spaces are
passed as separate subprocess arguments. `UDOCKER_DIR` is respected; the default
is persistent `~/.udocker`, not `/tmp/udocker`. No private proxy address is forced;
set `HB_PROXY` to a reachable proxy or `""` to disable it. WSL proxy addresses must
be reachable from Linux, which may differ from Windows localhost.

## 4. Generate or evaluate

```bash
python -m harmony_bench.cli stats
# New tasks need the configured authoring API credentials.
python -m harmony_bench.cli gen --module common --num 1

# Evaluation also requires mini-swe-agent in its own Linux venv and credentials.
export MSWEA_VENV_PYTHON=/path/to/mini-swe-agent-venv/bin/python
export ANTHROPIC_AUTH_TOKEN=YOUR_TOKEN
python -m eval_harness.mini_drive data/instances/sample_in_harmonyos__new_feature-6ba8f485.json 75
bash eval_harness/batch_eval.sh
```

The example driver uses a local agent, resets task files with Git, and temporarily
moves the benchmark's answer directories. Use disposable working copies and run
one evaluation at a time. If the benchmark is nested inside the material checkout,
keep both on the same filesystem for these renames. Process scoring remains the
existing heuristic implementation; this branch does not revalidate old results.

References: [udocker installation](https://github.com/indigo-dc/udocker/blob/master/docs/installation_manual.md),
[udocker runtime modes](https://github.com/indigo-dc/udocker/blob/master/docs/user_manual.md),
[WSL filesystem guidance](https://learn.microsoft.com/en-us/windows/wsl/filesystems).
