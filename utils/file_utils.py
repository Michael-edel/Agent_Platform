from __future__ import annotations

from pathlib import Path
from typing import Union
import shutil

PathLike = Union[str, Path]

def ensure_dir(path: PathLike) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p

def safe_output_path(out_path: PathLike) -> Path:
    """Если файл занят/существует — подбирает новое имя _1, _2, ..."""
    final_path = Path(out_path)
    if final_path.exists():
        stem = final_path.stem
        suffix = final_path.suffix
        parent = final_path.parent
        i = 1
        while True:
            candidate = parent / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                final_path = candidate
                break
            i += 1
    return final_path

def copy_to(src: PathLike, dst: PathLike) -> None:
    srcp, dstp = Path(src), Path(dst)
    ensure_dir(dstp.parent)
    shutil.copy2(srcp, dstp)
