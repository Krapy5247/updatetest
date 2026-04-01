"""
自動更新（launcher 與主程式「更新」按鈕共用）。

優先讀取更新清單網址：
  1. 環境變數 UPDATE_MANIFEST_URL
  2. 安裝目錄 update_config.json 的 manifest_url
  3. 模組常數 DEFAULT_MANIFEST_URL（預設空字串）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

MAIN_SCRIPT = "test.py"
VERSION_MODULE = "version_info.py"
UPDATE_CONFIG_NAME = "update_config.json"

# 可改為預設 HTTPS 清單；通常改 update_config.json 即可
DEFAULT_MANIFEST_URL = ""

# 主程式「系統更新」按鈕：未設定環境變數／update_config.json 時，改抓本機 releases 清單
# （於 updatetest 上層執行：python -m http.server 8000，清單路徑 releases/manifest.json）
DEFAULT_LOCAL_RELEASES_MANIFEST_URL = "http://127.0.0.1:8000/releases/manifest.json"


def install_root() -> Path:
    """與 exe／腳本同層（可寫入 test.py、version_info.py）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_manifest_url(root: Path | None = None) -> str:
    """取得更新清單 URL；無則回傳空字串。"""
    env = (os.environ.get("UPDATE_MANIFEST_URL") or "").strip()
    if env:
        return env
    r = root if root is not None else install_root()
    cfg = r / UPDATE_CONFIG_NAME
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                u = str(data.get("manifest_url") or "").strip()
                if u:
                    return u
        except (OSError, json.JSONDecodeError):
            pass
    return (DEFAULT_MANIFEST_URL or "").strip()


def _parse_version_tuple(s: str) -> tuple[int, ...]:
    s = (s or "").strip()
    parts = re.findall(r"\d+", s)
    return tuple(int(p) for p in parts) if parts else (0,)


def version_less(a: str, b: str) -> bool:
    return _parse_version_tuple(a) < _parse_version_tuple(b)


def read_local_version(root: Path) -> str:
    path = root / VERSION_MODULE
    if not path.is_file():
        return "0.0.0"
    try:
        text = path.read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', text)
        return m.group(1).strip() if m else "0.0.0"
    except OSError:
        return "0.0.0"


def fetch_manifest(url: str) -> dict[str, Any] | None:
    if requests is None:
        print("[更新] 請先安裝：pip install requests", file=sys.stderr)
        return None
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"[更新] 無法取得更新清單: {e}", file=sys.stderr)
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest_path: Path) -> bool:
    if requests is None:
        return False
    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception as e:
        print(f"[更新] 下載失敗: {e}", file=sys.stderr)
        return False


