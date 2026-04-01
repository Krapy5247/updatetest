"""
自動更新（launcher 與主程式「更新」按鈕共用）。

PyInstaller onedir：test.py／version_info.py 的 OTA 覆寫寫入 sys._MEIPASS（_internal），
與實際載入路徑一致；update_config.json 仍讀 exe 同層 install_root()。
清單可選 extra_files：path 以 data/ 開頭者寫入 exe 同層（與 external_data 讀取順序一致）；
其餘寫入 _MEIPASS。

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
    """與 exe／腳本同層：config.json、update_config.json、launcher_crash.log 等。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_bundle_root() -> Path:
    """實際載入／覆寫 test.py、version_info.py 的目錄。打包後即 PyInstaller _MEIPASS（_internal）。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def migrate_legacy_root_updates_if_needed() -> None:
    """舊版 OTA 曾寫入 exe 同層；若該處檔案較新，併入 _internal 以免與執行路徑不一致。"""
    if not getattr(sys, "frozen", False) or not hasattr(sys, "_MEIPASS"):
        return
    ir = install_root()
    br = app_bundle_root()
    for name in (MAIN_SCRIPT, VERSION_MODULE):
        src = ir / name
        dst = br / name
        if not src.is_file():
            continue
        if not dst.is_file():
            try:
                import shutil

                shutil.copy2(src, dst)
                print(f"[遷移] 已將 {name} 自安裝目錄複製至程式內建目錄")
            except OSError as e:
                print(f"[遷移] 略過 {name}: {e}", file=sys.stderr)
            continue
        try:
            if src.stat().st_mtime <= dst.stat().st_mtime:
                continue
            import shutil

            shutil.copy2(src, dst)
            print(f"[遷移] 已將較新的 {name} 自安裝目錄合併至程式內建目錄")
        except OSError as e:
            print(f"[遷移] 略過 {name}: {e}", file=sys.stderr)


def resolve_main_script_path(root: Path) -> Path | None:
    """主程式路徑：打包後以 _MEIPASS 內為準（OTA 覆寫同一路徑）；另相容舊版 exe 同層。"""
    br = app_bundle_root() / MAIN_SCRIPT
    if br.is_file():
        return br
    legacy = root / MAIN_SCRIPT
    if legacy.is_file():
        return legacy
    return None


CRASH_LOG_NAME = "launcher_crash.log"


def _fatal_error(root: Path, title: str, exc: BaseException) -> None:
    """Windowed exe 無主控台時仍讓使用者看到錯誤，並寫入安裝目錄日誌。"""
    import traceback

    log = root / CRASH_LOG_NAME
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        root.mkdir(parents=True, exist_ok=True)
        log.write_text(tb, encoding="utf-8", errors="replace")
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            body = f"啟動失敗：{exc}\n\n詳情已寫入：\n{log}"
            if len(body) > 900:
                body = body[:897] + "..."
            ctypes.windll.user32.MessageBoxW(0, body, title, 0x10)
        except Exception:
            pass
    else:
        print(tb, file=sys.stderr)


def _fatal_msg(root: Path, title: str, message: str) -> None:
    log = root / CRASH_LOG_NAME
    try:
        root.mkdir(parents=True, exist_ok=True)
        log.write_text(message + "\n", encoding="utf-8", errors="replace")
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            body = f"{message}\n\n（{log}）"
            if len(body) > 900:
                body = body[:897] + "..."
            ctypes.windll.user32.MessageBoxW(0, body, title, 0x10)
        except Exception:
            pass
    else:
        print(message, file=sys.stderr)


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


def read_local_version(root: Path | None = None) -> str:
    r = root if root is not None else app_bundle_root()
    path = r / VERSION_MODULE
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
        # 縮短逾時，避免 windowed 下長時間無視窗像當機
        r = requests.get(url, timeout=(5, 12))
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


def apply_update_test_py(manifest: dict[str, Any]) -> bool:
    url = str(manifest.get("download_url") or manifest.get("url") or "").strip()
    if not url:
        print("[更新] 清單缺少 download_url", file=sys.stderr)
        return False
    expect_hash = str(manifest.get("sha256") or "").strip().lower()
    br = app_bundle_root()
    main_path = br / MAIN_SCRIPT
    fd, tmp_path = tempfile.mkstemp(prefix="upd_", suffix=".py", dir=str(br))
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


def _safe_bundle_relative_path(rel: str) -> Path | None:
    """僅允許相對路徑，阻擋 .. 與絕對路徑。實際寫入根目錄由 _extra_files_write_base 決定。"""
    s = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not s:
        return None
    parts = Path(s).parts
    if ".." in parts:
        return None
    return Path(*parts) if parts else None


def _extra_files_write_base(rel: Path) -> Path:
    """data/ 底下寫入 exe 同層（與 external_data 第一優先一致）；其餘寫 _MEIPASS。"""
    if rel.parts and str(rel.parts[0]).lower() == "data":
        return install_root()
    return app_bundle_root()


def apply_extra_files(manifest: dict[str, Any]) -> bool:
    """選用。extra_files: [ { "path": "data/i18n/zh-tw.json", "url": "https://...", "sha256": "" } ]"""
    raw = manifest.get("extra_files")
    if raw is None:
        return True
    if not isinstance(raw, list):
        print("[更新] extra_files 必須為陣列", file=sys.stderr)
        return False
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            print(f"[更新] extra_files[{i}] 必須為物件", file=sys.stderr)
            return False
        rel = _safe_bundle_relative_path(str(item.get("path") or ""))
        if rel is None:
            print(f"[更新] extra_files[{i}] path 非法或為空", file=sys.stderr)
            return False
        url = str(item.get("url") or "").strip()
        if not url:
            print(f"[更新] extra_files[{i}] 缺少 url", file=sys.stderr)
            return False
        expect_hash = str(item.get("sha256") or "").strip().lower()
        base = _extra_files_write_base(rel)
        base_resolved = base.resolve()
        dest = (base / rel).resolve()
        try:
            dest.relative_to(base_resolved)
        except ValueError:
            print(f"[更新] extra_files[{i}] path 超出允許目錄", file=sys.stderr)
            return False
        parent = dest.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix="ext_", suffix=".part", dir=str(parent))
        os.close(fd)
        tmp_path = Path(tmp_path)
        try:
            if not download(url, tmp_path):
                return False
            if expect_hash and sha256_file(tmp_path) != expect_hash:
                print(f"[更新] extra_files[{i}] SHA256 不符，已中止", file=sys.stderr)
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return False
            os.replace(tmp_path, dest)
            print(f"[更新] 已更新 {rel.as_posix()}")
        finally:
            if tmp_path.is_file():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
    return True


def apply_update_version_info(manifest: dict[str, Any]) -> None:
    url = str(manifest.get("version_info_url") or "").strip()
    if not url:
        return
    br = app_bundle_root()
    path = br / VERSION_MODULE
    fd, tmp_path = tempfile.mkstemp(prefix="ver_", suffix=".py", dir=str(br))
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


def sync_version_info_from_manifest(manifest: dict[str, Any]) -> None:
    """清單的 version 為準：若本機 APP_VERSION 仍與清單不符，改寫 version_info.py。

    用於 version_info_url 未提供或下載失敗（如 404）時，避免下次啟動仍判定需更新。
    """
    remote_ver = str(manifest.get("version") or "").strip()
    if not remote_ver:
        return
    if read_local_version() == remote_ver:
        return
    path = app_bundle_root() / VERSION_MODULE
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


def check_and_apply_update(manifest_url: str) -> tuple[str, str]:
    """檢查並套用更新（覆寫檔寫入 app_bundle_root，與 PyInstaller _internal 一致）。

    回傳 (status, message)：
      status: 'updated' | 'latest' | 'error'
    """
    if requests is None:
        return ("error", "缺少 requests 套件")
    if not manifest_url.strip():
        return ("error", "未設定更新清單網址")

    local_ver = read_local_version()
    man = fetch_manifest(manifest_url)
    if not man:
        return ("error", "無法取得更新清單")

    remote_ver = str(man.get("version") or "").strip()
    if not remote_ver:
        return ("error", "清單缺少 version")

    if not version_less(local_ver, remote_ver):
        return ("latest", local_ver)

    if not apply_update_test_py(man):
        return ("error", "下載或覆寫 test.py 失敗")
    if not apply_extra_files(man):
        return ("error", "下載或覆寫附加檔案失敗")
    apply_update_version_info(man)
    sync_version_info_from_manifest(man)
    return ("updated", remote_ver)


def launch_main_script(root: Path) -> int:
    """啟動主程式 test.py。開發模式用 python 子行程；PyInstaller 打包後 sys.executable 為 exe，改以 runpy 同程序載入。"""
    main = resolve_main_script_path(root)
    if main is None:
        extra = ""
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            extra = f" 或 {Path(sys._MEIPASS) / MAIN_SCRIPT}"
        _fatal_msg(
            root,
            "TreasureClawLauncher",
            f"找不到主程式：{app_bundle_root() / MAIN_SCRIPT}{extra}",
        )
        return 1
    if getattr(sys, "frozen", False):
        import runpy

        old_argv = sys.argv[:]
        old_cwd = os.getcwd()
        try:
            br = str(app_bundle_root())
            rp = str(root)
            if br not in sys.path:
                sys.path.insert(0, br)
            if rp not in sys.path:
                sys.path.insert(1, rp)
            os.chdir(str(root))
            sys.argv = [main.name]
            runpy.run_path(str(main), run_name="__main__")
            return 0
        except SystemExit as e:
            code = e.code
            if code is None:
                return 0
            if isinstance(code, int):
                return code
            return 1
        except Exception as e:
            _fatal_error(root, "TreasureClawLauncher", e)
            return 1
        finally:
            sys.argv = old_argv
            try:
                os.chdir(old_cwd)
            except OSError:
                pass
    return subprocess.call([sys.executable, str(main)], cwd=str(root))


def launcher_main() -> int:
    """供 launcher.py 使用：先更新再啟動 test.py。"""
    root = install_root()
    if requests is None:
        _fatal_msg(
            root,
            "TreasureClawLauncher",
            "建置缺少 requests 模組（pip install requests），請向開發者回報。",
        )
        return 1

    try:
        return _launcher_main_impl(root)
    except Exception as e:
        _fatal_error(root, "TreasureClawLauncher", e)
        return 1


def _launcher_main_impl(root: Path) -> int:
    migrate_legacy_root_updates_if_needed()

    if os.environ.get("SKIP_UPDATE", "").strip().lower() in ("1", "true", "yes"):
        print("[啟動] 已略過更新檢查 (SKIP_UPDATE)")
        return launch_main_script(root)

    url = get_manifest_url(root)
    if not url:
        print("[啟動] 未設定更新清單（update_config.json 或 UPDATE_MANIFEST_URL），直接啟動主程式")
        return launch_main_script(root)

    local_ver = read_local_version()
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
    if not apply_update_test_py(man):
        print("[啟動] 更新失敗，仍嘗試啟動目前版本")
        return launch_main_script(root)
    if not apply_extra_files(man):
        print("[啟動] 附加檔案更新失敗，仍嘗試啟動目前版本")
        return launch_main_script(root)
    apply_update_version_info(man)
    sync_version_info_from_manifest(man)
    return launch_main_script(root)


if __name__ == "__main__":
    try:
        sys.exit(launcher_main())
    except KeyboardInterrupt:
        sys.exit(130)
