"""Prompts for the author stage: turn a carved feature into a GitHub-style feature
request (problem_statement) and a runnable @ohos/hypium F2P test.

Design: the issue is derived from the golden symbol's signature/behaviour but must NOT
leak the implementation; the test IS derived from the golden code (it is the oracle
spec) and must import + type-use the symbol.
"""
from __future__ import annotations

ISSUE_SYSTEM = (
    "You are a senior HarmonyOS (ArkTS/ets) engineer writing a GitHub feature-request "
    "issue for a capability that is MISSING from the codebase. You are given the reference "
    "implementation only so you understand the contract; you must describe WHAT to build "
    "and WHY, never HOW. Do not paste or paraphrase the implementation, algorithm, or "
    "concrete formulas. Output GitHub-flavored markdown only."
)


def issue_user(carve) -> str:
    deps = ", ".join(carve.keep_interface) or "(none)"
    users = ", ".join(carve.external_ref_files[:5]) or "(none listed)"
    return (
        f"Module: {carve.module}\n"
        f"Symbol to (re)implement: `{carve.anchor_name}` ({carve.kind}) in `{carve.anchor_rel}`\n"
        f"Available dependencies it may use: {deps}\n"
        f"Call sites elsewhere in the repo: {users}\n\n"
        "Reference implementation (for your understanding only — DO NOT reveal it):\n"
        "```ets\n" + carve.anchor_source + "\n```\n\n"
        "Write the issue with these sections (markdown headers):\n"
        f"## {carve.anchor_name}: <short capability title>\n"
        "### Core Functionality — what the symbol must provide, in terms of its public API/contract.\n"
        "### Requirements — bullet list of observable behaviors/return shapes callers rely on.\n"
        "### Key Challenges — edge cases/constraints worth noting.\n"
        "### Interface — the exported name, kind, and the signatures a caller will import "
        "(names + parameter/return types only, no bodies).\n"
        "Keep it concise and implementation-agnostic."
    )


TEST_SYSTEM = (
    "You are writing a HarmonyOS Local Test (@ohos/hypium) that pins the behaviour of one "
    "ArkTS symbol. The test doubles as a compile-time type oracle, so it MUST import the "
    "symbol and call its real public API with correctly-typed arguments. Do NOT use any "
    "tools. Respond with the ets source of the test file inside a single ```ets fenced "
    "code block and nothing else."
)


def test_user(carve, import_path: str) -> str:
    return (
        f"Symbol under test: `{carve.anchor_name}` ({carve.kind}).\n"
        f"Import it from: '{import_path}'\n\n"
        "Reference implementation (derive correct expected values from it):\n"
        "```ets\n" + carve.anchor_source + "\n```\n\n"
        "Produce a complete test file with EXACTLY this shape:\n"
        "  * imports from '@ohos/hypium': describe, it, expect\n"
        f"  * import {{ {carve.anchor_name} }} from '{import_path}';\n"
        "  * `export default function localUnitTest() {{ describe('...', () => {{ it(...) }}) }}`\n"
        "Write 3-6 `it` cases exercising the public API with typed calls and `expect(...).assertEqual(...)` "
        "(or assertTrue/assertContain). Every case must reference "
        f"`{carve.anchor_name}` so the file fails to type-check if the symbol is absent."
    )
