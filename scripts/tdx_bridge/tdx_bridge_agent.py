# -*- coding: utf-8 -*-
"""
本机通达信同步助手（可单独拷贝到其它电脑）。

配置文件与本脚本同目录：tdx_bridge_config.json
  - 首次运行会生成并进入设置向导
  - 之后可随时：python tdx_bridge_agent.py --setup
  - 或直接改 JSON 后重启助手

用法:
  python tdx_bridge_agent.py             # 按配置开始同步
  python tdx_bridge_agent.py --setup     # 修改服务器地址 / 口令等
  python tdx_bridge_agent.py --sync-now  # 手动拉取列表并写入两个板块后退出
  tdx_bridge_agent.exe                   # 托盘后台运行（打包后）
  tdx_bridge_agent.exe --console         # 前台控制台（调试）

手动同步（不依赖网页点击）:
  - 托盘右键菜单：「立即同步两个板块 / 仅命中列表 / 仅收藏夹」
  - 命令行：--sync-now [both|matches|favorites]（默认 both，完成后退出）
  - 配置 auto_sync_min > 0 时，后台每 N 分钟自动执行一次手动同步
  板块归属：命中列表 → block_name/block_abbr；收藏夹 → fav_block_name/fav_block_abbr
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

GBK = "gbk"
RECORD_SIZE = 120
NAME_SIZE = 50
ABBR_SIZE = 70
_STOP = threading.Event()
_LOG_LOCK = threading.Lock()

_DEFAULT_ROOT_CANDIDATES = (
    r"D:\SoftInstall\new_tdx",
    r"C:\new_tdx",
    r"D:\new_tdx",
    r"E:\new_tdx",
    r"C:\tdx",
    r"D:\tdx",
)

_DEFAULT_GTJA_CANDIDATES = (
    r"D:\SoftInstall\GTJA\RichEZ\newVer\newVer",
    r"D:\SoftInstall\GTJA\RichEZ",
    r"C:\GTJA\RichEZ\newVer\newVer",
    r"D:\GTJA\RichEZ\newVer\newVer",
)

DEFAULT_CONFIG = {
    "api_base": "http://127.0.0.1:8000",
    "bridge_token": "pse-tdx-bridge",
    "tdx_root": "",
    # 国泰海通富易（通达信内核，同样写 T0002/blocknew/*.blk）；空=不写
    "gtja_root": "",
    "block_name": "PSE布林",
    "block_abbr": "PSE",
    # 收藏夹板块（手动同步 / 网页收藏夹推送均写这里）
    "fav_block_name": "PSE收藏",
    "fav_block_abbr": "PSEFAV",
    # 手动同步：命中列表查询参数（与网页默认一致）
    "match_period": "daily",  # daily|weekly|monthly|空=全部周期
    "match_end_within_days": 3,  # 0=全量历史命中
    "match_limit": 200,
    "fav_limit": 200,
    # >0 时后台每 N 分钟自动执行一次「手动同步」（无需网页触发）；0=关闭
    "auto_sync_min": 0,
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


def log_path() -> Path:
    return app_dir() / "tdx_bridge.log"


def log(msg: str) -> None:
    """写日志；有控制台时同步打印。"""
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with _LOG_LOCK:
        try:
            with log_path().open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    try:
        if sys.stdout and hasattr(sys.stdout, "write"):
            print(msg, flush=True)
    except Exception:
        pass


def ensure_console() -> None:
    """无窗口 exe 下做 --setup 时分配控制台。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.GetConsoleWindow():
            return
        ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
    except Exception:
        pass


def message_box(text: str, title: str = "通达信同步助手") -> None:
    if sys.platform != "win32":
        print(f"{title}: {text}")
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        pass


