# -*- coding: utf-8 -*-
"""
本机通达信同步助手（可单独拷贝到其它电脑）。

配置文件与本脚本同目录：tdx_bridge_config.json
  - 首次运行会生成并进入设置向导
  - 之后可随时：python tdx_bridge_agent.py --setup
  - 或直接改 JSON 后重启助手

用法:
  python tdx_bridge_agent.py          # 按配置开始同步
  python tdx_bridge_agent.py --setup  # 修改服务器地址 / 口令等
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

GBK = "gbk"
RECORD_SIZE = 120
NAME_SIZE = 50
ABBR_SIZE = 70

_DEFAULT_ROOT_CANDIDATES = (
    r"D:\SoftInstall\new_tdx",
    r"C:\new_tdx",
    r"D:\new_tdx",
    r"E:\new_tdx",
    r"C:\tdx",
    r"D:\tdx",
)

DEFAULT_CONFIG = {
    "api_base": "http://127.0.0.1:8000",
    "bridge_token": "pse-tdx-bridge",
    "tdx_root": "",
    "block_name": "PSE布林",
    "block_abbr": "PSE",
    "poll_sec": 2,
    # https 用 IP 访问时证书域名对不上，需关闭校验；域名备案正常后可改 true
    "ssl_verify": True,
}


def app_dir() -> Path:
    """脚本或打包 exe 所在目录（配置放这里，方便拷贝）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path() -> Path:
    return app_dir() / "tdx_bridge_config.json"


# ---- 通达信 .blk 写出（自包含，不依赖 PSE 仓库）----

def normalize_code(raw: str) -> str | None:
    s = (raw or "").strip().upper()
    if not s:
        return None
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else None


def tdx_market_prefix(code: str) -> str:
    c = code.zfill(6)
    if c[0] in ("4", "8"):
        return "2"
    if c[0] in ("5", "6", "9"):
        return "1"
    return "0"


def codes_to_blk_text(codes: list[str]) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for raw in codes:
        code = normalize_code(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        lines.append(f"{tdx_market_prefix(code)}{code}")
    return ("\r\n".join(lines) + "\r\n") if lines else ""


def resolve_blocknew_dir(tdx_root: str | Path) -> Path:
    root = Path(tdx_root)
    if root.name.lower() == "blocknew":
        return root
    if root.name.lower() == "t0002":
        return root / "blocknew"
    return root / "T0002" / "blocknew"


def detect_tdx_root(extra: list[str] | None = None) -> str | None:
    for c in list(extra or []) + list(_DEFAULT_ROOT_CANDIDATES):
        p = Path(c)
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
    abbr = (block_abbr or "").strip().upper()
    name = (block_name or abbr).strip()
    if not abbr:
        raise ValueError("板块简称不能为空")
    if not re.fullmatch(r"[A-Z0-9_]{1,20}", abbr):
        raise ValueError("板块简称仅允许 A-Z / 0-9 / _")

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
        "blk_path": str(blk_path.resolve()),
        "code_count": unique_count,
        "cfg_created": created,
        "block_name": name,
        "block_abbr": abbr,
    }


# ---- 配置读写 / 交互设置 ----

def load_config() -> dict:
    path = config_path()
    cfg = dict(DEFAULT_CONFIG)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update({k: raw[k] for k in DEFAULT_CONFIG if k in raw})
        except Exception as e:
            print(f"[tdx-bridge] 配置文件损坏，将重建: {e}")
    # 环境变量可临时覆盖（可选）
    if os.getenv("PSE_API_BASE"):
        cfg["api_base"] = os.environ["PSE_API_BASE"].strip()
    if os.getenv("TDX_BRIDGE_TOKEN"):
        cfg["bridge_token"] = os.environ["TDX_BRIDGE_TOKEN"].strip()
    if os.getenv("TDX_ROOT"):
        cfg["tdx_root"] = os.environ["TDX_ROOT"].strip()
    return cfg


def save_config(cfg: dict) -> None:
    path = config_path()
    out = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[tdx-bridge] 已保存配置: {path}")


def _prompt(label: str, current: str) -> str:
    shown = current if current else "(空)"
    val = input(f"  {label} [{shown}]: ").strip()
    return val if val else current


