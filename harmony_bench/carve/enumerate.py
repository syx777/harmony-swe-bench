"""Enumerate carve candidates: pure-logic exported symbols (no ArkUI) in a module,
ranked so the richest features surface first.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import List

from harmony_bench.graph import RepoIndex, SymbolGraph
from harmony_bench.carve.closure import module_of


@dataclass
class Candidate:
    name: str
    file: str          # abspath
    rel: str
    kind: str
    module: str
    fan_in: int
    impl_lines: int


def _is_ui_file(pf) -> bool:
    return any(st.is_component for st in pf.structs)


def _impl_lines(pf, name: str) -> int:
    from harmony_bench.graph import symbol_source
    src = symbol_source(pf, name)
    return src.count("\n") + 1 if src else 0


def enumerate_candidates(index: RepoIndex, sg: SymbolGraph, module_dir: str,
                         min_impl_lines: int = 8) -> List[Candidate]:
    """Exported class/function symbols in `module_dir`/src/main that are pure logic."""
    module_dir = os.path.abspath(module_dir)
    main_root = os.path.join(module_dir, "src", "main")
    out: List[Candidate] = []
    seen = set()
    for path, pf in index.files.items():
        if not path.startswith(main_root + os.sep):
            continue
        if _is_ui_file(pf):
            continue
        for exp in pf.exports:
            if exp.kind not in ("class", "function"):
                continue
            if (exp.name, path) in seen:
                continue
            seen.add((exp.name, path))
            il = _impl_lines(pf, exp.name)
            if il < min_impl_lines:
                continue
            out.append(Candidate(
                name=exp.name, file=path, rel=os.path.relpath(path, index.repo_root),
                kind=exp.kind, module=module_of(index, path),
                fan_in=sg.fan_in_in(exp.name, path), impl_lines=il))
    # richest first: more impl lines, then more cross-file impact
    out.sort(key=lambda c: (c.impl_lines, c.fan_in), reverse=True)
    return out