def open_path(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception as e:
        log(f"[tdx-bridge] 无法打开 {path}: {e}")


def make_tray_image():
    """生成简单托盘图标（蓝底白字 T）。"""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((2, 2, size - 3, size - 3), radius=12, fill=(30, 104, 210, 255))
    draw.text((22, 14), "T", fill=(255, 255, 255, 255))
    return img


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


def detect_gtja_root(extra: list[str] | None = None) -> str | None:
    """国泰海通富易安装根（含 T0002/blocknew 的目录）。"""
    for c in list(extra or []) + list(_DEFAULT_GTJA_CANDIDATES):
        p = Path(c)
        if (p / "T0002" / "blocknew").is_dir():
            return str(p.resolve())
        # 有时用户指到 newVer 上一级
        if (p / "newVer" / "T0002" / "blocknew").is_dir():
            return str((p / "newVer").resolve())
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
            log(f"[tdx-bridge] 配置文件损坏，将重建: {e}")
    # 环境变量可临时覆盖（可选）
    if os.getenv("PSE_API_BASE"):
        cfg["api_base"] = os.environ["PSE_API_BASE"].strip()
    if os.getenv("TDX_BRIDGE_TOKEN"):
        cfg["bridge_token"] = os.environ["TDX_BRIDGE_TOKEN"].strip()
    if os.getenv("TDX_ROOT"):
        cfg["tdx_root"] = os.environ["TDX_ROOT"].strip()
    if os.getenv("GTJA_ROOT"):
        cfg["gtja_root"] = os.environ["GTJA_ROOT"].strip()
    return cfg


def save_config(cfg: dict) -> None:
    path = config_path()
    out = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"[tdx-bridge] 已保存配置: {path}")


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

    gtja_detected = detect_gtja_root()
    if not cfg.get("gtja_root") and gtja_detected:
        cfg["gtja_root"] = gtja_detected
        print(f"  (已自动探测富易: {gtja_detected})")
    cfg["gtja_root"] = _prompt(
        "国泰海通富易目录 gtja_root (空=不同步富易)",
        str(cfg.get("gtja_root") or ""),
    )

    cfg["block_name"] = _prompt("命中列表板块显示名", str(cfg.get("block_name") or "PSE布林"))
    cfg["block_abbr"] = _prompt("命中列表板块简称(.blk名)", str(cfg.get("block_abbr") or "PSE")).upper()
    cfg["fav_block_name"] = _prompt("收藏夹板块显示名", str(cfg.get("fav_block_name") or "PSE收藏"))
    cfg["fav_block_abbr"] = _prompt("收藏夹板块简称(.blk名)", str(cfg.get("fav_block_abbr") or "PSEFAV")).upper()

    print("\n  -- 手动同步（不经网页，直接从服务器拉列表写板块）--")
    period = _prompt(
        "命中列表周期 match_period (daily/weekly/monthly/空=全部)",
        str(cfg.get("match_period") or "daily"),
    ).strip().lower()
    cfg["match_period"] = period if period in ("daily", "weekly", "monthly") else ""
    for key, label, default in (
        ("match_end_within_days", "近端结束根数 (0=全量)", 3),
        ("match_limit", "命中列表条数上限", 200),
        ("fav_limit", "收藏夹条数上限", 200),
    ):
        val = _prompt(f"{label} {key}", str(cfg.get(key, default)))
        try:
            cfg[key] = max(0, int(val))
        except ValueError:
            cfg[key] = default
    auto = _prompt("自动手动同步间隔分钟 auto_sync_min (0=关闭)", str(cfg.get("auto_sync_min") or 0))
    try:
        cfg["auto_sync_min"] = max(0, int(auto))
    except ValueError:
        cfg["auto_sync_min"] = 0

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


def _export_to_targets(
    codes: list[str],
    *,
    tdx_root: str,
    gtja_root: str,
    block_name: str,
    block_abbr: str,
) -> list[dict]:
    """写入通达信 / 富易（同格式 .blk）；返回成功结果列表。"""
    results: list[dict] = []
    targets: list[tuple[str, str]] = []
    if tdx_root.strip():
        targets.append(("通达信", tdx_root.strip()))
    if gtja_root.strip():
        targets.append(("富易", gtja_root.strip()))
    if not targets:
        raise ValueError("tdx_root 与 gtja_root 均为空，无处可写")

    for label, root in targets:
        try:
            info = export_block(
                root,
                codes,
                block_name=block_name,
                block_abbr=block_abbr,
            )
            info["target"] = label
            results.append(info)
            log(
                f"[tdx-bridge] [{label}] 已写入 {info['blk_path']} · {info['code_count']} 只"
                + (" · 新建板块(需重启软件可见)" if info.get("cfg_created") else "")
            )
        except Exception as e:
            log(f"[tdx-bridge] [{label}] 写入失败: {type(e).__name__}: {e}")
    if not results:
        raise RuntimeError("所有目标均写入失败")
    return results


