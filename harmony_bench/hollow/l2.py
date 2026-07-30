"""L2 hollow: delete the carved feature (anchor + closure-private helpers, possibly
across several files) from the tree, producing the broken sources.  The golden answer
is always the on-disk original (never model-authored); the gold patch is the multi-file
diff that restores every touched file.
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from harmony_bench.carve.closure import CarveResult
from harmony_bench.graph import symbol_span
from harmony_bench.sample.diffing import make_patch

# `export ` / `export default ` and ArkUI decorators (`@Component`, `@Entry`,
# `@Builder`, `@Reusable`, ...) sit *before* the parser's symbol span; swallow them so
# deletion doesn't leave a dangling decorator or `export`.
_EXPORT_PREFIX = re.compile(rb"export\s+(default\s+)?$")


def _swallow_prefix(raw: bytes, start: int) -> int:
    """Move `start` back to include the symbol's own leading indent, an inline
    `export`/`export default`, and any decorator lines directly above it."""
    text = raw.decode("utf-8", "replace")
    cur = text.rfind("\n", 0, start) + 1          # start of the symbol's line (incl. indent + export)
    while cur > 0:
        prev_end = cur - 1                        # the '\n' terminating the previous line
        prev_start = text.rfind("\n", 0, prev_end) + 1
        s = text[prev_start:prev_end].strip()
        if s.startswith("@") or s in ("export", "export default"):
            cur = prev_start
        else:
            break
    return len(text[:cur].encode("utf-8"))

_ROUTER_MAP_REL = os.path.join("src", "main", "resources", "base", "profile",
                               "router_map.json")


@dataclass
class FileHollow:
    target_rel: str
    golden_source: str    # disk original (GOLDEN)
    broken_source: str    # after feature removal


@dataclass
class HollowResult:
    files: List[FileHollow]           # one per touched file (anchor file first)
    deleted_names: List[str]
    gold_patch: str                   # multi-file unified diff: broken -> golden

    # Back-compat single-file accessors (anchor file is files[0]).
    @property
    def target_rel(self) -> str:
        return self.files[0].target_rel

    @property
    def golden_source(self) -> str:
        return self.files[0].golden_source

    @property
    def broken_source(self) -> str:
        return self.files[0].broken_source


def _hollow_one(index, abs_file: str, names: List[str]) -> Tuple[str, str, List[str]]:
    with open(abs_file, "r", encoding="utf-8") as fh:
        golden = fh.read()            # GOLDEN: trust the disk
    raw = golden.encode("utf-8")
    pf = index.files[abs_file]
    ranges: List[Tuple[int, int]] = []
    deleted: List[str] = []
    for nm in names:
        span = symbol_span(pf, nm)
        if span is None:
            continue
        start, end = span
        start = _swallow_prefix(raw, start)
        while end < len(raw) and raw[end:end + 1] == b"\n":
            end += 1
        ranges.append((start, end))
        deleted.append(nm)
    ranges.sort(reverse=True)
    b = raw
    for (s, e) in ranges:
        b = b[:s] + b[e:]
    return golden, b.decode("utf-8"), deleted


def _module_root_dir(index, abs_file: str) -> Optional[str]:
    d = os.path.dirname(os.path.abspath(abs_file))
    root = os.path.abspath(index.repo_root)
    while d and d.startswith(root):
        if os.path.exists(os.path.join(d, "oh-package.json5")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _hollow_router_map(index, module_dir: str, deleted: List[str]) -> Optional[FileHollow]:
    """Drop routerMap entries whose buildFunction was deleted, so the hollowed page
    leaves no dangling routing config (which the ArkTS build validates)."""
    rm_abs = os.path.join(module_dir, _ROUTER_MAP_REL)
    if not os.path.exists(rm_abs):
        return None
    golden = open(rm_abs, "r", encoding="utf-8").read()
    try:
        data = json.loads(golden)
    except Exception:
        return None
    entries = data.get("routerMap")
    if not isinstance(entries, list):
        return None
    dead = set(deleted)
    kept = [e for e in entries if e.get("buildFunction") not in dead]
    if len(kept) == len(entries):
        return None
    data["routerMap"] = kept
    broken = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    rel = os.path.relpath(rm_abs, index.repo_root)
    return FileHollow(rel, golden, broken)


_IMPORT_NAMED = re.compile(r"import\s+(?:type\s+)?\{([^}]*)\}\s*from\s*['\"][^'\"]+['\"];?[ \t]*\n?",
                           re.DOTALL)


def _strip_dead_imports(source: str, dead: set) -> str:
    """Remove named-import specifiers that refer to a deleted symbol; drop the whole
    import line when it becomes empty.  Keeps imports whose names survive."""
    def repl(m: "re.Match") -> str:
        specs = [s.strip() for s in m.group(1).split(",") if s.strip()]
        live = [s for s in specs if s.split(" as ")[0].strip() not in dead]
        if not live:
            return ""                       # whole import removed
        if len(live) == len(specs):
            return m.group(0)               # unchanged
        return m.group(0).replace(m.group(1), " " + ", ".join(live) + " ")
    return _IMPORT_NAMED.sub(repl, source)


def hollow_l2(index, carve: CarveResult) -> HollowResult:
    by_file: Dict[str, List[str]] = {}
    for (nm, f) in carve.extract:
        by_file.setdefault(os.path.abspath(f), []).append(nm)

    anchor_abs = os.path.abspath(carve.anchor_file)
    ordered = [anchor_abs] + sorted(f for f in by_file if f != anchor_abs)

    # First delete symbol definitions in each file, collecting the full deleted set.
    hollowed: List[Tuple[str, str, str]] = []   # (rel, golden, broken)
    all_deleted: List[str] = []
    for abs_file in ordered:
        golden, broken, deleted = _hollow_one(index, abs_file, by_file[abs_file])
        hollowed.append((os.path.relpath(abs_file, index.repo_root), golden, broken))
        all_deleted += deleted

    # Then strip now-dangling imports of any deleted symbol (cross-file cleanup).
    dead = set(all_deleted)
    files: List[FileHollow] = []
    patches: List[str] = []
    for rel, golden, broken in hollowed:
        broken = _strip_dead_imports(broken, dead)
        files.append(FileHollow(rel, golden, broken))
        patches.append(make_patch(rel, broken, golden))

    # Also strip any routerMap entry that pointed at a deleted page build function.
    module_dir = _module_root_dir(index, anchor_abs)
    if module_dir:
        rm = _hollow_router_map(index, module_dir, all_deleted)
        if rm is not None:
            files.append(rm)
            patches.append(make_patch(rm.target_rel, rm.broken_source, rm.golden_source))

    return HollowResult(files=files, deleted_names=all_deleted,
                        gold_patch="".join(patches))
