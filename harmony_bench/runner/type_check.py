"""Compile-level oracle: judge a HarmonyOS module's working-tree state as GREEN
(the target feature resolves and its interface/type contract is satisfied) or RED
(the feature is absent or its contract is violated), by driving the real HarmonyOS
ArkTS toolchain (`hvigorw test` with strict type checking) headless inside a container.

This is the execution-backed integrity oracle: it runs the module's actual build and
UnitTestArkTS compile stage, so a deleted feature (or a wrong signature) fails and a
correct re-implementation passes.  Fast and reproducible; needs no display/GL.

Container engine: `docker` or `udocker`, selected via the `HB_CONTAINER_ENGINE` env var
(default `udocker`).  Both expose the same `run -v <mnt>:<mnt> <image> bash <script>`
surface, so the oracle is engine-agnostic; docker is recommended where available.
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional

_SCRIPT = os.path.join(os.path.dirname(__file__), "hvigor_typecheck.sh")


def _engine() -> str:
    """Container engine to drive the oracle: `docker` or `udocker`.

    Both share the same `run -v host:host image bash script` invocation surface, so the
    oracle body is identical either way.  Set `HB_CONTAINER_ENGINE=docker` to use docker.
    """
    return os.environ.get("HB_CONTAINER_ENGINE", "udocker").strip() or "udocker"


class Verdict(str, Enum):
    GREEN = "GREEN"   # UnitTestArkTS compiled: target feature resolves & its contract holds
    RED = "RED"       # compile/type error: feature absent or signature/contract mismatch
    ERROR = "ERROR"   # environment/setup failure (not a valid judgement)


@dataclass
class TypeCheckResult:
    verdict: Verdict
    log_tail: str
    stdout: str

    @property
    def ok(self) -> bool:
        return self.verdict is not Verdict.ERROR


def ensure_typecheck(repo_root: str) -> None:
    """Idempotently ACTIVATE execution.typeCheck:true in hvigor/hvigor-config.json5.

    ArkTS type checking is OFF by default, which makes unresolved imports compile
    cleanly -- turning it on is what gives the oracle its discriminating power (deleting
    one feature from a still-valid module must turn the build RED).  The stock config
    ships the option only as a *comment* (`// "typeCheck": ...`), so we strip any
    commented/active typeCheck line and inject a real active one.
    """
    cfg = os.path.join(repo_root, "hvigor", "hvigor-config.json5")
    with open(cfg, "r", encoding="utf-8") as fh:
        s = fh.read()
    # already active (uncommented) ?
    if re.search(r'^\s*"typeCheck"\s*:\s*true', s, re.MULTILINE):
        return
    # drop any existing typeCheck line (commented or not)
    s = re.sub(r'^[ \t]*//?[ \t]*"typeCheck"[^\n]*\n', '', s, flags=re.MULTILINE)
    # inject an active option as the first key of the execution block
    s = re.sub(r'("execution"\s*:\s*\{)',
               r'\1\n    "typeCheck": true,', s, count=1)
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write(s)


def type_check(module: str,
               repo_root: str,
               toolchain_root: str,
               container: Optional[str] = None,
               mount_root: Optional[str] = None,
               udocker_dir: Optional[str] = None,
               proxy: Optional[str] = None,
               timeout: int = 240) -> TypeCheckResult:
    """Judge the current on-disk state of `module` as GREEN / RED.

    Runs `hvigor_typecheck.sh` inside `container` via the configured engine
    (`HB_CONTAINER_ENGINE`, default `udocker`). By default the source, toolchain,
    and runner directories are mounted separately at their original Linux paths.
    An explicit `mount_root` must contain both source and toolchain; the runner
    is still mounted separately if necessary. An empty proxy string disables proxying.
    """
    repo_root = os.path.realpath(os.path.expanduser(repo_root))
    toolchain_root = os.path.realpath(os.path.expanduser(toolchain_root))
    engine = _engine()
    if engine not in ("udocker", "docker"):
        return TypeCheckResult(Verdict.ERROR, f"Unsupported HB_CONTAINER_ENGINE: {engine}", "")
    if not shutil.which(engine):
        return TypeCheckResult(Verdict.ERROR, f"{engine} not found in PATH; run inside WSL with your Linux venv activated", "")
    for path in (repo_root, toolchain_root):
        if not os.path.isdir(path):
            return TypeCheckResult(Verdict.ERROR, f"Directory not found: {path}", "")
    script_dir = os.path.dirname(os.path.realpath(_SCRIPT))
    if mount_root is not None:
        mount_root = os.path.realpath(os.path.expanduser(mount_root))
        if any(os.path.commonpath([mount_root, p]) != mount_root
               for p in (repo_root, toolchain_root)):
            return TypeCheckResult(Verdict.ERROR, "mount_root must contain source and toolchain", "")
        mounts = [mount_root]
    else:
        mounts = list(dict.fromkeys([repo_root, toolchain_root]))
    if not any(os.path.commonpath([p, script_dir]) == p for p in mounts):
        mounts.append(script_dir)
    try:
        ensure_typecheck(repo_root)
    except (OSError, ValueError) as exc:
        return TypeCheckResult(Verdict.ERROR, f"Cannot configure ArkTS type checking: {exc}", "")

    container = container or os.environ.get("HB_CONTAINER", "hosbench")
    if proxy is None:
        proxy = os.environ.get("HB_PROXY", os.environ.get("http_proxy", os.environ.get("https_proxy", "")))
    env = os.environ.copy()
    env["UDOCKER_DIR"] = os.path.abspath(os.path.expanduser(
        udocker_dir or env.get("UDOCKER_DIR") or "~/.udocker"))
    cmd = [
        engine, "run",
        f"--env=HB_TC={toolchain_root}",
        f"--env=HB_REPO={repo_root}",
        f"--env=HB_MODULE={module}",
        f"--env=HB_TIMEOUT={timeout}",
    ]
    if proxy:
        cmd.append(f"--env=HB_PROXY={proxy}")
    if engine == "docker":
        cmd.append("--rm")
    for path in mounts:
        cmd += ["-v", f"{path}:{path}"]
    cmd += [container, "bash", os.path.join(script_dir, os.path.basename(_SCRIPT))]

    # generous outer timeout: inner script self-limits to HB_TIMEOUT, add slack for
    # container startup + first-run pnpm/hvigor plugin bootstrap.
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=timeout + 300)
    except subprocess.TimeoutExpired as exc:
        return TypeCheckResult(Verdict.ERROR, "outer timeout", (exc.stdout or "") if isinstance(exc.stdout, str) else "")
    except OSError as exc:
        return TypeCheckResult(Verdict.ERROR, f"Cannot start {engine}: {exc}", "")

    out = proc.stdout or ""
    m = re.search(r"HB_VERDICT=(\d)", out)
    tail = ""
    tm = re.search(r"HB_LOG_TAIL_BEGIN\n(.*?)\nHB_LOG_TAIL_END", out, re.DOTALL)
    if tm:
        tail = tm.group(1).strip()
    if not m:
        return TypeCheckResult(Verdict.ERROR, tail or out[-2000:], out)
    verdict = {0: Verdict.GREEN, 1: Verdict.RED}.get(int(m.group(1)), Verdict.ERROR)
    return TypeCheckResult(verdict, tail, out)
