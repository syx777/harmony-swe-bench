from __future__ import annotations
from typing import List
import pydantic
import json5


class ConfigRef(pydantic.BaseModel):
    config_path: str
    key_path: str
    value: str
    kind: str


def _walk(obj, path, config_path, out, parent_list_key=None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = f"{path}.{k}" if path else k
            if k == "srcEntry" and isinstance(v, str):
                out.append(ConfigRef(config_path=config_path, key_path=kp, value=v, kind="srcEntry"))
            elif k == "pages" and isinstance(v, str):
                out.append(ConfigRef(config_path=config_path, key_path=kp, value=v, kind="page"))
            elif k == "name" and parent_list_key in ("abilities", "extensionAbilities") and isinstance(v, str):
                out.append(ConfigRef(config_path=config_path, key_path=kp, value=v, kind="ability"))
            else:
                _walk(v, kp, config_path, out, parent_list_key)
    elif isinstance(obj, list):
        # the dict key that holds this list is the last ".key" segment of path
        list_key = path.split(".")[-1] if path else None
        for i, item in enumerate(obj):
            _walk(item, f"{path}[{i}]", config_path, out, list_key)


def parse_config(path: str) -> List[ConfigRef]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json5.load(fh)
    out: List[ConfigRef] = []
    _walk(data, "", path, out)
    return out