# ---- 手动同步：直接从服务器拉列表，不经网页点击 ----

_SYNC_LOCK = threading.Lock()
_SYNCING = False


def _dedup_codes(items: list[dict]) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for it in items:
        c = str((it or {}).get("code") or "").strip()
        if c and c not in seen:
            seen.add(c)
            codes.append(c)
    return codes


def fetch_match_codes(api_base: str, cfg: dict, ssl_verify: bool) -> list[str]:
    """拉取布林编排命中列表（与网页「命中列表」同源接口）。"""
    period = str(cfg.get("match_period") or "").strip().lower()
    try:
        end_within = int(cfg.get("match_end_within_days") or 0)
    except (TypeError, ValueError):
        end_within = 3
    try:
        limit = max(1, int(cfg.get("match_limit") or 200))
    except (TypeError, ValueError):
        limit = 100
    qs = {"end_within_days": str(end_within), "order_by": "score", "limit": str(limit)}
    if period in ("daily", "weekly", "monthly"):
        qs["period"] = period
    url = f"{api_base}/api/boll-pattern-matches?{urlencode(qs)}"
    raw = _request("GET", url, str(cfg.get("bridge_token") or ""), ssl_verify=ssl_verify)
    if not raw.get("success"):
        raise RuntimeError(f"命中列表接口失败: {raw.get('error')}")
    return _dedup_codes((raw.get("data") or {}).get("items") or [])


def fetch_favorite_codes(api_base: str, cfg: dict, ssl_verify: bool) -> list[str]:
    """拉取收藏夹列表（与网页「收藏夹」同源接口）。"""
    try:
        limit = max(1, int(cfg.get("fav_limit") or 200))
    except (TypeError, ValueError):
        limit = 200
    url = f"{api_base}/api/boll-pattern-favorites?{urlencode({'limit': str(limit)})}"
    raw = _request("GET", url, str(cfg.get("bridge_token") or ""), ssl_verify=ssl_verify)
    if not raw.get("success"):
        raise RuntimeError(f"收藏夹接口失败: {raw.get('error')}")
    return _dedup_codes((raw.get("data") or {}).get("items") or [])


def manual_sync(cfg: dict, which: str = "both") -> str:
    """
    手动同步：直接从服务器拉「命中列表 / 收藏夹」，写入对应板块。
    which: both | matches | favorites。返回一行结果摘要（供托盘气泡/弹窗）。
    """
    global _SYNCING
    with _SYNC_LOCK:
        if _SYNCING:
            return "已有手动同步在进行中，稍后再试"
        _SYNCING = True
    try:
        api_base = str(cfg.get("api_base") or "").rstrip("/")
        if not api_base:
            raise ValueError("未设置服务器地址 api_base")
        tdx_root = str(cfg.get("tdx_root") or "") or (detect_tdx_root() or "")
        gtja_root = str(cfg.get("gtja_root") or "") or (detect_gtja_root() or "")
        if not tdx_root and not gtja_root:
            raise ValueError("tdx_root 与 gtja_root 均为空，无处可写")
        ssl_verify = bool(cfg.get("ssl_verify", True))

        summary: list[str] = []
        tasks: list[tuple[str, object, str, str]] = []
        if which in ("both", "matches"):
            tasks.append((
                "命中列表",
                lambda: fetch_match_codes(api_base, cfg, ssl_verify),
                str(cfg.get("block_name") or "PSE布林"),
                str(cfg.get("block_abbr") or "PSE").upper(),
            ))
        if which in ("both", "favorites"):
            tasks.append((
                "收藏夹",
                lambda: fetch_favorite_codes(api_base, cfg, ssl_verify),
                str(cfg.get("fav_block_name") or "PSE收藏"),
                str(cfg.get("fav_block_abbr") or "PSEFAV").upper(),
            ))

        ok_count = 0
        for label, fetcher, name, abbr in tasks:
            try:
                codes = fetcher()  # type: ignore[operator]
                if not codes:
                    log(f"[tdx-bridge] [手动同步] {label} 为空，跳过写入（保留原板块内容）")
                    summary.append(f"{label}: 空，跳过")
                    continue
                infos = _export_to_targets(
                    codes,
                    tdx_root=tdx_root,
                    gtja_root=gtja_root,
                    block_name=name,
                    block_abbr=abbr,
                )
                ok_count += 1
                log(f"[tdx-bridge] [手动同步] {label} → {name}({abbr}) · {len(codes)} 只 · {len(infos)} 处目标")
                summary.append(f"{label}: {len(codes)} 只 → {name}")
            except Exception as e:
                log(f"[tdx-bridge] [手动同步] {label} 失败: {type(e).__name__}: {e}")
                summary.append(f"{label}: 失败 {type(e).__name__}")

        head = "手动同步完成" if ok_count == len(tasks) and tasks else (
            "手动同步部分完成" if ok_count else "手动同步失败"
        )
        return f"{head}\n" + "\n".join(summary)
    except Exception as e:
        log(f"[tdx-bridge] [手动同步] 异常: {type(e).__name__}: {e}")
        return f"手动同步异常: {type(e).__name__}: {e}"
    finally:
        with _SYNC_LOCK:
            _SYNCING = False


