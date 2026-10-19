"""Integration test for the compile-level oracle (runner.type_check).

Reproduces the empirically-validated red/green discrimination:
  * FormatDate present + F2P test imports it  -> GREEN
  * FormatDate deleted                        -> RED

Slow: each judgement invokes udocker + hvigor (~1-2 min). Run explicitly:
    python -m pytest tests/test_runner_oracle.py -s
or directly:
    python tests/test_runner_oracle.py
"""
from __future__ import annotations
import os
import shutil

from harmony_bench.runner import type_check, Verdict
from harmony_bench.paths import repo_root, toolchain_root

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = repo_root()
TC = toolchain_root(REPO)
DFU = os.path.join(REPO, "common/src/main/ets/util/DateFormatUtil.ets")
TEST = os.path.join(REPO, "common/src/test/LocalUnit.test.ets")


def _patch_test_to_use_formatdate(path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        s = fh.read()
    if "DateFormatUtil" in s:
        return
    s = s.replace(
        "import { describe",
        "import { FormatDate } from '../main/ets/util/DateFormatUtil';\nimport { describe",
        1)
    s = s.replace(
        "expect(a).assertContain(b);",
        "expect(a).assertContain(b);\n"
        "      expect(FormatDate.formatDate(new Date(2024,0,1),'YYYY-MM-DD')).assertEqual('2024-01-01');",
        1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(s)


def _delete_formatdate(path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        s = fh.read()
    i = s.find("export class FormatDate")
    assert i >= 0, "FormatDate anchor not found"
    j = s.find("{", i)
    depth = 0
    k = j
    while k < len(s):
        c = s[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                k += 1
                break
        k += 1
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(s[:i] + s[k:])


def test_oracle_discriminates_symbol_presence():
    dfu_bak = DFU + ".bak"
    test_bak = TEST + ".bak"
    shutil.copy(DFU, dfu_bak)
    shutil.copy(TEST, test_bak)
    try:
        _patch_test_to_use_formatdate(TEST)

        green = type_check("common", REPO, TC)
        print(f"[GREEN case] verdict={green.verdict}\n{green.log_tail}\n")
        assert green.verdict is Verdict.GREEN, f"expected GREEN, got {green.verdict}: {green.log_tail}"

        _delete_formatdate(DFU)
        red = type_check("common", REPO, TC)
        print(f"[RED case] verdict={red.verdict}\n{red.log_tail}\n")
        assert red.verdict is Verdict.RED, f"expected RED, got {red.verdict}: {red.log_tail}"
    finally:
        shutil.move(dfu_bak, DFU)
        shutil.move(test_bak, TEST)


if __name__ == "__main__":
    test_oracle_discriminates_symbol_presence()
    print("OK: oracle discriminates symbol presence (GREEN/RED)")
