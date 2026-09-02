from pathlib import Path
from typing import Set

IMPORTED_FILE = Path(__file__).resolve().parent.parent / "imported.txt"


def load_imported_list() -> Set[str]:
    if not IMPORTED_FILE.exists():
        return set()
    result: Set[str] = set()
    with open(IMPORTED_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                result.add(line.lower())
    return result


def append_to_imported_list(owner_repo: str) -> None:
    key = owner_repo.lower()
    # 写入端去重：已记录过则跳过，避免 imported.txt 脏增长
    if key in load_imported_list():
        return
    with open(IMPORTED_FILE, "a", encoding="utf-8") as f:
        f.write(key + "\n")


def init_imported_file() -> None:
    if not IMPORTED_FILE.exists():
        with open(IMPORTED_FILE, "w", encoding="utf-8") as f:
            f.write("# 每行一个 owner/repo，已成功入库的项目\n")