def run_loop(cfg: dict) -> int:
    api_base = str(cfg.get("api_base") or "").rstrip("/")
    token = str(cfg.get("bridge_token") or "")
    tdx_root = str(cfg.get("tdx_root") or "") or (detect_tdx_root() or "")
    gtja_root = str(cfg.get("gtja_root") or "") or (detect_gtja_root() or "")
    poll_sec = float(cfg.get("poll_sec") or 2)
    default_name = str(cfg.get("block_name") or "PSE布林")
    default_abbr = str(cfg.get("block_abbr") or "PSE")

    if not api_base:
        log("ERROR: 未设置服务器地址，请先 --setup")
        return 1
    if not token:
        log("ERROR: 未设置桥接口令，请先 --setup")
        return 1
    if not tdx_root and not gtja_root:
        log("ERROR: 通达信与富易目录均为空，请先 --setup")
        return 1

    log(f"[tdx-bridge] 配置: {config_path()}")
    log(f"[tdx-bridge] api={api_base}")
    log(f"[tdx-bridge] tdx_root={tdx_root or '(跳过)'}")
    log(f"[tdx-bridge] gtja_root={gtja_root or '(跳过)'}")
    ssl_verify = bool(cfg.get("ssl_verify", True))
    if _is_ip_host(api_base) or not ssl_verify:
        log("[tdx-bridge] https: 已对 IP/关闭校验 使用 insecure SSL（证书域名不校验）")
    log(f"[tdx-bridge] poll={poll_sec}s  | 托盘右键可退出 | 日志: {log_path()}")

    last_id: str | None = None
    try:
        auto_sync_min = float(cfg.get("auto_sync_min") or 0)
    except (TypeError, ValueError):
        auto_sync_min = 0.0
    next_auto = time.time() + auto_sync_min * 60 if auto_sync_min > 0 else None
    if auto_sync_min > 0:
        log(f"[tdx-bridge] 自动手动同步: 每 {auto_sync_min:g} 分钟")
    while not _STOP.is_set():
        try:
            raw = _request(
                "GET",
                f"{api_base}/api/tdx/bridge/pull",
                token,
                ssl_verify=ssl_verify,
            )
            if not raw.get("success"):
                log(f"[tdx-bridge] pull 失败: {raw.get('error')}")
            else:
                job = (raw.get("data") or {}).get("job")
                if job and job.get("id") and job["id"] != last_id:
                    name = job.get("block_name") or default_name
                    abbr = job.get("block_abbr") or default_abbr
                    codes = job.get("codes") or []
                    infos = _export_to_targets(
                        codes,
                        tdx_root=tdx_root,
                        gtja_root=gtja_root,
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
                    n = infos[0]["code_count"] if infos else 0
                    log(
                        f"[tdx-bridge] 完成 · {n} 只 · 目标 {len(infos)} 处 · ack={ack.get('success')}"
                    )
        except urllib.error.HTTPError as e:
            log(f"[tdx-bridge] HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            log(f"[tdx-bridge] 无法连接后端: {e.reason}")
        except Exception as e:
            log(f"[tdx-bridge] {type(e).__name__}: {e}")
        if next_auto is not None and time.time() >= next_auto:
            manual_sync(cfg)
            next_auto = time.time() + auto_sync_min * 60
        _STOP.wait(max(0.5, poll_sec))
    log("[tdx-bridge] 已停止")
    return 0


def run_tray(cfg: dict) -> int:
    try:
        import pystray
        from pystray import MenuItem as Item
    except ImportError:
        log("[tdx-bridge] 未安装 pystray，改为前台运行。pip install pystray pillow")
        return run_loop(cfg)

    def on_open_config(icon, item):  # noqa: ARG001
        open_path(config_path())

    def on_open_log(icon, item):  # noqa: ARG001
        if not log_path().is_file():
            log_path().write_text("", encoding="utf-8")
        open_path(log_path())

    def _run_manual_sync(icon, which: str) -> None:
        """在后台线程执行手动同步，完成后托盘气泡提示。"""

        def worker():
            result = manual_sync(cfg, which)
            log(f"[tdx-bridge] [手动同步] {result.replace(chr(10), ' | ')}")
            try:
                icon.notify(result, "通达信同步助手")
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def on_sync_both(icon, item):  # noqa: ARG001
        _run_manual_sync(icon, "both")

    def on_sync_matches(icon, item):  # noqa: ARG001
        _run_manual_sync(icon, "matches")

    def on_sync_favorites(icon, item):  # noqa: ARG001
        _run_manual_sync(icon, "favorites")

    def on_exit(icon, item):  # noqa: ARG001
        _STOP.set()
        icon.stop()

    def on_ready(icon):
        icon.visible = True
        try:
            icon.notify(
                "已在后台运行",
                "通达信同步助手\n右键托盘：立即同步两个板块 / 配置 / 日志 / 退出",
            )
        except Exception:
            pass

    icon = pystray.Icon(
        "tdx_bridge",
        make_tray_image(),
        "通达信同步助手",
        menu=pystray.Menu(
            Item("立即同步两个板块", on_sync_both),
            Item("仅同步命中列表", on_sync_matches),
            Item("仅同步收藏夹", on_sync_favorites),
            Item("打开配置", on_open_config),
            Item("打开日志", on_open_log),
            Item("退出", on_exit),
        ),
    )

    worker = threading.Thread(target=run_loop, args=(cfg,), daemon=True)
    worker.start()
    icon.run(setup=on_ready)
    _STOP.set()
    worker.join(timeout=5)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="通达信同步助手")
    parser.add_argument("--setup", action="store_true", help="交互修改服务器地址、口令等")
    parser.add_argument(
        "--console",
        action="store_true",
        help="前台控制台运行（调试用；打包 exe 默认托盘后台）",
    )
    parser.add_argument(
        "--sync-now",
        nargs="?",
        const="both",
        choices=("both", "matches", "favorites"),
        default=None,
        metavar="both|matches|favorites",
        help="立即手动同步两个板块（不经网页点击），完成后退出；默认 both",
    )
    args = parser.parse_args(argv)

    frozen = bool(getattr(sys, "frozen", False))
    use_tray = frozen and not args.console and not args.setup and not args.sync_now

    cfg = load_config()
    path = config_path()
    first_run = not path.is_file()

    if args.setup or (first_run and not use_tray):
        ensure_console()
        if first_run:
            log("[tdx-bridge] 首次运行，请先完成设置。\n")
            save_config(cfg)
        cfg = run_setup(cfg)
        if args.setup and not first_run:
            ans = input("是否立即开始同步？[Y/n]: ").strip().lower()
            if ans in ("n", "no"):
                return 0

    if first_run and use_tray:
        save_config(cfg)
        message_box(
            "首次运行：已生成配置文件。\n"
            "请填写 api_base / bridge_token / tdx_root 后保存，\n"
            "然后重新双击运行本程序。\n\n"
            f"配置路径:\n{config_path()}",
        )
        open_path(config_path())
        return 0

    if args.sync_now:
        if frozen and not args.console:
            ensure_console()
        result = manual_sync(cfg, args.sync_now)
        head = result.split("\n", 1)[0]
        if frozen and not args.console:
            # 交互场景弹窗展示结果；无人值守请改用托盘或 auto_sync_min
            message_box(result)
        return 0 if head == "手动同步完成" else 1

    if use_tray:
        return run_tray(cfg)
    return run_loop(cfg)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        _STOP.set()
        log("\n[tdx-bridge] 已退出")
        raise SystemExit(0)
