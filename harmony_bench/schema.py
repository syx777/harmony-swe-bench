"""Instance schema for the compile-level new_feature benchmark (L2)."""
from __future__ import annotations
from typing import Dict, List, Optional

import os
import pydantic


class HarmonyMeta(pydantic.BaseModel):
    module: str                     # oh-package name, e.g. @ohos/common
    task_type: str = "new_feature"  # this benchmark is L2 new_feature only
    level: str = "L2"               # from-interface (whole symbol removed)
    kind: str = ""                  # class | function | const
    anchor: str = ""                # the removed symbol
    target_file: str = ""           # repo-rel path of the anchor's file
    target_files: List[str] = []    # all files the feature was carved out of
    closure: List[str] = []         # forward-closure symbol names (context)
    keep_interface: List[str] = []  # deps left available to the solver
    difficulty: Dict[str, int] = {}


class Validation(pydantic.BaseModel):
    # execution-backed gate (compile-level oracle)
    integrity_green: Optional[bool] = None  # broken tree + stock test still compiles
    broken_red: Optional[bool] = None   # hollowed tree fails the ArkTS build (F2P red)
    gold_green: Optional[bool] = None   # original tree compiles (gold resolves)
    # LLM judge (issue quality); 0=best .. 3=worst
    issue_clarity: Optional[int] = None
    test_to_issue_alignment: Optional[int] = None
    interface_alignment: Optional[int] = None


class Instance(pydantic.BaseModel):
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    patch: str            # gold patch: broken -> golden (restores the feature)
    test_patch: str       # F2P hypium test (also drives the compile oracle)
    FAIL_TO_PASS: List[str] = []
    PASS_TO_PASS: List[str] = []
    harmony: HarmonyMeta
    validation: Validation = pydantic.Field(default_factory=Validation)


def write_instance(inst: Instance, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{inst.instance_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(inst.model_dump_json(indent=2))
    return path


def read_instance(path: str) -> Instance:
    with open(path, "r", encoding="utf-8") as fh:
        return Instance.model_validate_json(fh.read())
