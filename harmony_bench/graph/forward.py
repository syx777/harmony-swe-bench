"""Forward dependency edges: for a defined symbol, which *other* known symbols does
its declaration/body reference?  The legacy SymbolGraph only records reverse edges
(who imports a name); carve needs the forward direction to walk a feature's closure
(anchor -> the private helpers it pulls in).

Heuristic but adequate: extract the symbol's source span, tokenise identifiers, and
keep those that resolve to a known definition -- either a same-file top-level symbol
or an imported name resolved through the module graph.
"""
from __future__ import annotations
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from harmony_bench.graph.import_graph import RepoIndex
from harmony_bench.graph.symbol_graph import SymbolGraph, resolve_module
from harmony_bench.parser.ets_parser import ParsedFile

_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")

Sym = Tuple[str, str]  # (symbol name, defining file abspath)


def local_symbol_names(pf: ParsedFile) -> Set[str]:
    """All top-level symbol names the parser can see in a file."""
    names: Set[str] = set()
    for fn in pf.functions:
        names.add(fn.name)
    for st in pf.structs:
        names.add(st.name)
    for exp in pf.exports:
        if exp.kind != "reexport":
            names.add(exp.name)
    return names


def symbol_span(pf: ParsedFile, name: str) -> Optional[Tuple[int, int]]:
    """Byte span of a symbol's declaration (preferred) or body, or None."""
    for exp in pf.exports:
        if exp.name == name and exp.kind != "reexport":
            return (exp.start_byte, exp.end_byte)
    for fn in pf.functions:
        if fn.name == name:
            return (fn.body_start_byte, fn.body_end_byte)
    for st in pf.structs:
        if st.name == name:
            if st.decl_end_byte > st.decl_start_byte:
                return (st.decl_start_byte, st.decl_end_byte)
            if st.build_body_start_byte is not None:
                return (st.build_body_start_byte, st.build_body_end_byte)
    return None


def symbol_source(pf: ParsedFile, name: str) -> str:
    span = symbol_span(pf, name)
    if span is None:
        return ""
    raw = pf.source.encode("utf-8")
    return raw[span[0]:span[1]].decode("utf-8", errors="ignore")


def _imported_name_targets(pf: ParsedFile, index: RepoIndex) -> Dict[str, List[str]]:
    """Map each imported identifier -> resolved defining file(s) in the index."""
    out: Dict[str, List[str]] = {}
    for imp in pf.imports:
        targets = resolve_module(pf.path, imp.module, index)
        if not targets:
            continue
        for nm in imp.names:
            out.setdefault(nm, [])
            for t in targets:
                if t not in out[nm]:
                    out[nm].append(t)
    return out


def forward_refs(index: RepoIndex, sg: SymbolGraph, name: str, defining_file: str) -> Set[Sym]:
    """Symbols referenced from within `name`'s span in `defining_file`.

    Resolves each referenced identifier to a concrete (name, file):
      * same-file top-level symbol -> (id, defining_file)
      * imported name             -> (id, resolved target file that defines it,
                                        following barrels to a concrete provider)
    Self-references and unresolved identifiers are dropped.
    """
    pf = index.files.get(defining_file)
    if pf is None:
        return set()
    body = symbol_source(pf, name)
    if not body:
        return set()
    idents = set(_IDENT.findall(body)) - {name}
    local = local_symbol_names(pf)
    imported = _imported_name_targets(pf, index)

    out: Set[Sym] = set()
    for ident in idents:
        if ident in local:
            out.add((ident, defining_file))
            continue
        if ident in imported:
            for target in imported[ident]:
                # target may be a barrel; map to concrete provider(s) under its dir
                providers = [f for f in sg.defining_files(ident)]
                if target in providers:
                    out.add((ident, target))
                elif providers:
                    bdir = os.path.dirname(target)
                    for p in providers:
                        if p == bdir or p.startswith(bdir + os.sep):
                            out.add((ident, p))
                else:
                    out.add((ident, target))
    return out
