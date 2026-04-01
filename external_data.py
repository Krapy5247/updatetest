"""從 data/ 載入介面翻譯、主題色、平台／金額、座標等外部 JSON。

優先順序：exe／腳本同層的 data/（可讓使用者修改；OTA 的 extra_files 亦寫入此處）；
若缺檔則嘗試 PyInstaller 內建 _MEIPASS/data/。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundled_resources_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def resolve_data_path(relative: str) -> Path:
    """relative 如 'theme.json' 或 'i18n/zh-tw.json'。"""
    name = relative.replace("\\", "/").lstrip("/")
    for base in (_app_base_dir() / "data", _bundled_resources_dir() / "data"):
        p = base / name
        if p.is_file():
            return p
    raise FileNotFoundError(
        f"找不到資料檔：data/{name}（已搜尋 {_app_base_dir() / 'data'} 與內建目錄）"
    )


def _load_json(relative: str) -> Any:
    path = resolve_data_path(relative)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class ExternalBundles:
    theme: dict[str, str]
    ui_i18n: dict[str, dict[str, str]]
    default_platform_key: str
    platform_presets: dict[str, dict[str, str]]
    wallet_currency_by_host: dict[str, str]
    ref_referral_commission_vnd: dict[int, int]
    commission_amt_vnd: dict[str, int]
    share_deposit_example_vnd: int
    win_base: int
    win_default: int
    win_max: int
    hope_min: int
    hope_step: int
    canvas_jackpot_grid_records: tuple[dict[str, Any], ...]
    canvas_jackpot_confirm_records: tuple[dict[str, int | str], ...]
    spin_ack_to_jackpot_sweep_delay_sec: float
    in_game_ai_marquee_interval_ms: int


def load_external_bundles() -> ExternalBundles:
    theme_raw = _load_json("theme.json")
    if not isinstance(theme_raw, dict):
        raise ValueError("theme.json 必須為物件")
    theme: dict[str, str] = {str(k): str(v) for k, v in theme_raw.items()}

    plat = _load_json("platform.json")
    if not isinstance(plat, dict):
        raise ValueError("platform.json 必須為物件")
    presets = plat.get("platform_presets")
    if not isinstance(presets, dict) or not presets:
        raise ValueError("platform.json 需含 platform_presets")
    platform_presets: dict[str, dict[str, str]] = {}
    for pk, pv in presets.items():
        if not isinstance(pv, dict):
            continue
        platform_presets[str(pk)] = {str(kk): str(vv) for kk, vv in pv.items()}
    default_key = str(plat.get("default_platform_key", "") or "").strip()
    if not default_key or default_key not in platform_presets:
        default_key = next(iter(platform_presets))
    wch = plat.get("wallet_currency_by_host") or {}
    wallet_currency_by_host: dict[str, str] = (
        {str(k): str(v) for k, v in wch.items()} if isinstance(wch, dict) else {}
    )
    ref_raw = plat.get("ref_referral_commission_vnd") or {}
    ref_referral_commission_vnd: dict[int, int] = {}
    if isinstance(ref_raw, dict):
        for k, v in ref_raw.items():
            try:
                ref_referral_commission_vnd[int(k)] = int(v)
            except (TypeError, ValueError):
                continue

    comm_raw = plat.get("commission_amt_vnd") or {}
    commission_amt_vnd: dict[str, int] = {}
    if isinstance(comm_raw, dict):
        for k, v in comm_raw.items():
            try:
                commission_amt_vnd[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
    if not commission_amt_vnd:
        commission_amt_vnd = {
            "30%": 180000,
            "20%": 120000,
            "10%": 60000,
            "4%": 24000,
            "3%": 18000,
            "2%": 12000,
            "1%": 6000,
        }

    share_deposit_example_vnd = int(plat.get("share_deposit_example_vnd", 600000))

    win_base = int(plat["win_base"])
    win_default = int(plat["win_default"])
    win_max = int(plat["win_max"])
    hope_min = int(plat.get("hope_min", plat["win_default"]))
    hope_min = max(0, min(hope_min, win_max))
    hope_step = int(plat["hope_step"])

    cj = _load_json("canvas_jackpot.json")
    if not isinstance(cj, dict):
        raise ValueError("canvas_jackpot.json 必須為物件")
    grid_raw = cj.get("grid")
    if not isinstance(grid_raw, list):
        raise ValueError("canvas_jackpot.json 需含 grid 陣列")
    grid: list[dict[str, Any]] = []
    for item in grid_raw:
        if isinstance(item, dict):
            grid.append(
                {
                    "ox": int(item["ox"]),
                    "oy": int(item["oy"]),
                    "note": str(item.get("note", "")),
                }
            )
    canvas_confirm_list: list[dict[str, int | str]] = []
    seq_raw = cj.get("confirm_sequence")
    if isinstance(seq_raw, list):
        for item in seq_raw:
            if isinstance(item, dict):
                canvas_confirm_list.append(
                    {
                        "ox": int(item["ox"]),
                        "oy": int(item["oy"]),
                        "note": str(item.get("note", "")),
                    }
                )
    if not canvas_confirm_list:
        confirm = cj.get("confirm")
        if not isinstance(confirm, dict):
            raise ValueError("canvas_jackpot.json 需含 confirm 物件或 confirm_sequence 陣列")
        canvas_confirm_list = [
            {
                "ox": int(confirm["ox"]),
                "oy": int(confirm["oy"]),
                "note": str(confirm.get("note", "")),
            }
        ]
    spin_ack = float(cj.get("spin_ack_to_jackpot_sweep_delay_sec", 3.0))
    marquee_ms = int(cj.get("in_game_ai_marquee_interval_ms", 1500))

    ui_i18n: dict[str, dict[str, str]] = {}
    for lang in ("zh-tw", "vi"):
        pack = _load_json(f"i18n/{lang}.json")
        if not isinstance(pack, dict):
            raise ValueError(f"i18n/{lang}.json 必須為物件")
        ui_i18n[lang] = {str(k): str(v) for k, v in pack.items()}

    return ExternalBundles(
        theme=theme,
        ui_i18n=ui_i18n,
        default_platform_key=default_key,
        platform_presets=platform_presets,
        wallet_currency_by_host=wallet_currency_by_host,
        ref_referral_commission_vnd=ref_referral_commission_vnd,
        commission_amt_vnd=commission_amt_vnd,
        share_deposit_example_vnd=share_deposit_example_vnd,
        win_base=win_base,
        win_default=win_default,
        win_max=win_max,
        hope_min=hope_min,
        hope_step=hope_step,
        canvas_jackpot_grid_records=tuple(grid),
        canvas_jackpot_confirm_records=tuple(canvas_confirm_list),
        spin_ack_to_jackpot_sweep_delay_sec=spin_ack,
        in_game_ai_marquee_interval_ms=marquee_ms,
    )


__all__ = ["ExternalBundles", "load_external_bundles", "resolve_data_path"]
