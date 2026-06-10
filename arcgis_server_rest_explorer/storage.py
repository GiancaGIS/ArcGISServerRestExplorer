import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json_file(path: Path, default: Any) -> tuple[Any, str | None]:
    if not path.exists():
        return default, None

    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as exc:
        return default, f"Could not read {path.name}: {exc}"


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp_path, path)


def backup_corrupt_json(path: Path) -> Path | None:
    if not path.exists():
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.corrupt-{stamp}.bak")
    path.replace(backup_path)
    return backup_path
