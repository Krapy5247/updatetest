"""
啟動流程：先依「單一清單」檢查更新 → 再啟動 test.py。

清單（manifest.json）由 update_config.json 的 manifest_url 指向；內含 version、
download_url（test.py）、選用 version_info_url、選用 extra_files（多檔）。

使用方式：python launcher.py

設定清單網址（擇一）：
  1. 同目錄 update_config.json 內 "manifest_url"
  2. 環境變數 UPDATE_MANIFEST_URL
  3. app_update.py 內 DEFAULT_MANIFEST_URL

SKIP_UPDATE=1 略過更新檢查。

範例見 update_manifest.example.json
"""
from __future__ import annotations

import sys

if __name__ == "__main__":
    try:
        from app_update import launcher_main

        sys.exit(launcher_main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        try:
            from app_update import _fatal_error, install_root

            _fatal_error(install_root(), "TreasureClawLauncher", e)
        except Exception:
            pass
        sys.exit(1)
