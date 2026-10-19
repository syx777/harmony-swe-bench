"""Paths shared by the CLI, example drivers, and integration tests."""
import os
from pathlib import Path


BENCH_ROOT = Path(__file__).resolve().parent.parent


def repo_root():
    """Keep the original nested layout as a fallback; allow separate checkouts."""
    return str(Path(os.environ.get("HB_REPO_ROOT") or BENCH_ROOT.parent).expanduser().resolve())


def toolchain_root(repo=None):
    default = Path(repo or repo_root()).parent / "harmony_toolchain"
    return str(Path(os.environ.get("HB_TOOLCHAIN_ROOT") or default).expanduser().resolve())
