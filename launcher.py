"""
啟動流程：先檢查遠端版本 → 需要則下載並覆寫主程式 test.py → 再啟動 test.py。

使用方式（建議）：
  python launcher.py

設定更新清單網址（擇一）：
  1. 同目錄 update_config.json 內 "manifest_url"
  2. 環境變數 UPDATE_MANIFEST_URL
  3. 編輯 app_update.py 內 DEFAULT_MANIFEST_URL

環境變數：
  SKIP_UPDATE=1   略過更新檢查，直接啟動主程式

清單 JSON 範例見 update_manifest.example.json
"""
from __future__ import annotations

import sys

from app_update import launcher_main

if __name__ == "__main__":
    try:
        sys.exit(launcher_main())
    except KeyboardInterrupt:
        sys.exit(130)
