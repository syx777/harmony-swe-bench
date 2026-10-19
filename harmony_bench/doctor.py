"""Read-only WSL/Linux setup checks; runnable before dependencies are installed."""
import importlib.metadata
import os
import platform
import shutil
import sys
from pathlib import Path

from harmony_bench.paths import repo_root, toolchain_root


def main():
    failures = 0

    def check(ok, message):
        nonlocal failures
        print(f"{'OK' if ok else 'MISSING'}: {message}")
        if not ok:
            failures += 1

    check(sys.platform == "linux", "Linux Python (run this command inside WSL2)")
    print(f"INFO: {platform.release()} / {platform.machine()}")
    check(platform.machine().lower() in ("x86_64", "amd64"), "x86_64 host for this toolchain layout")
    check(sys.version_info >= (3, 10), "Python >= 3.10 (3.10-3.12 recommended for pinned tree-sitter)")
    for name in ("git", "bash", "timeout", os.environ.get("HB_CONTAINER_ENGINE", "udocker")):
        check(bool(shutil.which(name)), f"executable: {name}")
    for name in ("tree-sitter", "tree-sitter-typescript", "json5", "click", "pydantic", "pyyaml", "anthropic", "openai"):
        try:
            print(f"OK: {name} {importlib.metadata.version(name)}")
        except importlib.metadata.PackageNotFoundError:
            check(False, f"Python package: {name} (pip install -e .)")
    repo, tc = Path(repo_root()), Path(toolchain_root())
    check((repo / "hvigor" / "hvigor-config.json5").is_file(), f"HB_REPO_ROOT: {repo}")
    for rel in ("jdk17/bin/java", "command-line-tools/bin/hvigorw", "command-line-tools/tool/node/bin/node", "command-line-tools/sdk"):
        check((tc / rel).exists(), f"toolchain: {tc / rel}")
    for path in (repo, tc):
        if str(path).startswith("/mnt/"):
            print(f"NOTE: {path} is on a mounted filesystem; WSL's Linux filesystem is recommended for builds")
    script = Path(__file__).parent / "runner" / "hvigor_typecheck.sh"
    check(b"\r\n" not in script.read_bytes(), "oracle shell script has LF line endings")
    print(f"INFO: UDOCKER_DIR={os.environ.get('UDOCKER_DIR') or '~/.udocker'}")
    print(f"INFO: HB_CONTAINER={os.environ.get('HB_CONTAINER') or 'hosbench'} (existence not checked)")
    print("INFO: This checks host configuration only; run the container smoke test and real oracle in docs/wsl-udocker.md")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
