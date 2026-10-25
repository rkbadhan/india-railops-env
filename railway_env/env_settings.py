"""Load literal KEY=value settings from a local file without shell expansion."""

import json
import os
import re
from pathlib import Path


def load_env(path=".env", environment=None, required=False):
    path = Path(path)
    if not path.exists():
        if required:
            raise ValueError("requested environment file does not exist")
        return []
    parsed = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        try:
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key):
                raise ValueError()
            if value.startswith('"'):
                value, end = json.JSONDecoder().raw_decode(value)
                trailing = line.partition("=")[2].strip()[end:].strip()
                if not isinstance(value, str) or (trailing and not trailing.startswith("#")):
                    raise ValueError()
            elif value.startswith("'"):
                end = value.find("'", 1)
                if end < 0 or (value[end + 1:].strip() and not value[end + 1:].strip().startswith("#")):
                    raise ValueError()
                value = value[1:end]
            else:
                value = value.partition(" #")[0].rstrip()
            parsed[key] = value
        except ValueError:
            # File contents may include secrets; report only the line number.
            raise ValueError(f"invalid environment assignment at line {number}") from None
    target = os.environ if environment is None else environment
    for key, value in parsed.items():
        target.setdefault(key, value)
    return list(parsed)
