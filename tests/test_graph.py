"""Fast (no-udocker) tests for the ArkTS symbol graph on the real material repo."""
from __future__ import annotations
import os

from harmony_bench.graph import build_repo_index, build_symbol_graph, forward_refs

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
SUBDIRS = ["common", "features", "products", "AppScope"]


def _graph():
    idx = build_repo_index(REPO, SUBDIRS)
    return idx, build_symbol_graph(idx)


def test_index_and_defs():
    idx, sg = _graph()
    assert len(idx.files) > 300
    for name in ("FuzzyMatch", "UrlUtil", "FormatDate", "ColorUtil"):
        assert sg.defining_file(name), f"{name} should be defined"


def test_fuzzymatch_forward_closure():
    idx, sg = _graph()
    df = sg.defining_file("FuzzyMatch")
    fwd = {n for (n, _f) in forward_refs(idx, sg, "FuzzyMatch", df)}
    assert "RawfileIO" in fwd
    assert "FuzzyMatchResult" in fwd


def test_urlutil_is_self_contained():
    idx, sg = _graph()
    df = sg.defining_file("UrlUtil")
    fwd = {n for (n, _f) in forward_refs(idx, sg, "UrlUtil", df)}
    assert fwd == set(), f"UrlUtil expected self-contained, got {fwd}"