def apply_update_test_py(root: Path, manifest: dict[str, Any]) -> bool:
    url = str(manifest.get("download_url") or manifest.get("url") or "").strip()
    if not url:
        print("[更新] 清單缺少 download_url", file=sys.stderr)
        return False
    expect_hash = str(manifest.get("sha256") or "").strip().lower()
    main_path = root / MAIN_SCRIPT
    fd, tmp_path = tempfile.mkstemp(prefix="upd_", suffix=".py", dir=str(root))
    os.close(fd)
    tmp_path = Path(tmp_path)
    try:
        if not download(url, tmp_path):
            return False
        if expect_hash and sha256_file(tmp_path) != expect_hash:
            print("[更新] SHA256 不符，已中止覆寫", file=sys.stderr)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        os.replace(tmp_path, main_path)
        print(f"[更新] 已更新 {MAIN_SCRIPT}")
        return True
    finally:
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def apply_update_version_info(root: Path, manifest: dict[str, Any]) -> None:
    url = str(manifest.get("version_info_url") or "").strip()
    if not url:
        return
    path = root / VERSION_MODULE
    fd, tmp_path = tempfile.mkstemp(prefix="ver_", suffix=".py", dir=str(root))
    os.close(fd)
    tmp_path = Path(tmp_path)
    try:
        if not download(url, tmp_path):
            return
        vh = str(manifest.get("version_info_sha256") or "").strip().lower()
        if vh and sha256_file(tmp_path) != vh:
            print("[更新] version_info.py SHA256 不符，略過", file=sys.stderr)
            return
        os.replace(tmp_path, path)
        print(f"[更新] 已更新 {VERSION_MODULE}")
    finally:
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def sync_version_info_from_manifest(root: Path, manifest: dict[str, Any]) -> None:
    """清單的 version 為準：若本機 APP_VERSION 仍與清單不符，改寫 version_info.py。

    用於 version_info_url 未提供或下載失敗（如 404）時，避免下次啟動仍判定需更新。
    """
    remote_ver = str(manifest.get("version") or "").strip()
    if not remote_ver:
        return
    if read_local_version(root) == remote_ver:
        return
    path = root / VERSION_MODULE
    if not path.is_file():
        try:
            path.write_text(
                f'# 版本號單一來源（launcher 與主程式共用）\nAPP_VERSION = "{remote_ver}"\n',
                encoding="utf-8",
            )
            print(f"[更新] 已建立 {VERSION_MODULE}，版本 {remote_ver}")
        except OSError as e:
            print(f"[更新] 無法建立 {VERSION_MODULE}: {e}", file=sys.stderr)
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[更新] 無法讀取 {VERSION_MODULE}: {e}", file=sys.stderr)
        return
    new_text = re.sub(
        r"^(\s*APP_VERSION\s*=\s*)[\"'][^\"']*[\"']",
        rf'\1"{remote_ver}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if new_text == text:
        new_text = re.sub(
            r"APP_VERSION\s*=\s*[\"'][^\"']*[\"']",
            f'APP_VERSION = "{remote_ver}"',
            text,
            count=1,
        )
    if new_text != text:
        try:
            path.write_text(new_text, encoding="utf-8")
            print(f"[更新] 已將 {VERSION_MODULE} 同步為清單版本 {remote_ver}")
        except OSError as e:
            print(f"[更新] 無法寫入 {VERSION_MODULE}: {e}", file=sys.stderr)


def check_and_apply_update(root: Path, manifest_url: str) -> tuple[str, str]:
    """檢查並套用更新。

    回傳 (status, message)：
      status: 'updated' | 'latest' | 'error'
    """
    if requests is None:
        return ("error", "缺少 requests 套件")
    if not manifest_url.strip():
        return ("error", "未設定更新清單網址")

    local_ver = read_local_version(root)
    man = fetch_manifest(manifest_url)
    if not man:
        return ("error", "無法取得更新清單")

    remote_ver = str(man.get("version") or "").strip()
    if not remote_ver:
        return ("error", "清單缺少 version")

    if not version_less(local_ver, remote_ver):
        return ("latest", local_ver)

    if not apply_update_test_py(root, man):
        return ("error", "下載或覆寫 test.py 失敗")
    apply_update_version_info(root, man)
    sync_version_info_from_manifest(root, man)
    return ("updated", remote_ver)


def launch_main_script(root: Path) -> int:
    """啟動主程式 test.py（開發模式）。"""
    main = root / MAIN_SCRIPT
    if not main.is_file():
        print(f"[啟動] 找不到 {main}", file=sys.stderr)
        return 1
    return subprocess.call([sys.executable, str(main)], cwd=str(root))


def launcher_main() -> int:
    """供 launcher.py 使用：先更新再啟動 test.py。"""
    if requests is None:
        print("請先安裝：pip install requests", file=sys.stderr)
        return 1

    root = install_root()
    if os.environ.get("SKIP_UPDATE", "").strip().lower() in ("1", "true", "yes"):
        print("[啟動] 已略過更新檢查 (SKIP_UPDATE)")
        return launch_main_script(root)

    url = get_manifest_url(root)
    if not url:
        print("[啟動] 未設定更新清單（update_config.json 或 UPDATE_MANIFEST_URL），直接啟動主程式")
        return launch_main_script(root)

    local_ver = read_local_version(root)
    man = fetch_manifest(url)
    if not man:
        print("[啟動] 無法取得更新資訊，仍嘗試啟動主程式")
        return launch_main_script(root)

    remote_ver = str(man.get("version") or "").strip()
    if not remote_ver:
        print("[啟動] 清單缺少 version，直接啟動主程式")
        return launch_main_script(root)

    if not version_less(local_ver, remote_ver):
        print(f"[啟動] 已是最新或較新 (本地 {local_ver} / 遠端 {remote_ver})")
        return launch_main_script(root)

    print(f"[更新] 發現新版本 {remote_ver} (目前 {local_ver})")
    if not apply_update_test_py(root, man):
        print("[啟動] 更新失敗，仍嘗試啟動目前版本")
        return launch_main_script(root)
    apply_update_version_info(root, man)
    sync_version_info_from_manifest(root, man)
    return launch_main_script(root)


if __name__ == "__main__":
    try:
        sys.exit(launcher_main())
    except KeyboardInterrupt:
        sys.exit(130)
