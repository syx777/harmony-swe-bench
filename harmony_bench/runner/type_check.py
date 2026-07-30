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
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Optional

_SCRIPT = os.path.join(os.path.dirname(__file__), "hvigor_typecheck.sh")
_DEFAULT_PROXY = os.environ.get("http_proxy") or "http://10.217.142.137:8080"


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
               container: str = "hosbench",
               mount_root: Optional[str] = None,
               udocker_dir: str = "/tmp/udocker",
               proxy: Optional[str] = _DEFAULT_PROXY,
               timeout: int = 240) -> TypeCheckResult:
    """Judge the current on-disk state of `module` as GREEN / RED.

    Runs `hvigor_typecheck.sh` inside `container` via the configured engine
    (`HB_CONTAINER_ENGINE`, default `udocker`). `mount_root` must be a common ancestor
    of both `repo_root` and `toolchain_root` (defaults to repo parent); it is
    bind-mounted into the container at the same path so absolute paths line up.
    """
    repo_root = os.path.abspath(repo_root)
    toolchain_root = os.path.abspath(toolchain_root)
    if mount_root is None:
        mount_root = os.path.dirname(repo_root)
    mount_root = os.path.abspath(mount_root)

    ensure_typecheck(repo_root)

    engine = _engine()
    env = os.environ.copy()
    env["UDOCKER_DIR"] = udocker_dir
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
    cmd += ["-v", f"{mount_root}:{mount_root}", container, "bash", _SCRIPT]

    # generous outer timeout: inner script self-limits to HB_TIMEOUT, add slack for
    # container startup + first-run pnpm/hvigor plugin bootstrap.
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              timeout=timeout + 300)
    except subprocess.TimeoutExpired as exc:
        return TypeCheckResult(Verdict.ERROR, "outer timeout", (exc.stdout or "") if isinstance(exc.stdout, str) else "")

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