def run_setup(cfg: dict) -> dict:
    print("=" * 52)
    print("  通达信同步助手 · 设置")
    print(f"  配置文件: {config_path()}")
    print("=" * 52)
    print("直接回车 = 保持当前值\n")

    cfg["api_base"] = _prompt("服务器地址 api_base", str(cfg.get("api_base") or "")).rstrip("/")
    print("  (云端若域名 HTTPS 失败，可填 https://服务器公网IP ，例如 https://47.103.82.155)")
    cfg["bridge_token"] = _prompt("桥接口令 bridge_token", str(cfg.get("bridge_token") or ""))

    detected = detect_tdx_root()
    if not cfg.get("tdx_root") and detected:
        cfg["tdx_root"] = detected
        print(f"  (已自动探测通达信: {detected})")
    cfg["tdx_root"] = _prompt("通达信目录 tdx_root", str(cfg.get("tdx_root") or ""))

    cfg["block_name"] = _prompt("板块显示名", str(cfg.get("block_name") or "PSE布林"))
    cfg["block_abbr"] = _prompt("板块简称(.blk名)", str(cfg.get("block_abbr") or "PSE")).upper()

    poll = _prompt("轮询秒数 poll_sec", str(cfg.get("poll_sec") or 2))
    try:
        cfg["poll_sec"] = max(0.5, float(poll))
    except ValueError:
        cfg["poll_sec"] = 2

    save_config(cfg)
    print("\n设置完成。下次改配置可再运行: python tdx_bridge_agent.py --setup")
    print("或直接编辑同目录的 tdx_bridge_config.json\n")
    return cfg


def _is_ip_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").strip()
    except Exception:
        return False
    if not host:
        return False
    # IPv4
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
        return True
    # bare IPv6 in brackets already stripped by urlparse
    if ":" in host:
        return True
    return False


def _request(
    method: str,
    url: str,
    token: str,
    body: dict | None = None,
    *,
    ssl_verify: bool = True,
) -> dict:
    data = None
    headers = {"X-TDX-Bridge-Token": token, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    # 域名未备案时，SNI=域名会被掐断；用 IP + 关闭证书校验可直连
    need_insecure = (not ssl_verify) or _is_ip_host(url)
    ctx = None
    if url.lower().startswith("https://") and need_insecure:
        ctx = ssl._create_unverified_context()

    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_loop(cfg: dict) -> int:
    api_base = str(cfg.get("api_base") or "").rstrip("/")
    token = str(cfg.get("bridge_token") or "")
    tdx_root = str(cfg.get("tdx_root") or "") or (detect_tdx_root() or "")
    poll_sec = float(cfg.get("poll_sec") or 2)
    default_name = str(cfg.get("block_name") or "PSE布林")
    default_abbr = str(cfg.get("block_abbr") or "PSE")

    if not api_base:
        print("ERROR: 未设置服务器地址，请先 --setup")
        return 1
    if not token:
        print("ERROR: 未设置桥接口令，请先 --setup")
        return 1
    if not tdx_root:
        print("ERROR: 未找到通达信目录，请先 --setup 填写 tdx_root")
        return 1

    print(f"[tdx-bridge] 配置: {config_path()}")
    print(f"[tdx-bridge] api={api_base}")
    print(f"[tdx-bridge] tdx_root={tdx_root}")
    ssl_verify = bool(cfg.get("ssl_verify", True))
    if _is_ip_host(api_base) or not ssl_verify:
        print("[tdx-bridge] https: 已对 IP/关闭校验 使用 insecure SSL（证书域名不校验）")
    print(f"[tdx-bridge] poll={poll_sec}s  |  改设置: --setup  |  Ctrl+C 退出")

    last_id: str | None = None
    while True:
        try:
            raw = _request(
                "GET",
                f"{api_base}/api/tdx/bridge/pull",
                token,
                ssl_verify=ssl_verify,
            )
            if not raw.get("success"):
                print(f"[tdx-bridge] pull 失败: {raw.get('error')}")
            else:
                job = (raw.get("data") or {}).get("job")
                if job and job.get("id") and job["id"] != last_id:
                    name = job.get("block_name") or default_name
                    abbr = job.get("block_abbr") or default_abbr
                    codes = job.get("codes") or []
                    info = export_block(
                        tdx_root,
                        codes,
                        block_name=name,
                        block_abbr=abbr,
                    )
                    ack = _request(
                        "POST",
                        f"{api_base}/api/tdx/bridge/ack",
                        token,
                        {"id": job["id"]},
                        ssl_verify=ssl_verify,
                    )
                    last_id = job["id"]
                    print(
                        f"[tdx-bridge] 已写入 {info['blk_path']} · {info['code_count']} 只"
                        f" · ack={ack.get('success')}"
                    )
        except urllib.error.HTTPError as e:
            print(f"[tdx-bridge] HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            print(f"[tdx-bridge] 无法连接后端: {e.reason}")
        except Exception as e:
            print(f"[tdx-bridge] {type(e).__name__}: {e}")
        time.sleep(max(0.5, poll_sec))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通达信同步助手")
    parser.add_argument("--setup", action="store_true", help="交互修改服务器地址、口令等")
    args = parser.parse_args(argv)

    cfg = load_config()
    path = config_path()
    first_run = not path.is_file()

    if args.setup or first_run:
        if first_run:
            print("[tdx-bridge] 首次运行，请先完成设置。\n")
            save_config(cfg)
        cfg = run_setup(cfg)
        if args.setup and not first_run:
            # 仅改设置时询问是否立刻开始同步
            ans = input("是否立即开始同步？[Y/n]: ").strip().lower()
            if ans in ("n", "no"):
                return 0

    return run_loop(cfg)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[tdx-bridge] 已退出")
        raise SystemExit(0)
