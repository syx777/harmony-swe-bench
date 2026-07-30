"""Execution-backed gate: the compile-level integrity check for a carved feature.

For a (possibly multi-file) candidate it drives the runner three times against the real
material repo -- a "P2P passes + F2P fails + gold passes" integrity check, realized on
the HarmonyOS ArkTS toolchain:
  1. broken sources + the STOCK LocalUnit test -> must be GREEN
     (integrity: deleting the feature left no dangling reference in the kept code)
  2. broken sources + the F2P test             -> must be RED
     (the feature is genuinely absent -- the test can no longer resolve it)
  3. golden sources + the F2P test             -> must be GREEN
     (the on-disk gold restores it and the module compiles)
An instance passes only if all three hold.

Every touched source file and the module's `src/test/LocalUnit.test.ets` are backed up
and restored in a finally block.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Dict, List, Tuple

from harmony_bench.runner import type_check, Verdict


@dataclass
class GateResult:
    integrity_green: bool
    broken_red: bool
    gold_green: bool
    integrity_verdict: str
    broken_verdict: str
    gold_verdict: str
    integrity_tail: str = ""
    broken_tail: str = ""
    gold_tail: str = ""

    @property
    def passed(self) -> bool:
        return self.integrity_green and self.broken_red and self.gold_green


def _module_dir_name(module_root: str) -> str:
    return os.path.basename(os.path.abspath(module_root))


def gate(repo_root: str,
         toolchain_root: str,
         module_root: str,
         files: List[Tuple[str, str, str]],
         f2p_test_source: str,
         **runner_kwargs) -> GateResult:
    """`files`: list of (target_rel, broken_source, golden_source) -- one per file the
    feature was carved out of (the anchor file first)."""
    module_name = _module_dir_name(module_root)
    local_test = os.path.join(module_root, "src", "test", "LocalUnit.test.ets")
    if not os.path.exists(local_test):
        raise FileNotFoundError(f"module has no Local Test entry: {local_test}")

    targets = [(os.path.join(repo_root, rel), broken, golden)
               for (rel, broken, golden) in files]

    # In-memory backups (never write sidecar files -- hvigor's resource compiler
    # rejects stray filenames like `router_map.json.hbbak` under resources/).
    originals: Dict[str, str] = {}
    for abs_path, _b, _g in targets:
        originals[abs_path] = open(abs_path, "r", encoding="utf-8").read()
    originals[local_test] = open(local_test, "r", encoding="utf-8").read()
    stock_test = originals[local_test]

    def _write(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _run():
        return type_check(module_name, repo_root, toolchain_root, **runner_kwargs)

    try:
        # 1) broken + stock test -> integrity (no dangling refs in kept code)
        for abs_path, broken, _g in targets:
            _write(abs_path, broken)
        _write(local_test, stock_test)
        r_integrity = _run()

        # 2) broken + F2P test -> feature absent
        _write(local_test, f2p_test_source)
        r_broken = _run()

        # 3) golden + F2P test -> gold restores
        for abs_path, _b, golden in targets:
            _write(abs_path, golden)
        r_gold = _run()
    finally:
        for path, text in originals.items():
            _write(path, text)

    return GateResult(
        integrity_green=r_integrity.verdict is Verdict.GREEN,
        broken_red=r_broken.verdict is Verdict.RED,
        gold_green=r_gold.verdict is Verdict.GREEN,
        integrity_verdict=r_integrity.verdict.value,
        broken_verdict=r_broken.verdict.value,
        gold_verdict=r_gold.verdict.value,
        integrity_tail=r_integrity.log_tail,
        broken_tail=r_broken.log_tail,
        gold_tail=r_gold.log_tail,
    )
