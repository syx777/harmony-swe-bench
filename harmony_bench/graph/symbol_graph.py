from __future__ import annotations
from typing import Dict, List, Optional, Set, Tuple
import os
from harmony_bench.graph.import_graph import RepoIndex


def resolve_module(referencing_file: str, module_spec: str, index: RepoIndex) -> List[str]:
    """Resolve an import module spec from referencing_file to concrete target file
    paths that exist in the index. Returns [] for external (e.g. @kit.*) specs."""
    if module_spec.startswith("."):
        base = os.path.normpath(os.path.join(os.path.dirname(referencing_file), module_spec))
        out: List[str] = []
        for cand in (base + ".ets", base + ".ts",
                     os.path.join(base, "Index.ets"), os.path.join(base, "Index.ts"),
                     os.path.join(base, "index.ets"), os.path.join(base, "index.ts")):
            ap = os.path.abspath(cand)
            if ap in index.files:
                out.append(ap)
        return out
    if module_spec in index.module_aliases:
        return [index.module_aliases[module_spec]]
    return []


class SymbolGraph:
    def __init__(self) -> None:
        # symbol name -> ordered list of concrete defining files
        self._defs: Dict[str, List[str]] = {}
        # (symbol name, defining file) -> set of referencing files
        self._refs: Dict[Tuple[str, str], Set[str]] = {}

    def add_def(self, symbol: str, file: str) -> None:
        files = self._defs.setdefault(symbol, [])
        if file not in files:
            files.append(file)

    def add_ref(self, symbol: str, defining_file: str, referencing_file: str) -> None:
        self._refs.setdefault((symbol, defining_file), set()).add(referencing_file)

    def defining_file(self, symbol_name: str) -> Optional[str]:
        files = self._defs.get(symbol_name)
        return files[0] if files else None

    def defining_files(self, symbol_name: str) -> List[str]:
        return list(self._defs.get(symbol_name, []))

    def references_to(self, symbol_name: str) -> List[str]:
        """Aggregate referencing files across all defining files of this name."""
        acc: Set[str] = set()
        for (nm, _df), refs in self._refs.items():
            if nm == symbol_name:
                acc |= refs
        return sorted(acc)

    def fan_in(self, symbol_name: str) -> int:
        return len(self.references_to(symbol_name))

    def references_to_in(self, symbol_name: str, defining_file: str) -> List[str]:
        return sorted(self._refs.get((symbol_name, defining_file), set()))

    def fan_in_in(self, symbol_name: str, defining_file: str) -> int:
        return len(self._refs.get((symbol_name, defining_file), set()))


def build_symbol_graph(index: RepoIndex) -> SymbolGraph:
    sg = SymbolGraph()

    # 1. Concrete defs: functions, structs, non-reexport named exports, and the
    #    file's default export (named by its canonical name, e.g. Logger).
    concrete_defs: Dict[str, Set[str]] = {}
    for path, pf in index.files.items():
        names: Set[str] = set()
        for fn in pf.functions:
            names.add(fn.name)
        for st in pf.structs:
            names.add(st.name)
        for exp in pf.exports:
            if exp.kind != "reexport":
                names.add(exp.name)
        if pf.default_export_name:
            names.add(pf.default_export_name)
        for nm in names:
            concrete_defs.setdefault(nm, set()).add(path)
            sg.add_def(nm, path)

    # 2. Resolve which concrete (name, file) providers satisfy importing `name` from a
    #    module file, following `export *` and `export { a as b } from` barrels
    #    transitively.  `want_default` follows the default-export chain instead.
    def providers(target: str, name: str, want_default: bool,
                  seen: Set[Tuple[str, str, bool]]) -> List[Tuple[str, str]]:
        key = (target, name, want_default)
        if key in seen:
            return []
        seen.add(key)
        pf = index.files.get(target)
        if pf is None:
            return []
        out: List[Tuple[str, str]] = []
        if want_default:
            if pf.default_export_name:
                out.append((pf.default_export_name, target))
            for r in pf.named_reexports:                 # export { X as default }
                if r.name == "default":
                    for sub in resolve_module(target, r.module, index):
                        out += providers(sub, r.orig, r.is_default, seen)
            return out
        if target in concrete_defs.get(name, set()):
            out.append((name, target))
        for r in pf.named_reexports:                     # export { orig as name } from mod
            if r.name == name:
                for sub in resolve_module(target, r.module, index):
                    out += providers(sub, r.orig, r.is_default, seen)
        for mod in pf.star_reexports:                    # export * from mod
            for sub in resolve_module(target, mod, index):
                out += providers(sub, name, False, seen)
        return out

    # 3. Reference attribution through resolved modules (named + default imports).
    for path, pf in index.files.items():
        for imp in pf.imports:
            targets = resolve_module(path, imp.module, index)
            if not targets:
                continue
            for target in targets:
                for name in imp.names:
                    for (cn, cf) in providers(target, name, False, set()):
                        sg.add_ref(cn, cf, path)
                if imp.default_name:
                    for (cn, cf) in providers(target, imp.default_name, True, set()):
                        sg.add_ref(cn, cf, path)
    return sg
