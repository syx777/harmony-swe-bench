"""Author stage: generate the problem_statement and the F2P hypium test for a carve."""
from __future__ import annotations
import os
import re

from harmony_bench.author import prompts
from harmony_bench.carve.closure import CarveResult

_CODE_BLOCK = re.compile(r"```[A-Za-z0-9_+-]*\n(.*?)```", re.DOTALL)


def module_root(anchor_file: str, repo_root: str) -> str:
    """Directory of the module (contains oh-package.json5) that owns anchor_file."""
    d = os.path.dirname(os.path.abspath(anchor_file))
    root = os.path.abspath(repo_root)
    while d and d.startswith(root):
        if os.path.exists(os.path.join(d, "oh-package.json5")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.dirname(anchor_file)


def test_import_path(carve: CarveResult, repo_root: str) -> str:
    """Relative import specifier from <module>/src/test/ to the anchor file (no ext)."""
    mroot = module_root(carve.anchor_file, repo_root)
    test_dir = os.path.join(mroot, "src", "test")
    anchor_noext = os.path.splitext(carve.anchor_file)[0]
    rel = os.path.relpath(anchor_noext, test_dir)
    return rel if rel.startswith(".") else "./" + rel


def _extract_code(text: str) -> str:
    """Pull the test source out of a chatty reply: prefer the largest fenced code
    block; fall back to the stripped raw text if there is no fence."""
    blocks = _CODE_BLOCK.findall(text)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    return text.strip() + "\n"


def author_issue(client, carve: CarveResult) -> str:
    return client.complete(prompts.ISSUE_SYSTEM, prompts.issue_user(carve)).strip()


def author_test(client, carve: CarveResult, repo_root: str) -> str:
    import_path = test_import_path(carve, repo_root)
    raw = client.complete(prompts.TEST_SYSTEM, prompts.test_user(carve, import_path))
    return _extract_code(raw)
