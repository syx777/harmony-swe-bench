from __future__ import annotations
from typing import Dict, List
import os
import pydantic
from harmony_bench.parser.ets_parser import parse_file, ParsedFile


class RepoIndex(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)
    repo_root: str
    files: Dict[str, ParsedFile] = {}
    module_aliases: Dict[str, str] = {}


_SKIP_DIRS = ("node_modules", ".git", "oh_modules", "build",
              ".test", ".hvigor", ".cache", ".preview")


def _iter_source_files(root: str, subdirs: List[str]):
    for sub in subdirs:
        base = os.path.join(root, sub)
        for dirpath, dirs, names in os.walk(base):
            # prune build/output/dependency dirs in-place so we never index generated
            # copies of source (e.g. common/.test/.../esmodule/debug/**/*.ts).
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for n in names:
                if n.endswith(".ets") or n.endswith(".ts"):
                    yield os.path.join(dirpath, n)


def _read_oh_package_name(dir_path: str):
    import json5
    p = os.path.join(dir_path, "oh-package.json5")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json5.load(fh)
            return data.get("name")
        except Exception:
            return None
    return None


def build_repo_index(repo_root: str, subdirs: List[str]) -> RepoIndex:
    idx = RepoIndex(repo_root=repo_root)
    for path in _iter_source_files(repo_root, subdirs):
        try:
            idx.files[os.path.abspath(path)] = parse_file(path)
        except Exception:
            continue
    # Map oh-package names → that module's Index.ets barrel.
    for dirpath, dirs, names in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        if "oh-package.json5" in names:
            name = _read_oh_package_name(dirpath)
            index_ets = os.path.join(dirpath, "Index.ets")
            if name and os.path.exists(index_ets):
                idx.module_aliases[name] = os.path.abspath(index_ets)
    return idx
