# -*- coding: utf-8 -*-
"""通达信自定义板块 (.blk + blocknew.cfg) 写出。"""

from __future__ import annotations

import re
from pathlib import Path

GBK = "gbk"
RECORD_SIZE = 120
NAME_SIZE = 50
ABBR_SIZE = 70

# 常见安装根目录候选（含本机）
_DEFAULT_ROOT_CANDIDATES = (
    r"D:\SoftInstall\new_tdx",
    r"C:\new_tdx",
    r"D:\new_tdx",
    r"E:\new_tdx",
    r"C:\tdx",
    r"D:\tdx",
)


def normalize_code(raw: str) -> str | None:
    """提取 6 位 A 股代码；失败返回 None。"""
    s = (raw or "").strip().upper()
    if not s:
        return None
    m = re.search(r"(\d{6})", s)
    if not m:
        return None
    return m.group(1)


def tdx_market_prefix(code: str) -> str:
    """
    通达信 .blk 行首市场位：
    0=深 1=沪 2=北交所。
    """
    c = code.zfill(6)
    if c[0] in ("4", "8"):
        return "2"
    if c[0] in ("5", "6", "9"):
        return "1"
    return "0"


def codes_to_blk_text(codes: list[str]) -> str:
    """去重保序，生成通达信 .blk 文本（\\r\\n）。"""
    seen: set[str] = set()
    lines: list[str] = []
    for raw in codes:
        code = normalize_code(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        lines.append(f"{tdx_market_prefix(code)}{code}")
    if not lines:
        return ""
    return "\r\n".join(lines) + "\r\n"


def resolve_blocknew_dir(tdx_root: str | Path) -> Path:
    root = Path(tdx_root)
    # 允许直接传入 blocknew 或 T0002
    if root.name.lower() == "blocknew":
        return root
    if root.name.lower() == "t0002":
        return root / "blocknew"
    return root / "T0002" / "blocknew"


def detect_tdx_root(extra: list[str] | None = None) -> str | None:
    candidates = list(extra or []) + list(_DEFAULT_ROOT_CANDIDATES)
    for c in candidates:
        p = Path(c)
        if (p / "T0002" / "blocknew").is_dir() or (p / "tdxw.exe").is_file():
            if (p / "T0002" / "blocknew").is_dir():
                return str(p.resolve())
    return None


def _pad_gbk(text: str, size: int) -> bytes:
    raw = (text or "").encode(GBK, errors="replace")[: size - 1]
    return raw + b"\x00" * (size - len(raw))


def _read_cfg_records(cfg_path: Path) -> list[bytearray]:
    if not cfg_path.is_file():
        return []
    data = cfg_path.read_bytes()
    n = len(data) // RECORD_SIZE
    return [bytearray(data[i * RECORD_SIZE : (i + 1) * RECORD_SIZE]) for i in range(n)]


def _abbr_of_record(rec: bytearray | bytes) -> str:
    return bytes(rec[NAME_SIZE:]).split(b"\x00", 1)[0].decode(GBK, errors="replace")


def ensure_block_in_cfg(cfg_path: Path, block_name: str, block_abbr: str) -> bool:
    """
    若 abbr 已存在则更新显示名；否则追加记录。
    返回 True 表示新建了条目。
    """
    abbr = (block_abbr or "").strip().upper()
    name = (block_name or abbr).strip()
    if not abbr:
        raise ValueError("板块简称(abbr)不能为空")
    if not re.fullmatch(r"[A-Z0-9_]{1,20}", abbr):
        raise ValueError("板块简称仅允许 A-Z / 0-9 / _，最长 20")

    records = _read_cfg_records(cfg_path)
    for rec in records:
        if _abbr_of_record(rec).upper() == abbr:
            rec[0:NAME_SIZE] = _pad_gbk(name, NAME_SIZE)
            cfg_path.write_bytes(b"".join(bytes(r) for r in records))
            return False

    new_rec = bytearray(RECORD_SIZE)
    new_rec[0:NAME_SIZE] = _pad_gbk(name, NAME_SIZE)
    new_rec[NAME_SIZE:RECORD_SIZE] = _pad_gbk(abbr, ABBR_SIZE)
    records.append(new_rec)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_bytes(b"".join(bytes(r) for r in records))
    return True


def export_block(
    tdx_root: str | Path,
    codes: list[str],
    *,
    block_name: str = "PSE布林",
    block_abbr: str = "PSE",
) -> dict:
    """
    写出自定义板块 .blk，并确保 blocknew.cfg 有对应条目。
    """
    block_dir = resolve_blocknew_dir(tdx_root)
    if not block_dir.is_dir():
        raise FileNotFoundError(f"找不到通达信板块目录: {block_dir}")

    abbr = (block_abbr or "PSE").strip().upper()
    name = (block_name or "PSE布林").strip()
    text = codes_to_blk_text(codes)
    unique_count = 0 if not text else text.count("\n")

    cfg_path = block_dir / "blocknew.cfg"
    created = ensure_block_in_cfg(cfg_path, name, abbr)

    blk_path = block_dir / f"{abbr}.blk"
    blk_path.write_bytes(text.encode("ascii"))

    return {
        "tdx_root": str(Path(tdx_root).resolve()) if Path(tdx_root).exists() else str(tdx_root),
        "block_dir": str(block_dir.resolve()),
        "blk_path": str(blk_path.resolve()),
        "cfg_path": str(cfg_path.resolve()),
        "block_name": name,
        "block_abbr": abbr,
        "code_count": unique_count,
        "cfg_created": created,
    }
