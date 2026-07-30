from __future__ import annotations
import difflib
import subprocess


def _normalize(text: str) -> str:
    # Normalize trailing newlines so generated patches are git-apply-clean
    # (difflib omits the "\ No newline at end of file" marker).
    if text and not text.endswith("\n"):
        return text + "\n"
    return text


def make_patch(rel_path: str, before: str, after: str) -> str:
    before = _normalize(before)
    after = _normalize(after)
    if before == after:
        return ""

    # New-file case: emit a proper git new-file patch with /dev/null source.
    if before == "" and after != "":
        after_lines = after.splitlines()
        n = len(after_lines)
        lines = [
            f"diff --git a/{rel_path} b/{rel_path}\n",
            "new file mode 100644\n",
            "--- /dev/null\n",
            f"+++ b/{rel_path}\n",
            f"@@ -0,0 +1,{n} @@\n",
        ]
        lines.extend(f"+{line}\n" for line in after_lines)
        return "".join(lines)

    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before_lines, after_lines,
        fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}",
    )
    return "".join(diff)


def applies_cleanly(repo_root: str, patch: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "apply", "--check", "-"],
            input=patch.encode("utf-8"),
            cwd=repo_root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return proc.returncode == 0
    except Exception:
        return False
