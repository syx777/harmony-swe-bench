"""Fast tests for carve (symbol-level reference-closed closure) + hollow (L2 multi-file
deletion). Reflects the post-refactor behaviour: a feature may span several files.
"""
from __future__ import annotations
import os
import re

from harmony_bench.graph import build_repo_index, build_symbol_graph
from harmony_bench.carve import carve
from harmony_bench.carve.closure import module_of
from harmony_bench.hollow import hollow_l2
from harmony_bench.paths import repo_root

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = repo_root()


def _graph():
    idx = build_repo_index(REPO, ["common"])
    return idx, build_symbol_graph(idx)


def _decl_present(src: str, name: str) -> bool:
    return re.search(rf"\b(class|function|const|interface|enum)\s+{re.escape(name)}\b", src) is not None


def test_carve_invariants():
    """Anchor is always deleted; the delete set is single-module and consistent."""
    idx, sg = _graph()
    for name in ("UrlUtil", "FuzzyMatch", "FormatDate"):
        df = sg.defining_file(name)
        cv = carve(idx, sg, name, df)
        assert (name, df) in cv.extract, f"{name}: anchor missing from extract"
        mods = {module_of(idx, f) for (_n, f) in cv.extract}
        assert len(mods) == 1, f"{name}: delete set crosses modules {mods}"
        assert set(cv.extract_files) == {os.path.relpath(f, REPO) for (_n, f) in cv.extract}


def test_carve_supports_multifile():
    """FuzzyMatch pulls private helpers across files -> a multi-file feature."""
    idx, sg = _graph()
    df = sg.defining_file("FuzzyMatch")
    cv = carve(idx, sg, "FuzzyMatch", df)
    assert len(cv.extract_files) > 1, f"expected multi-file, got {cv.extract_files}"


def test_hollow_roundtrip():
    """Every deleted symbol is gone from its broken file, kept in golden, and the gold
    patch re-adds the anchor. Golden is the on-disk original (never model-authored)."""
    idx, sg = _graph()
    df = sg.defining_file("FuzzyMatch")
    cv = carve(idx, sg, "FuzzyMatch", df)
    hr = hollow_l2(idx, cv)
    assert cv.anchor_name in hr.deleted_names
    anchor_fh = next(fh for fh in hr.files if fh.target_rel == cv.anchor_rel)
    assert not _decl_present(anchor_fh.broken_source, "FuzzyMatch"), "anchor still in broken"
    assert _decl_present(anchor_fh.golden_source, "FuzzyMatch"), "anchor missing from golden"
    assert re.search(r"^\+.*\bFuzzyMatch\b", hr.gold_patch, re.MULTILINE), "patch does not re-add anchor"


def test_module_and_kind():
    idx, sg = _graph()
    cv = carve(idx, sg, "UrlUtil", sg.defining_file("UrlUtil"))
    assert cv.module == "@ohos/common"
    assert cv.kind == "class"
    assert cv.anchor_rel.endswith("UrlUtil.ets")
