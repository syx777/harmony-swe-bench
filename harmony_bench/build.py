"""Assemble an Instance from carve + hollow + author artifacts."""
from __future__ import annotations
import hashlib
import os

from harmony_bench.carve.closure import CarveResult
from harmony_bench.hollow.l2 import HollowResult
from harmony_bench.author.generate import module_root
from harmony_bench.sample.diffing import make_patch
from harmony_bench.schema import Instance, HarmonyMeta, Validation


def _localunit_rel(carve: CarveResult, repo_root: str) -> str:
    mroot = module_root(carve.anchor_file, repo_root)
    p = os.path.join(mroot, "src", "test", "LocalUnit.test.ets")
    return os.path.relpath(p, repo_root)


def build_instance(carve: CarveResult,
                   hollow: HollowResult,
                   issue: str,
                   f2p_test: str,
                   repo_root: str,
                   base_commit: str) -> Instance:
    lu_rel = _localunit_rel(carve, repo_root)
    lu_abs = os.path.join(repo_root, lu_rel)
    with open(lu_abs, "r", encoding="utf-8") as fh:
        original_localunit = fh.read()
    # test_patch transforms the stock LocalUnit.test.ets into the F2P test -- exactly
    # the state the oracle judged.
    test_patch = make_patch(lu_rel, original_localunit, f2p_test)

    h = hashlib.sha1(f"{carve.anchor_rel}:{carve.anchor_name}".encode()).hexdigest()[:8]
    impl_lines = carve.anchor_source.count("\n") + 1 if carve.anchor_source else 0
    return Instance(
        instance_id=f"sample_in_harmonyos__new_feature-{h}",
        repo="sample_in_harmonyos",
        base_commit=base_commit,
        problem_statement=issue,
        patch=hollow.gold_patch,
        test_patch=test_patch,
        FAIL_TO_PASS=[carve.anchor_name],
        PASS_TO_PASS=[],
        harmony=HarmonyMeta(
            module=carve.module,
            kind=carve.kind,
            anchor=carve.anchor_name,
            target_file=carve.anchor_rel,
            target_files=carve.extract_files,
            closure=[n for (n, _f) in carve.closure],
            keep_interface=carve.keep_interface,
            difficulty={
                "closure_size": len(carve.closure),
                "external_refs": len(carve.external_ref_files),
                "impl_lines": impl_lines,
                "files_touched": len(carve.extract_files),
                "deleted_symbols": len(hollow.deleted_names),
            },
        ),
        validation=Validation(),
    )
