"""總結匯出:設定 AUTOLIVEBLOG_OBSIDIAN_VAULT 後自動複製 md 到 vault。"""
import shutil
from pathlib import Path

from . import config


def copy_to_obsidian(md_path: Path) -> None:
    vault = config.OBSIDIAN_VAULT
    if not vault:
        return
    try:
        dst_dir = Path(vault) / "autoliveblog"
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(md_path, dst_dir / md_path.name)
    except OSError as e:
        print(f"[export] Obsidian 複製失敗:{e}")
