"""Carve a feature closure out of the symbol graph.

A *feature* is anchored at one exported symbol S (a class/function) and its forward
closure -- the same-module helpers S pulls in.  For the L2 (from-interface) task we
delete S (and any same-file helper that is private to the closure); the model must
re-implement it from the issue + the F2P test's type expectations.

Robustness note: over-carving is caught downstream -- the verify stage requires the
*gold* (original) tree to type-check GREEN, so a carve that deletes a still-needed
symbol simply fails the gate and is dropped, never emitted.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import List, Set, Tuple

import json5

from harmony_bench.graph import RepoIndex, SymbolGraph, forward_refs, symbol_source

Sym = Tuple[str, str]  # (name, abspath)


class CarveError(Exception):
    """Raised when a candidate cannot be carved into a clean, self-contained task."""


def module_of(index: RepoIndex, file_path: str) -> str:
    """oh-package name of the module that owns file_path (walk up to oh-package.json5)."""
    d = os.path.dirname(os.path.abspath(file_path))
    root = os.path.abspath(index.repo_root)
    while d and d.startswith(root):
        p = os.path.join(d, "oh-package.json5")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    return json5.load(fh).get("name") or ""
            except Exception:
                return ""
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


def _is_exported(index: RepoIndex, name: str, file_path: str) -> bool:
    pf = index.files.get(file_path)
    return bool(pf) and any(e.name == name and e.kind != "reexport" for e in pf.exports)


def _kind(index: RepoIndex, name: str, file_path: str) -> str:
    pf = index.files.get(file_path)
    if pf:
        for e in pf.exports:
            if e.name == name and e.kind != "reexport":
                return e.kind
        if any(f.name == name for f in pf.functions):
            return "function"
    return "symbol"


@dataclass
class CarveResult:
    anchor_name: str
    anchor_file: str          # abspath
    anchor_rel: str           # rel to repo_root
    module: str               # oh-package name (e.g. @ohos/common)
    kind: str                 # class | function | const
    closure: List[Sym]        # anchor + forward-reachable symbols
    extract: List[Sym]        # symbols to delete (may span several files)
    extract_files: List[str]  # repo-rel files touched by the deletion (>=1)
    keep_interface: List[str] # dependency names kept & available (issue context)
    external_ref_files: List[str]  # repo-rel files importing the anchor
    anchor_source: str        # the anchor's declaration text (for issue generation)


def _toplevel_syms(index: RepoIndex, f: str) -> Set[str]:
    pf = index.files.get(f)
    if pf is None:
        return set()
    ns = {fn.name for fn in pf.functions} | {s.name for s in pf.structs}
    ns |= {e.name for e in pf.exports if e.kind != "reexport"}
    if pf.default_export_name:
        ns.add(pf.default_export_name)
    return ns


def _symbol_referrers(index: RepoIndex, sg: SymbolGraph,
                      closure: Set[Sym]) -> dict:
    """For each closure member, the set of (symbol, file) that reference it -- computed
    at symbol granularity by inverting forward_refs over every file that might use a
    member (its definition file plus all files the reverse graph attributes to it)."""
    referrers = {m: set() for m in closure}
    scan_files: Set[str] = {f for (_n, f) in closure}
    for (n, f) in closure:
        scan_files |= set(sg.references_to_in(n, f))
    for g in scan_files:
        for s in _toplevel_syms(index, g):
            for t in forward_refs(index, sg, s, g):
                if t in referrers and (s, g) != t:
                    referrers[t].add((s, g))
    return referrers


def carve(index: RepoIndex, sg: SymbolGraph, anchor_name: str, anchor_file: str,
          max_members: int = 40, max_files: int = 8) -> CarveResult:
    """Carve the reference-closed feature owned by `anchor_name`.

    A closure member is *deleted* only when every symbol that references it is also
    being deleted (symbol-level predecessor closure grown from the anchor); shared
    code -- used by anything outside the deleted set -- stays as interface.  The
    deletion set may span several files -> a multi-file re-implementation task.

    Raises CarveError if the delete set would span more than `max_files` files (a
    runaway closure) or cross module boundaries (the single-module gate cannot verify
    cross-module integrity yet).
    """
    anchor_file = os.path.abspath(anchor_file)
    anchor: Sym = (anchor_name, anchor_file)
    seen: Set[Sym] = {anchor}
    queue: List[Sym] = [anchor]
    while queue and len(seen) < max_members:
        cur = queue.pop(0)
        for nxt in forward_refs(index, sg, cur[0], cur[1]):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)

    referrers = _symbol_referrers(index, sg, seen)
    # Grow the delete set from the anchor: add a member only when *all* of its
    # referrers are already being deleted (so no kept code will dangle).
    extract_set: Set[Sym] = {anchor}
    changed = True
    while changed:
        changed = False
        for m in seen:
            if m in extract_set:
                continue
            rs = referrers.get(m, set())
            if rs and rs <= extract_set:
                extract_set.add(m)
                changed = True

    extract: List[Sym] = [anchor] + sorted(extract_set - {anchor})
    keep = [nm for (nm, f) in sorted(seen) if (nm, f) not in extract_set and nm != anchor_name]

    extract_files = sorted({f for (_n, f) in extract})
    if len(extract_files) > max_files:
        raise CarveError(f"{anchor_name}: delete set spans {len(extract_files)} files "
                         f"(> max_files={max_files})")
    anchor_module = module_of(index, anchor_file)
    for f in extract_files:
        if module_of(index, f) != anchor_module:
            raise CarveError(f"{anchor_name}: delete set crosses module boundary "
                             f"({module_of(index, f)} != {anchor_module})")

    ext_files = [os.path.relpath(x, index.repo_root)
                 for x in sg.references_to_in(anchor_name, anchor_file)]
    pf = index.files[anchor_file]
    return CarveResult(
        anchor_name=anchor_name,
        anchor_file=anchor_file,
        anchor_rel=os.path.relpath(anchor_file, index.repo_root),
        module=anchor_module,
        kind=_kind(index, anchor_name, anchor_file),
        closure=sorted(seen),
        extract=extract,
        extract_files=[os.path.relpath(f, index.repo_root) for f in extract_files],
        keep_interface=sorted(set(keep)),
        external_ref_files=ext_files,
        anchor_source=symbol_source(pf, anchor_name),
    )
