from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlparse
import tkinter as tk
import random
import threading
import requests
from pathlib import Path
from tkinter import messagebox
from tkinter import ttk
import tkinter.font as tkfont
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    NoSuchElementException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
)
# PyInstaller / Nuitka：Selenium 4 延遲載入的子模組需顯式 import，否則打包後執行會缺模組
import selenium.webdriver.chrome.webdriver  # noqa: F401
import selenium.webdriver.chromium.webdriver  # noqa: F401
from webdriver_manager.chrome import ChromeDriverManager

try:
    from PIL import Image, ImageSequence, ImageTk  # type: ignore[import-untyped]
except ImportError:
    Image = ImageSequence = ImageTk = None  # type: ignore[misc, assignment]

from version_info import APP_VERSION  # 版本號請改 version_info.py（與 launcher 共用）
from external_data import load_external_bundles
from app_update import check_and_apply_update, get_manifest_url


print("test.py 執行中 04011316 git")

_E = load_external_bundles()
_t = _E.theme
LOGIN_UI_BG = _t["LOGIN_UI_BG"]
LOGIN_CARD_BG = _t["LOGIN_CARD_BG"]
LOGIN_CARD_INNER = _t["LOGIN_CARD_INNER"]
LOGIN_CARD_BORDER = _t["LOGIN_CARD_BORDER"]
LOGIN_FG = _t["LOGIN_FG"]
LOGIN_FG_MUTED = _t["LOGIN_FG_MUTED"]
LOGIN_ENTRY_BG = _t["LOGIN_ENTRY_BG"]
LOGIN_ENTRY_HL = _t["LOGIN_ENTRY_HL"]
LOGIN_BTN_BG = _t["LOGIN_BTN_BG"]
LOGIN_BTN_BG_ACTIVE = _t["LOGIN_BTN_BG_ACTIVE"]
LOGIN_REV_GREEN = _t["LOGIN_REV_GREEN"]
LOGIN_NOTE_ORANGE = _t["LOGIN_NOTE_ORANGE"]
LOGIN_FONT_FAMILY = _t["LOGIN_FONT_FAMILY"]
MAIN_UI_BG = _t["MAIN_UI_BG"]
MAIN_CARD_BG = _t["MAIN_CARD_BG"]
MAIN_CARD_BORDER = _t["MAIN_CARD_BORDER"]
MAIN_FG = _t["MAIN_FG"]
MAIN_FG_MUTED = _t["MAIN_FG_MUTED"]
MAIN_ACCENT = _t["MAIN_ACCENT"]
MAIN_BTN_BG = _t["MAIN_BTN_BG"]
MAIN_BTN_ACTIVE_BG = _t["MAIN_BTN_ACTIVE_BG"]
MAIN_MARQUEE_BG = _t["MAIN_MARQUEE_BG"]
MAIN_ENTRY_BG = _t["MAIN_ENTRY_BG"]
MAIN_SECTION_BG = _t["MAIN_SECTION_BG"]

CONFIG_FILE = "config.json"
# 介面語言：預設 zh-tw；可於 config.json 設定 "ui_language": "vi"，或登入頁下拉選單切換
UI_LANG_DEFAULT = "vi"
LOTTERY_RECORD_FILE = "lottery_record.json"
FACEBOOK_COOKIES_FILE = "facebook_cookies.json"
FB_REGISTRATION_RECORD_FILE = "fb_registration_record.json"
# FB 註冊：本機任意一次執行後，須間隔至少此秒數才會再跑（不分平台帳號）；目前為 3 小時
FB_REGISTRATION_INTERVAL_SEC = 3 * 60 * 60  # 10800 秒
# 登入／主畫面頂部媒體：GIF 檔名（實際路徑為 data/ 下，見 resolve_data_asset）
LOGIN_MEDIA_CANDIDATES = ("VN.gif",)
# 七階獎金說明下方表格圖（實際路徑為 data/ 下）
TIER_BONUS_TABLE_IMAGE = "VN.jpg"
# 主題色見 data/theme.json；平台／金額見 data/platform.json
REF_REFERRAL_COMMISSION_VND: dict[int, int] = _E.ref_referral_commission_vnd
WALLET_CURRENCY_BY_HOST: dict[str, str] = _E.wallet_currency_by_host
DEFAULT_PLATFORM_KEY = _E.default_platform_key
PLATFORM_PRESETS: dict[str, dict[str, str]] = _E.platform_presets

# 目前作用中平台（與登入頁選項／config platform 同步；所有訪客網址／幣別皆由此推導）
_ACTIVE_PLATFORM_KEY: str = DEFAULT_PLATFORM_KEY


def normalize_platform_key(raw: object) -> str:
    s = str(raw or "").strip() if raw is not None else ""
    if s in PLATFORM_PRESETS:
        return s
    return DEFAULT_PLATFORM_KEY


def apply_platform_key(key: str) -> str:
    """套用平台：更新 _ACTIVE_PLATFORM_KEY 與 API.BASE；回傳實際使用的 key。"""
    global _ACTIVE_PLATFORM_KEY
    k = normalize_platform_key(key)
    _ACTIVE_PLATFORM_KEY = k
    d = PLATFORM_PRESETS[k]
    API.BASE = d["api_origin"]
    return k


def get_guest_url() -> str:
    """目前作用中平台的遊戲訪客頁（隨平台切換而變）。"""
    return PLATFORM_PRESETS[_ACTIVE_PLATFORM_KEY]["guest_url"]


def get_site_host() -> str:
    """目前作用中平台主機名（小寫），供幣別對照。"""
    return (urlparse(get_guest_url()).hostname or "").lower()


def get_wallet_currency_code() -> str:
    return WALLET_CURRENCY_BY_HOST.get(get_site_host(), "")


def format_wallet_balance_display(raw: object) -> str:
    """錢包餘額顯示用（加幣別）；內部 _dashboard_data['balance'] 仍為 API 原始數字字串。"""
    s = str(raw).strip()
    if not s or s == "—":
        return s
    code = get_wallet_currency_code()
    if code:
        return f"{s} {code}"
    return s


def site_origin_base_url() -> str:
    """由目前作用中訪客頁網址取得站台 origin（scheme + host），供組出 /member/... 等絕對網址。"""
    p = urlparse(get_guest_url())
    if not p.netloc:
        return ""
    scheme = p.scheme or "https"
    return f"{scheme}://{p.netloc}"


# 會員中心 — 獎勵／領獎頁（路徑相對於 site_origin_base_url()）
MEMBER_AWARD_CENTER_PATH = "/member/awardCenterPoint"
# 遊戲列表頁（無領獎按鈕或領獎後需回此頁時使用）
SITE_GAMES_PATH = "/site/games"
# gamePoint「領獎」按鈕 data-url 內含此片段即視為可點（item 後方 id 為變數）
GAME_POINT_REWARD_URL_MARKER = "/member/ajaxGamePointReward/item/"


def try_click_swal2_confirm_ok(driver: webdriver.Chrome, timeout_sec: float = 6.0) -> bool:
    """若有 SweetAlert2 確認鈕（.swal2-confirm，常為 OK）則點擊並回傳 True。"""
    deadline = time.time() + max(0.5, timeout_sec)
    while time.time() < deadline:
        try:
            driver.switch_to.default_content()
            for sel in ("button.swal2-confirm.swal2-styled", "button.swal2-confirm"):
                for el in driver.find_elements(By.CSS_SELECTOR, sel):
                    try:
                        if not el.is_displayed():
                            continue
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        time.sleep(0.08)
                        try:
                            el.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        print("[領獎] 已點 SweetAlert 確認（OK）")
                        time.sleep(0.35)
                        return True
                    except Exception:
                        continue
        except Exception:
            pass
        time.sleep(0.2)
    return False


FB_HOME_URL = "https://www.facebook.com/"
# API GetFBSocieLink 使用的國別（與 get_group_link 一致）
FB_GROUP_LINK_COUNTRY = "VN"
# 精準局數：開局前抓 betCount；每局 SPIN 後輪詢 API 直到 betCount 增加
BETCOUNT_BASELINE_FETCH_RETRIES = 8
BETCOUNT_BASELINE_FETCH_SLEEP_SEC = 0.75
BETCOUNT_SPIN_ACK_TIMEOUT_SEC = 5.0
BETCOUNT_SPIN_ACK_INTERVAL_SEC = 1
# 大獎／全屏遮罩座標與 timing：data/canvas_jackpot.json
CANVAS_JACKPOT_GRID_RECORDS: tuple[dict, ...] = _E.canvas_jackpot_grid_records
CANVAS_JACKPOT_CONFIRM_RECORDS: tuple[dict[str, int | str], ...] = (
    _E.canvas_jackpot_confirm_records
)
SPIN_ACK_TO_JACKPOT_SWEEP_DELAY_SEC = _E.spin_ack_to_jackpot_sweep_delay_sec
IN_GAME_AI_MARQUEE_INTERVAL_MS = _E.in_game_ai_marquee_interval_ms


def app_base_dir() -> Path:
    """設定／Cookie 等檔案目錄（打包後為 exe 同層）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_resources_dir() -> Path:
    """PyInstaller `--add-data` 打包的檔案執行時位於此目錄（`sys._MEIPASS`）；開發時等同程式目錄。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def resolve_data_asset(filename: str) -> Path | None:
    """取得 data/ 內資源（openclaw.ico、VN.gif、VN.jpg 等）：先 exe／腳本同層 data/，再 PyInstaller 內建 data/。"""
    name = (filename or "").strip().replace("\\", "/").lstrip("/")
    if not name or ".." in name.split("/"):
        return None
    seen: set[Path] = set()
    for base in (app_base_dir(), bundled_resources_dir()):
        try:
            r = base.resolve()
        except OSError:
            continue
        if r in seen:
            continue
        seen.add(r)
        p = r / "data" / name
        if p.is_file():
            return p
    return None


def launch_exe_elevated_windows(exe_path: Path, cwd: Path) -> bool:
    """以「以系統管理員身分執行」啟動程式（ShellExecuteW verb=runas）。成功回傳 True。

    用於更新程式需寫入受保護路徑時，僅對 updater 提權；主程式本身維持一般權限，避免 Chrome/Selenium 異常。
    """
    if sys.platform != "win32":
        return False
    import ctypes

    exe_path = exe_path.resolve()
    cwd = cwd.resolve()
    ret = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        str(exe_path),
        None,
        str(cwd),
        1,
    )
    try:
        return int(ret) > 32
    except (TypeError, ValueError):
        return False


def chrome_options_suppress_prompts(options: Options) -> None:
    """關閉／降低 Chrome 內建干擾：儲存密碼、網站通知權限、還原工作階段氣泡、預設瀏覽器詢問等。

    注意：勿使用 --disable-infobars；新版 Chrome 下該參數可能反而導致「正受自動化軟體控制」提示列出現。
    """
    options.add_experimental_option(
        "prefs",
        {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
            "profile.default_content_setting_values.notifications": 2,
        },
    )
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-session-crashed-bubble")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")


def chrome_options_hide_automation_infobar(options: Options) -> None:
    """隱藏「Chrome 正受自動化軟體控制」提示列：排除 enable-automation 等開關。"""
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)


def chrome_driver_patch_automation_detection(driver: webdriver.Chrome) -> None:
    """每個新文件載入時覆寫 navigator.webdriver，減少網頁偵測（與提示列搭配使用）。"""
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"},
        )
    except Exception:
        pass


WIN_BASE = _E.win_base
WIN_DEFAULT = _E.win_default
WIN_MAX = _E.win_max
HOPE_MIN = _E.hope_min
HOPE_STEP = _E.hope_step
# 每輪 worker 週期結束後休息秒數；0＝不休息
WORKER_CYCLE_REST_SEC = 0

UI_I18N: dict[str, dict[str, str]] = _E.ui_i18n


def load_ui_guide_sections(lang: str) -> tuple[str, str, str]:
    """登入頁三段說明（系統／操作／預計收益）皆取自 UI_I18N。"""
    code = lang if lang in UI_I18N else UI_LANG_DEFAULT
    pack = UI_I18N[code]
    return pack["system_intro"], pack["guide_ops"], pack["guide_revenue"]


class API:
    """即時資訊 API"""
    BASE = PLATFORM_PRESETS[DEFAULT_PLATFORM_KEY]["api_origin"]

    @staticmethod
    def get_user_info(username: str) -> dict:
        """取得會員即時資訊；失敗或業務錯誤時回傳 {}"""
        try:
            r = requests.post(
                f"{API.BASE}/Api/Information",
                data={"username": username},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict):
                return {}
            code = data.get("code")
            if code is not None:
                try:
                    if int(code) != 200:
                        print(f"API Information 回傳 code={code}: {data.get('msg', '')}")
                        return {}
                except (TypeError, ValueError):
                    pass
            if "data" in data:
                inner = data["data"]
                if inner is None:
                    return {}
                return inner if isinstance(inner, dict) else {}
            return data
        except Exception as e:
            print(f"API 取得即時資訊失敗: {e}")
            return {}

    @staticmethod
    def user_info_looks_valid(info: dict, username: str) -> bool:
        """是否像成功取到會員資料（取不到則視為帳密或權限有誤）。"""
        if not info or not isinstance(info, dict):
            return False
        u = str(info.get("username", "")).strip()
        if u and u.lower() == (username or "").strip().lower():
            return True
        for key in ("level", "betCount", "balance", "promo_code", "downline", "event_downline"):
            v = info.get(key)
            if v is None:
                continue
            s = str(v).strip()
            if s and s != "—":
                return True
        return False

    @staticmethod
    def map_to_dashboard(api_data: dict) -> dict:
        """將 API 回傳對應到 _dashboard_data 欄位
        實際 API 欄位: username, level, betCount, balance, promo_code, downline, event_downline, update_at
        推薦人數 10/30/60/100 由 event_downline 計算並顯示百分比
        """
        out = {}
        out["level"] = str(api_data.get("level", "—"))
        out["game_count"] = str(api_data.get("betCount", "—"))
        out["balance"] = str(api_data.get("balance", "—"))
        out["referral_code"] = str(api_data.get("promo_code", "—"))
        out["downline_count"] = str(api_data.get("downline", "—"))
        out["active_count"] = str(api_data.get("event_downline", "—"))
        out["lottery_reward"] = str(api_data.get("lottery_reward", "—"))
        out["roulette_reward"] = str(api_data.get("roulette_reward", "—"))

        try:
            event_downline = int(api_data.get("event_downline", 0))
        except (ValueError, TypeError):
            event_downline = 0
        for target in [10, 30, 60, 100]:
            current = min(event_downline, target)
            pct = round(current / target * 100) if target > 0 else 0
            out[f"ref_{target}_pct"] = f"{current}/{target} ({pct}%)"

        out["challenge_pct"] = str(api_data.get("balance", "—"))  # 挑戰關卡的錢 = 錢包餘額
        out["commission"] = str(api_data.get("commission", "—"))
        lt_raw = api_data.get("lotteryTime")
        out["lottery_time"] = (str(lt_raw).strip() if lt_raw is not None else "") or "—"
        try:
            out["lottery_number"] = int(float(str(api_data.get("lotteryNumber", 0)).strip()))
        except (ValueError, TypeError):
            out["lottery_number"] = 0
        return out

    @staticmethod
    def parse_bet_count(info: dict) -> int | None:
        """從 get_user_info 回傳的 dict 解析 betCount；無法解析時 None。"""
        if not info or not isinstance(info, dict):
            return None
        raw = info.get("betCount")
        if raw is None:
            return None
        try:
            return int(str(raw).strip().replace(",", "").replace(" ", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def lottery_bet(username: str, number: int) -> dict:
        """玩樂透，number 為 00~99 的字串"""
        try:
            r = requests.post(
                f"{API.BASE}/Api/lotteryApi",
                data={"username": username, "number": number},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and "data" in data:
                return data["data"]
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"API 玩樂透失敗: {e}")
            return {}

    @staticmethod
    def get_group_link(country: str) -> str | None:
        """取得 Facebook 社團／群組連結 URL（GetFBSocieLink）；失敗回傳 None。"""
        try:
            r = requests.post(
                "https://www.gamer16888.com/Api/GetFBSocieLink",
                data={"country": country, "type": "1"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                u = data.get("url")
                inner = data.get("data")
                if not u and isinstance(inner, dict):
                    u = inner.get("url")
                if u:
                    return str(u).strip()
        except Exception as e:
            print(f"API 取得群組連結失敗: {e}")
        return None
    @staticmethod
    def save_downloadaccount(username,platform):
        r = requests.post('https://www.gamer16888.com/api/openclawData',
        data={'account':username,'platform':platform},
        timeout=10,
        )
        return r.text


apply_platform_key(DEFAULT_PLATFORM_KEY)

# =============================================================================
# Facebook 帳號註冊（原 FBcreate/test.py，已合併至此檔）
# =============================================================================
FB_CREATION_WAIT_TIMEOUT = 15
FB_MAIL_API_TIMEOUT = 30
FB_STEP_DELAY = (1, 2)
FB_TYPE_DELAY = (0.1, 0.2)


class FBCreationAPI:
    """FB 註冊用：信箱與回報 API（與遊戲 API 分開）"""
    BASE_MAIL = "http://35.194.178.176:8000/mail"
    BASE_SAVE = "https://www.gamer16888.com/api/fbaccountcreation"

    @staticmethod
    def getmail():
        r = requests.post(FBCreationAPI.BASE_MAIL, data={"type": "1"}, timeout=FB_MAIL_API_TIMEOUT)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def getcode(account, password, domain):
        r = requests.post(
            FBCreationAPI.BASE_MAIL,
            data={"type": "2", "account": account, "password": password, "domain": domain},
            timeout=FB_MAIL_API_TIMEOUT,
        )
        return r.text

    @staticmethod
    def savemail(account, password, name, status):
        r = requests.post(
            FBCreationAPI.BASE_SAVE,
            data={"account": account, "password": password, "name": name, "status": status},
            timeout=FB_MAIL_API_TIMEOUT,
        )
        return r.text


class FBNameGenerator:
    """各國女性姓名 (姓, 名)"""

    @staticmethod
    def get_name(country_code: str):
        names = {
            "VN": [
                ("Nguyễn", "Thị Hương"),
                ("Trần", "Thị Mai"),
                ("Lê", "Thanh Huyền"),
                ("Phạm", "Ngọc Lan"),
                ("Hoàng", "Minh Tú"),
                ("Vũ", "Thị Hồng"),
                ("Đặng", "Bích Thủy"),
                ("Bùi", "Phương Thảo"),
                ("Đỗ", "Thanh Hà"),
                ("Hồ", "Thị Liên"),
                ("Ngô", "Trúc Anh"),
                ("Dương", "Kim Chi"),
                ("Lý", "Mỹ Linh"),
                ("Trương", "Quỳnh Dao"),
                ("Phan", "Thu Cúc"),
                ("Nguyễn", "Minh Nguyệt"),
                ("Trịnh", "Thanh Vân"),
                ("Lưu", "Diệu Hiền"),
                ("Mai", "Tuyết Nhung"),
                ("Võ", "Hạnh Phúc"),
                ("Đào", "Gia Hân"),
                ("Huỳnh", "Thục Quyên"),
                ("Lương", "Khánh An"),
                ("Phùng", "Yến Nhi"),
                ("Tô", "Hải Yến"),
                ("Quách", "Ái Linh"),
                ("Đường", "Lệ Hằng"),
                ("Thái", "Như Ý"),
                ("Hà", "Cẩm Tú"),
                ("Cao", "Ngọc Trâm"),
            ],
            "IN": [
                ("Sharma", "Ananya"),
                ("Patel", "Ishani"),
                ("Singh", "Kavya"),
                ("Kumar", "Priyanka"),
                ("Das", "Saanvi"),
                ("Gupta", "Riya"),
                ("Reddy", "Deepika"),
                ("Mehta", "Aavya"),
                ("Iyer", "Lakshmi"),
                ("Khan", "Zoya"),
                ("Joshi", "Tanvi"),
                ("Nair", "Aditi"),
                ("Malhotra", "Sanya"),
                ("Verma", "Ishita"),
                ("Chopra", "Parineeti"),
                ("Rao", "Sushma"),
                ("Bose", "Nandini"),
                ("Mishra", "Shweta"),
                ("Yadav", "Pooja"),
                ("Kulkarni", "Megha"),
                ("Desai", "Amrita"),
                ("Chatterjee", "Bipasha"),
                ("Pandey", "Sneha"),
                ("Saxena", "Kriti"),
                ("Agarwal", "Bhavna"),
                ("Shah", "Drishti"),
                ("Kapoor", "Kiara"),
                ("Thakur", "Jyoti"),
                ("Bhardwaj", "Rashmi"),
                ("Pillai", "Gayatri"),
            ],
            "US": [
                ("Smith", "Olivia"),
                ("Johnson", "Emma"),
                ("Williams", "Ava"),
                ("Brown", "Sophia"),
                ("Jones", "Isabella"),
                ("Miller", "Mia"),
                ("Davis", "Charlotte"),
                ("Garcia", "Amelia"),
                ("Rodriguez", "Harper"),
                ("Wilson", "Evelyn"),
                ("Martinez", "Abigail"),
                ("Anderson", "Emily"),
                ("Taylor", "Elizabeth"),
                ("Thomas", "Mila"),
                ("Hernandez", "Ella"),
                ("Moore", "Avery"),
                ("Martin", "Sofia"),
                ("Jackson", "Camila"),
                ("Thompson", "Aria"),
                ("White", "Scarlett"),
                ("Lopez", "Victoria"),
                ("Lee", "Madison"),
                ("Gonzalez", "Luna"),
                ("Harris", "Grace"),
                ("Clark", "Chloe"),
                ("Lewis", "Penelope"),
                ("Robinson", "Layla"),
                ("Walker", "Riley"),
                ("Young", "Zoey"),
                ("Hall", "Lily"),
                ("Bailey", "Alice"),
                ("Bennett", "Autumn"),
                ("Brooks", "Adeline"),
                ("Butler", "Arianna"),
                ("Coleman", "Aubrey"),
                ("Cooper", "Brielle"),
                ("Cox", "Cora"),
                ("Diaz", "Delilah"),
                ("Fisher", "Daisy"),
                ("Foster", "Eliana"),
                ("Gray", "Eloise"),
                ("Griffin", "Emery"),
                ("Hayes", "Eva"),
                ("Henderson", "Finley"),
                ("Howard", "Faith"),
                ("Hughes", "Freya"),
                ("James", "Genevieve"),
                ("Jenkins", "Georgia"),
                ("Kelly", "Hadley"),
                ("Long", "Iris"),
                ("Mason", "Ivy"),
                ("McDonald", "Jade"),
                ("Miller", "Josie"),
                ("Myers", "Juniper"),
                ("Nelson", "Keira"),
                ("Ortiz", "Laila"),
                ("Patterson", "Lia"),
                ("Perry", "Lila"),
                ("Peterson", "Lola"),
                ("Powell", "Lucia"),
                ("Price", "Lydia"),
                ("Reed", "Mabel"),
                ("Richardson", "Margot"),
                ("Ross", "Melody"),
                ("Russell", "Nora"),
                ("Sanders", "Olive"),
                ("Simmons", "Piper"),
                ("Stevens", "Quinn"),
                ("Sullivan", "Raegan"),
                ("Vargas", "Reese"),
                ("Wallace", "Remi"),
                ("Ward", "Rose"),
                ("Watson", "Sienna"),
                ("Webb", "Sloane"),
                ("Wells", "Summer"),
                ("West", "Tessa"),
                ("Wood", "Thea"),
                ("Abbott", "Valerie"),
                ("Black", "Vera"),
                ("Bowman", "Vivian"),
                ("Bryant", "Willa"),
                ("Burke", "Zara"),
                ("Castillo", "Zoe"),
                ("Curtis", "Amara"),
                ("Douglas", "Anaya"),
                ("Elliott", "Callie"),
                ("Fleming", "Dahlia"),
                ("Graham", "Felicity"),
                ("Hansen", "Gemma"),
                ("Knight", "Harlow"),
            ],
        }
        cu = country_code.upper()
        if cu in names:
            return random.choice(names[cu])
        keys = list(names.keys())
        return random.choice(names[random.choice(keys)])


def fb_human_type(element, text: str) -> None:
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(*FB_TYPE_DELAY))


def fb_smart_click(driver, wait: WebDriverWait, xpath: str, desc: str = "按鈕") -> bool:
    try:
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        time.sleep(random.uniform(*FB_STEP_DELAY))
        try:
            ActionChains(driver).move_to_element(btn).click().perform()
        except Exception:
            driver.execute_script("arguments[0].click();", btn)
        print(f"[FB] 成功點：{desc}")
        return True
    except Exception as e:
        print(f"[FB] 點 {desc} 失敗: {e}")
        return False


def fb_sync_birthday(driver, wait: WebDriverWait, date_str: str) -> None:
    el = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='date']")))
    js = """
    var el = arguments[0]; var val = arguments[1];
    var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(el, val);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    """
    driver.execute_script(js, el, date_str)
    print(f"[FB] 生日: {date_str}")


def _fb_account_log_path() -> Path:
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "account_log.txt"


def fb_default_savename() -> str:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).stem
    return Path(__file__).stem


def run_fb_account_registration(country: str, savename: str) -> None:
    """Facebook 手機版註冊流程（Headless 專用 Chrome 實例，與遊戲瀏覽器分開）。

    savename：傳給 FBCreationAPI.savemail 的 name，主程式使用平台登入的 username。
    """
    mail_info = FBCreationAPI.getmail()
    email_pwd = mail_info["password"]
    email_acc = mail_info["account"]
    email_domain = mail_info["domain"]
    print(f"[FB] acc: {email_acc} domain: {email_domain}")
    email_addr = f"{email_acc}@{email_domain}"
    ln, fn = FBNameGenerator.get_name(country)

    opts = Options()
    opts.add_argument("--mute-audio")
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=390,844")
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 19_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.2 Mobile/15E148 Safari/604.1"
    opts.add_argument(f"user-agent={ua}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options_hide_automation_infobar(opts)
    chrome_options_suppress_prompts(opts)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    chrome_driver_patch_automation_detection(driver)
    wait = WebDriverWait(driver, FB_CREATION_WAIT_TIMEOUT)

    try:
        driver.get("https://m.facebook.com/")
        fb_smart_click(driver, wait, "//div[@aria-label='建立新帳號']", "建立帳號")
        time.sleep(7)
        fb_smart_click(driver, wait, "//div[@aria-label='建立新帳號']", "建立帳號")
        wait.until(EC.url_to_be("https://m.facebook.com/reg/#"))

        time.sleep(7)
        fb_human_type(wait.until(EC.presence_of_element_located((By.XPATH, "//input[@aria-label='姓氏']"))), ln)
        fb_human_type(driver.find_element(By.XPATH, "//input[@aria-label='名字']"), fn)
        fb_smart_click(driver, wait, "//div[@aria-label='下一步']", "下一步(姓名)")

        time.sleep(7)
        bday = f"{random.randint(1992, 2005)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
        fb_sync_birthday(driver, wait, bday)
        fb_smart_click(driver, wait, "//div[@aria-label='下一步']", "下一步(生日)")

        time.sleep(7)
        fb_smart_click(driver, wait, "//div[@aria-label='女性' and @role='radio']", "選擇女性")
        time.sleep(5)
        fb_smart_click(driver, wait, "//div[@aria-label='下一步']", "下一步(性別)")

        time.sleep(7)
        fb_smart_click(driver, wait, "//div[@aria-label='使用電子郵件地址註冊']", "切換信箱")
        mail_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@aria-label='電子郵件地址']")))
        fb_human_type(mail_input, email_addr)
        fb_smart_click(driver, wait, "//div[@aria-label='下一步']", "下一步(信箱)")

        time.sleep(7)
        pwd_input = wait.until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='password' or @aria-label='密碼']"))
        )
        fb_human_type(pwd_input, f"{email_pwd}")
        fb_smart_click(driver, wait, "//div[@aria-label='下一步']", "完成註冊下一步")

        time.sleep(7)
        fb_smart_click(driver, wait, "//div[@aria-label='儲存']", "儲存")
        time.sleep(10)

        time.sleep(7)
        fb_smart_click(driver, wait, "//div[@aria-label='我同意']", "我同意")
        time.sleep(40)

        time.sleep(7)
        mailcode = FBCreationAPI.getcode(email_acc, email_pwd, email_domain)
        fb_human_type(wait.until(EC.presence_of_element_located((By.XPATH, "//input[@aria-label='確認碼']"))), mailcode)
        time.sleep(5)
        fb_smart_click(driver, wait, "//div[@aria-label='下一步']", "下一步(確認碼)")
        time.sleep(15)

        target_url_1 = "https://m.facebook.com/"
        target_url_2 = "https://m.facebook.com/gettingstarted/notifications/"
        max_retries = 5
        success = False
        for i in range(max_retries):
            try:
                WebDriverWait(driver, 10).until(
                    lambda d: d.current_url == target_url_1 or d.current_url == target_url_2
                )
                print(f"[FB] 第 {i + 1} 次檢查：網址正確")
                time.sleep(3)
                if i == max_retries - 1:
                    success = True
            except Exception:
                print(f"[FB] 第 {i + 1} 次重試")
                if i == max_retries - 1:
                    break

        log_path = _fb_account_log_path()
        if success:
            print("[FB] 網址穩")
            log_entry = f"Account: {email_acc}@{email_domain} | PWD: {email_pwd}\n"
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(log_entry)
                print(f"[FB] 已寫入 {log_path}")
                FBCreationAPI.savemail(email_addr, email_pwd, savename, "Success")
            except Exception as e:
                print(f"[FB] 寫入失敗：{e}")
                FBCreationAPI.savemail(email_addr, email_pwd, savename, "Success")
        else:
            print("[FB] 確認失敗：網址未穩定")
            FBCreationAPI.savemail(email_addr, email_pwd, savename, "Fail")
    except Exception as e:
        print(f"[FB] simu err: {e}")
        FBCreationAPI.savemail(email_addr, email_pwd, savename, "Fail")
    finally:
        driver.quit()


def _fb_registration_record_path() -> Path:
    return app_base_dir() / FB_REGISTRATION_RECORD_FILE


def _load_fb_registration_record() -> dict:
    path = _fb_registration_record_path()
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _effective_last_fb_registration_ts(data: dict) -> float | None:
    """全域上次註冊時間：優先 last_run_unix；舊版 last_by_user 則取其中最大值。"""
    raw = data.get("last_run_unix")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    by_user = data.get("last_by_user")
    if isinstance(by_user, dict) and by_user:
        try:
            return max(float(v) for v in by_user.values())
        except (TypeError, ValueError):
            pass
    return None


def _save_fb_registration_last_run(unix_ts: float) -> None:
    path = _fb_registration_record_path()
    try:
        data = _load_fb_registration_record()
        data["last_run_unix"] = unix_ts
        # 舊欄位不再使用，寫回時移除以免誤解
        data.pop("last_by_user", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[FB 註冊] 無法寫入本地紀錄 {path}: {e}")


def _fb_registration_seconds_until_next() -> float | None:
    """未滿間隔時回傳尚須等待的秒數；可執行則 None。"""
    last_ts = _effective_last_fb_registration_ts(_load_fb_registration_record())
    if last_ts is None:
        return None
    elapsed = time.time() - last_ts
    if elapsed >= FB_REGISTRATION_INTERVAL_SEC:
        return None
    return FB_REGISTRATION_INTERVAL_SEC - elapsed


def run_fb_registration_in_background(username: str) -> None:
    """背景執行一輪 FB 註冊（供「啟動」呼叫）。savemail 的 name 固定為平台 username。
    與帳號無關：本機最近一次執行後須間隔 FB_REGISTRATION_INTERVAL_SEC 才會再跑。"""
    name = (username or "").strip()
    if not name:
        print("[FB] 略過註冊：未取得 username")
        return

    wait_sec = _fb_registration_seconds_until_next()
    if wait_sec is not None:
        m, s = divmod(int(max(0, wait_sec)), 60)
        h, m = divmod(m, 60)
        interval_h = FB_REGISTRATION_INTERVAL_SEC // 3600
        print(
            f"[FB 註冊] 距上次執行未滿 {interval_h} 小時，略過（約 {h} 小時 {m} 分 {s} 秒後可再執行）"
        )
        return

    def job() -> None:
        try:
            print(f"[FB] savemail name（username）: {name}")
            run_fb_account_registration("US", name)
        except Exception as e:
            print(f"[FB 註冊] 錯誤: {e}")
        finally:
            _save_fb_registration_last_run(time.time())

    threading.Thread(target=job, daemon=True).start()


class LoginApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{UI_I18N[UI_LANG_DEFAULT]['app_title']} — v{APP_VERSION}")
        self.root.geometry("600x960")

        self.main_container = tk.Frame(self.root)
        self.main_container.pack(expand=True, fill="both")

        self.userinfo: dict[str, str] = {}
        self._driver: webdriver.Chrome | None = None
        self._wait: WebDriverWait | None = None
        self._is_platform_running = False
        self._worker_running = False
        self._worker_thread: threading.Thread | None = None
        self._stop_requested = False
        self._fb_driver: webdriver.Chrome | None = None
        self._login_media_after_id: str | None = None
        self._login_gif_resize_after_id: str | None = None
        self._login_gif_box: tk.Frame | None = None
        self._login_gif_last_layout_w: int = 0
        self._ai_game_marquee_active = False
        self._ai_game_marquee_after_id: str | None = None
        self._ai_show_ready_until_start = False
        self._main_banner_photo = None
        self._main_scroll_canvas: tk.Canvas | None = None

        self.show_login_frame()

    @staticmethod
    def _normalize_ui_language(code: object) -> str:
        lang = str(code or UI_LANG_DEFAULT).strip().lower().replace("_", "-")
        if lang in ("zh", "zh-hant", "tw"):
            lang = "zh-tw"
        if lang not in UI_I18N:
            lang = UI_LANG_DEFAULT
        return lang

    @staticmethod
    def _normalize_hope_amount(raw: object) -> int | None:
        """config 內的希望金額轉為合法整數；無效則回傳 None。"""
        if raw is None:
            return None
        try:
            if isinstance(raw, (int, float)):
                v = int(raw)
            else:
                v = int(str(raw).replace(",", "").strip())
        except (ValueError, TypeError):
            return None
        if v < 0 or v > WIN_MAX:
            return None
        return max(HOPE_MIN, min(WIN_MAX, v))

    def _t(self, key: str, **kwargs) -> str:
        pack = UI_I18N.get(self._ui_lang) or UI_I18N[UI_LANG_DEFAULT]
        fb = UI_I18N[UI_LANG_DEFAULT]
        s = pack.get(key) or fb.get(key) or key
        if kwargs:
            try:
                return s.format(**kwargs)
            except (KeyError, ValueError):
                return s
        return s

    def _app_window_title(self) -> str:
        """視窗標題：應用名稱 + 版本號。"""
        return f"{self._t('app_title')} — v{APP_VERSION}"

    def _persist_language_pref(self) -> None:
        data = self.load_config()
        data["ui_language"] = self._ui_lang
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"儲存語言設定失敗: {e}")

    def _persist_platform_pref(self) -> None:
        data = self.load_config()
        data["platform"] = getattr(self, "_platform_key", DEFAULT_PLATFORM_KEY)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"儲存平台設定失敗: {e}")

    def _guest_site_url(self) -> str:
        """目前選擇平台對應的遊戲訪客頁；同步 API.BASE（與 driver.get 一致）。"""
        key = normalize_platform_key(getattr(self, "_platform_key", DEFAULT_PLATFORM_KEY))
        apply_platform_key(key)
        return get_guest_url()

    def _persist_hope_amount(self, event: object | None = None) -> None:
        """將希望金額寫入 config.json（與帳密、語言同檔）。"""
        if not hasattr(self, "_var_hope_amount"):
            return
        try:
            params = self._get_game_params()
            amt = params["win_amount"]
        except Exception:
            return
        data = self.load_config()
        data["hope_amount"] = amt
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"儲存希望金額失敗: {e}")
        try:
            self._update_extra_balance_line()
        except Exception:
            pass

    def _hope_amount_int(self) -> int:
        try:
            raw = getattr(self, "_var_hope_amount", None)
            if raw is None:
                return WIN_DEFAULT
            return int(str(raw.get()).replace(",", "").strip() or "0")
        except (ValueError, AttributeError, tk.TclError):
            return WIN_DEFAULT

    def _adjust_hope_by_step(self, delta: int) -> None:
        """希望金額 +/- 調整（步長 HOPE_STEP，範圍 HOPE_MIN～WIN_MAX）。"""
        if not hasattr(self, "_var_hope_amount"):
            return
        cur = max(HOPE_MIN, min(WIN_MAX, self._hope_amount_int()))
        nxt = max(HOPE_MIN, min(WIN_MAX, cur + delta))
        if nxt == cur:
            return
        self._var_hope_amount.set(str(nxt))
        self._persist_hope_amount()

    def _sync_extra_hope_display(self) -> None:
        """更新訊息框下方希望金額顯示（千分位）。"""
        if not hasattr(self, "_lbl_extra_hope_value"):
            return
        try:
            n = self._hope_amount_int()
            self._lbl_extra_hope_value.config(text=f"{n:,}")
        except (tk.TclError, AttributeError):
            pass

    def _sync_extra_balance_bar_width(self, _event: tk.Event | None = None) -> None:
        """達成率進度條寬度隨區塊拉伸；重繪填充。"""
        if not hasattr(self, "_extra_balance_frame") or not hasattr(self, "_canvas_extra_balance_rate"):
            return
        try:
            w = int(self._extra_balance_frame.winfo_width())
        except tk.TclError:
            return
        if w < 8:
            return
        cw = max(120, w - 4)
        try:
            if not self._canvas_extra_balance_rate.winfo_exists():
                return
            self._canvas_extra_balance_rate.config(width=cw)
            self._update_bar_dark(self._canvas_extra_balance_rate, self._extra_balance_rate_pct)
        except tk.TclError:
            pass

    def _update_extra_balance_line(self) -> None:
        """錢包餘額與達成率（達成率 = 錢包餘額／希望金額 × 100%）；下方進度條顯示 0～100%。"""
        if not hasattr(self, "_lbl_extra_balance_achievement"):
            return
        try:
            raw_bal = self._dashboard_data.get("balance", "—")
            bal_disp = format_wallet_balance_display(raw_bal)
            hope = self._hope_amount_int()
            bal_int = self._parse_balance_to_int(raw_bal)
            if bal_int is None or hope <= 0:
                rate_s = "—"
                self._extra_balance_rate_pct = 0
            else:
                ratio = bal_int / hope
                rate_s = f"{ratio * 100:.2f}"
                self._extra_balance_rate_pct = min(100, max(0, int(round(ratio * 100))))
            self._lbl_extra_balance_achievement.config(
                text=self._t("extra_balance_achievement", bal=bal_disp, rate=rate_s)
            )
            if hasattr(self, "_canvas_extra_balance_rate"):
                try:
                    self._update_bar_dark(self._canvas_extra_balance_rate, self._extra_balance_rate_pct)
                except tk.TclError:
                    pass
        except (tk.TclError, AttributeError):
            pass
        self._update_extra_deposit_privilege_line()
        self._update_extra_lottery_schedule_line()

    def _format_extra_lottery_schedule_text(self) -> str:
        """樂透行：時間與號碼來自 get_user_info 的 lotteryTime、lotteryNumber；號碼為 0 時兩者皆顯示未下注。"""
        d = getattr(self, "_dashboard_data", {}) or {}
        nb = self._t("extra_lottery_not_bet")
        try:
            n = int(d.get("lottery_number", 0))
        except (ValueError, TypeError):
            n = 0
        if n == 0:
            return self._t("extra_lottery_schedule", time=nb, number=nb)
        time_s = str(d.get("lottery_time", "—")).strip() or "—"
        return self._t("extra_lottery_schedule", time=time_s, number=str(n))

    def _update_extra_lottery_schedule_line(self) -> None:
        if not hasattr(self, "_lbl_extra_lottery_schedule"):
            return
        try:
            self._lbl_extra_lottery_schedule.config(text=self._format_extra_lottery_schedule_text())
        except (tk.TclError, AttributeError):
            pass

    def _update_extra_deposit_privilege_line(self) -> None:
        """儲值升級段落中「快速提款」金額＝即時錢包餘額（與 row_balance 同源）。"""
        if not hasattr(self, "_lbl_extra_deposit_privilege"):
            return
        try:
            raw_bal = self._dashboard_data.get("balance", "—")
            withdraw_bal = format_wallet_balance_display(raw_bal)
            self._lbl_extra_deposit_privilege.config(
                text=self._t("extra_deposit_privilege", withdraw_bal=withdraw_bal)
            )
        except (tk.TclError, AttributeError):
            pass

    def _cancel_login_gif_tick(self) -> None:
        if self._login_media_after_id is not None:
            try:
                self.root.after_cancel(self._login_media_after_id)
            except Exception:
                pass
            self._login_media_after_id = None

    def _cancel_login_media(self) -> None:
        self._cancel_login_gif_tick()
        if self._login_gif_resize_after_id is not None:
            try:
                self.root.after_cancel(self._login_gif_resize_after_id)
            except Exception:
                pass
            self._login_gif_resize_after_id = None
        if self._login_gif_box is not None:
            try:
                self._login_gif_box.unbind("<Configure>")
            except Exception:
                pass
            self._login_gif_box = None
        self._login_gif_last_layout_w = 0

    def _pick_login_media_path(self) -> Path | None:
        for name in LOGIN_MEDIA_CANDIDATES:
            p = resolve_data_asset(name)
            if p is not None:
                return p
        return None

    def _start_login_gif_tick(self, lbl: tk.Label) -> None:
        def tick_gif() -> None:
            try:
                if not lbl.winfo_exists():
                    return
            except tk.TclError:
                return
            self._login_media_after_id = None
            idx = self._login_gif_index
            photo = self._login_gif_frames[idx]
            lbl.config(image=photo)
            lbl.image = photo
            delay = self._login_gif_delays[idx]
            self._login_gif_index = (idx + 1) % len(self._login_gif_frames)
            self._login_media_after_id = self.root.after(delay, tick_gif)

        self._login_media_after_id = self.root.after(50, tick_gif)

    def _rebuild_login_gif_for_width(
        self, lbl: tk.Label, media_path: Path, width_px: int
    ) -> bool:
        """依指定寬度等比例縮放 GIF 各幀並重新開始播放。失敗回傳 False。"""
        self._cancel_login_gif_tick()
        if Image is None or ImageTk is None or ImageSequence is None:
            return False
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS  # type: ignore[attr-defined]
        try:
            im = Image.open(media_path)
        except Exception:
            return False
        wpx = max(1, int(width_px))
        frames: list[ImageTk.PhotoImage] = []
        delays_ms: list[int] = []
        try:
            for fr in ImageSequence.Iterator(im):
                rgba = fr.convert("RGBA").copy()
                ow, oh = rgba.size
                ow = max(ow, 1)
                nh = max(1, int(round(oh * wpx / ow)))
                rgba = rgba.resize((wpx, nh), resample)
                frames.append(ImageTk.PhotoImage(image=rgba))
                delays_ms.append(max(int(fr.info.get("duration", 100)), 50))
        finally:
            im.close()
        if not frames:
            return False
        self._login_gif_frames = frames
        self._login_gif_delays = delays_ms
        self._login_gif_index = 0
        self._start_login_gif_tick(lbl)
        return True

    def _show_login_media(
        self,
        parent: tk.Frame,
        *,
        panel_bg: str = "#000000",
        max_thumb_w: int = 480,
        max_thumb_h: int = 270,
        fit_container_width: bool = False,
    ) -> None:
        """頂部橫幅：循環播放 LOGIN_MEDIA_CANDIDATES 的 GIF（僅 Pillow）。"""
        self._cancel_login_media()
        media_path = self._pick_login_media_path()
        box = tk.Frame(parent, bg=panel_bg)
        if fit_container_width:
            box.pack(fill=tk.X, pady=(0, 6))
        else:
            box.pack(anchor="center", pady=(0, 6))

        if media_path is None:
            tk.Label(
                box,
                text=self._t("media_hint_gif"),
                fg=LOGIN_FG_MUTED,
                bg=panel_bg,
                font=(LOGIN_FONT_FAMILY, 9),
                wraplength=500,
                justify="left",
            ).pack()
            return

        suf = media_path.suffix.lower()
        if suf != ".gif":
            tk.Label(
                box,
                text=self._t("media_wrong_ext", ext=suf or self._t("media_ext_none")),
                fg=LOGIN_FG_MUTED,
                bg=panel_bg,
                font=(LOGIN_FONT_FAMILY, 9),
                wraplength=500,
                justify="left",
            ).pack()
            return

        lbl = tk.Label(box, bg=panel_bg)
        lbl.pack()
        if Image is None or ImageTk is None or ImageSequence is None:
            tk.Label(
                box,
                text=self._t("media_need_pillow"),
                fg=LOGIN_FG_MUTED,
                bg=panel_bg,
                font=(LOGIN_FONT_FAMILY, 9),
            ).pack()
            return

        if fit_container_width:
            self._login_gif_box = box
            self._login_gif_last_layout_w = 0

            def apply_width_layout() -> None:
                self._login_gif_resize_after_id = None
                try:
                    if not box.winfo_exists():
                        return
                    bw = box.winfo_width()
                except tk.TclError:
                    return
                if bw < 64:
                    return
                if bw == self._login_gif_last_layout_w:
                    return
                if not self._rebuild_login_gif_for_width(lbl, media_path, bw):
                    return
                self._login_gif_last_layout_w = bw

            def on_box_configure(event: tk.Event) -> None:
                if event.widget != box:
                    return
                if self._login_gif_resize_after_id is not None:
                    try:
                        self.root.after_cancel(self._login_gif_resize_after_id)
                    except Exception:
                        pass
                self._login_gif_resize_after_id = self.root.after(80, apply_width_layout)

            box.bind("<Configure>", on_box_configure)
            self.root.after_idle(lambda: self.root.after(50, apply_width_layout))
            return

        max_w, max_h = max_thumb_w, max_thumb_h
        try:
            im = Image.open(media_path)
        except Exception:
            tk.Label(box, text=self._t("media_read_fail"), fg=LOGIN_FG_MUTED, bg=panel_bg, font=(LOGIN_FONT_FAMILY, 9)).pack()
            return
        try:
            resample = Image.Resampling.LANCZOS
        except AttributeError:
            resample = Image.LANCZOS  # type: ignore[attr-defined]
        frames: list[ImageTk.PhotoImage] = []
        delays_ms: list[int] = []
        for fr in ImageSequence.Iterator(im):
            rgba = fr.convert("RGBA").copy()
            rgba.thumbnail((max_w, max_h), resample)
            frames.append(ImageTk.PhotoImage(image=rgba))
            delays_ms.append(max(int(fr.info.get("duration", 100)), 50))
        im.close()
        if not frames:
            tk.Label(box, text=self._t("media_no_frames"), fg=LOGIN_FG_MUTED, bg=panel_bg, font=(LOGIN_FONT_FAMILY, 9)).pack()
            return

        self._login_gif_frames = frames
        self._login_gif_delays = delays_ms
        self._login_gif_index = 0
        self._start_login_gif_tick(lbl)

    def _show_login_top_banner(self, parent: tk.Frame) -> None:
        """登入頁頂部：循環播放 data/ 內 GIF（如 VN.gif）"""
        self._login_banner_photo = None
        wrap = tk.Frame(parent, bg=LOGIN_UI_BG)
        wrap.pack(fill=tk.X)
        self._show_login_media(wrap, panel_bg=LOGIN_UI_BG, fit_container_width=True)

    def _show_main_screen_banner(self, parent: tk.Frame, panel_bg: str) -> None:
        """主畫面頂部：與登入相同，循環播放 data/ 內 GIF"""
        self._main_banner_photo = None
        wrap = tk.Frame(parent, bg=panel_bg)
        wrap.pack(fill=tk.X, pady=(0, 8))
        self._show_login_media(wrap, panel_bg=panel_bg, fit_container_width=True)

    def _main_outline_button(self, parent: tk.Frame, text: str, command, width: int = 10) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            font=(LOGIN_FONT_FAMILY, 9),
            bg=MAIN_BTN_BG,
            fg=MAIN_ACCENT,
            activebackground=MAIN_BTN_ACTIVE_BG,
            activeforeground=MAIN_ACCENT,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=MAIN_ACCENT,
            highlightcolor=MAIN_ACCENT,
            cursor="hand2",
        )

    def _resize_tier_bonus_table_image(self, card_width: int) -> None:
        """依即時資訊卡寬度縮放七階獎金表圖（data/VN.jpg），維持比例。"""
        if Image is None or ImageTk is None:
            return
        pil = getattr(self, "_tier_bonus_pil_src", None)
        lbl = getattr(self, "_lbl_tier_bonus_img", None)
        if pil is None or lbl is None:
            return
        try:
            if not lbl.winfo_exists():
                return
        except tk.TclError:
            return
        w = max(80, card_width - 28)
        try:
            img = pil.copy()
            ratio = w / img.width
            h = max(1, int(img.height * ratio))
            try:
                img = img.resize((w, h), Image.Resampling.LANCZOS)
            except AttributeError:
                img = img.resize((w, h), Image.LANCZOS)  # type: ignore[attr-defined]
            self._tier_bonus_photo_ref = ImageTk.PhotoImage(image=img)
            lbl.config(image=self._tier_bonus_photo_ref)
        except Exception as e:
            print(f"[七階獎金圖] 縮放失敗: {e}")

    def _login_section_header(self, parent: tk.Frame, icon: str, title: str) -> None:
        row = tk.Frame(parent, bg=LOGIN_CARD_BG)
        row.pack(anchor="w", fill=tk.X, pady=(0, 8))
        tk.Label(
            row,
            text=f"{icon}  {title}",
            font=(LOGIN_FONT_FAMILY, 11, "bold"),
            bg=LOGIN_CARD_BG,
            fg="#ffffff",
            anchor="w",
        ).pack(anchor="w")

    def _login_readonly_text(
        self,
        parent: tk.Frame,
        body: str,
        height: int,
        *,
        vnd_green: bool = False,
        tag_note_from: str | None = None,
    ) -> tk.Text:
        t = tk.Text(
            parent,
            height=height,
            width=1,
            wrap=tk.WORD,
            font=(LOGIN_FONT_FAMILY, 9),
            bg=LOGIN_CARD_INNER,
            fg=LOGIN_FG,
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=8,
            cursor="arrow",
        )
        t.insert("1.0", body or "—")
        if tag_note_from:
            i = body.find(tag_note_from)
            if i >= 0:
                t.tag_configure("note", foreground=LOGIN_NOTE_ORANGE)
                t.tag_add("note", f"1.0+{i}c", "end-1c")
        if vnd_green:
            t.tag_configure("vnd", foreground=LOGIN_REV_GREEN)
            # 繁中：120000／120,000 VND；越南文常為 120.000 VND、1.000.000 VND（千分位用點）
            for m in re.finditer(r"[\d.,]+\s+VND", body):
                t.tag_add("vnd", f"1.0+{m.start()}c", f"1.0+{m.end()}c")
            for m in re.finditer(r"\d[\d,]*萬", body):
                t.tag_add("vnd", f"1.0+{m.start()}c", f"1.0+{m.end()}c")
        t.config(state=tk.DISABLED)
        return t

    def show_login_frame(self) -> None:
        self.clear_frame()
        saved = self.load_config()
        self._ui_lang = saved["ui_language"]
        self._platform_key = normalize_platform_key(saved.get("platform", DEFAULT_PLATFORM_KEY))
        apply_platform_key(self._platform_key)
        self.root.title(self._app_window_title())
        self.root.configure(bg=LOGIN_UI_BG)
        self.main_container.configure(bg=LOGIN_UI_BG)

        outer = tk.Frame(self.main_container, bg=LOGIN_UI_BG, padx=10, pady=10)
        outer.pack(expand=True, fill="both")
        outer.grid_columnconfigure(0, weight=2, uniform="login_cols")
        outer.grid_columnconfigure(1, weight=3, uniform="login_cols")
        outer.grid_rowconfigure(1, weight=1)

        banner_host = tk.Frame(outer, bg=LOGIN_UI_BG)
        banner_host.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._show_login_top_banner(banner_host)

        login_card = tk.Frame(
            outer,
            bg=LOGIN_CARD_BG,
            padx=16,
            pady=16,
            highlightbackground=LOGIN_CARD_BORDER,
            highlightthickness=1,
        )
        login_card.grid(row=1, column=0, sticky="nsew", padx=(0, 5), pady=(0, 8))

        sys_card = tk.Frame(
            outer,
            bg=LOGIN_CARD_BG,
            padx=14,
            pady=14,
            highlightbackground=LOGIN_CARD_BORDER,
            highlightthickness=1,
        )
        sys_card.grid(row=1, column=1, sticky="nsew", padx=(5, 0), pady=(0, 8))

        ops_card = tk.Frame(
            outer,
            bg=LOGIN_CARD_BG,
            padx=14,
            pady=14,
            highlightbackground=LOGIN_CARD_BORDER,
            highlightthickness=1,
        )
        ops_card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        rev_card = tk.Frame(
            outer,
            bg=LOGIN_CARD_BG,
            padx=14,
            pady=14,
            highlightbackground=LOGIN_CARD_BORDER,
            highlightthickness=1,
        )
        rev_card.grid(row=3, column=0, columnspan=2, sticky="ew")

        tk.Label(
            login_card,
            text=self._t("login_prompt_title"),
            font=(LOGIN_FONT_FAMILY, 11, "bold"),
            bg=LOGIN_CARD_BG,
            fg=LOGIN_FG,
            anchor="w",
        ).pack(anchor="w", pady=(0, 12))

        entry_opts = {
            "width": 22,
            "font": (LOGIN_FONT_FAMILY, 10),
            "bg": LOGIN_ENTRY_BG,
            "fg": LOGIN_FG,
            "insertbackground": LOGIN_FG,
            "relief": tk.FLAT,
            "highlightthickness": 1,
            "highlightbackground": LOGIN_CARD_BORDER,
            "highlightcolor": LOGIN_ENTRY_HL,
        }

        creds = tk.Frame(login_card, bg=LOGIN_CARD_BG)
        creds.pack(anchor="w", fill=tk.X, pady=(0, 4))
        creds.grid_columnconfigure(1, weight=1)
        for row_i, (lab, key, show) in enumerate(
            (
                (self._t("label_account"), "username", None),
                (self._t("label_password"), "password", "*"),
            )
        ):
            tk.Label(
                creds,
                text=lab,
                font=(LOGIN_FONT_FAMILY, 10),
                bg=LOGIN_CARD_BG,
                fg=LOGIN_FG_MUTED,
                anchor="w",
            ).grid(row=row_i, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
            ent = tk.Entry(creds, show=show, **entry_opts)
            ent.grid(row=row_i, column=1, sticky="ew", pady=(0, 8))
            if key == "username":
                self.entry_user = ent
                if saved.get("username"):
                    self.entry_user.insert(0, saved["username"])
            else:
                self.entry_pass = ent
                if saved.get("password"):
                    self.entry_pass.insert(0, saved["password"])

        lang_row = tk.Frame(login_card, bg=LOGIN_CARD_BG)
        lang_row.pack(anchor="w", fill=tk.X, pady=(0, 8))
        tk.Label(
            lang_row,
            text=self._t("label_interface_lang"),
            font=(LOGIN_FONT_FAMILY, 10),
            bg=LOGIN_CARD_BG,
            fg=LOGIN_FG_MUTED,
            anchor="w",
        ).pack(side=tk.LEFT, padx=(0, 8))
        _lang_by_label = {"繁體中文": "zh-tw", "Tiếng Việt": "vi"}
        _label_by_lang = {"zh-tw": "繁體中文", "vi": "Tiếng Việt"}
        _lang_labels = tuple(_lang_by_label.keys())
        lang_cb = ttk.Combobox(
            lang_row,
            state="readonly",
            width=16,
            values=_lang_labels,
        )
        lang_cb.set(_label_by_lang.get(self._ui_lang, _lang_labels[0]))
        lang_cb.pack(side=tk.LEFT)

        def on_lang_selected(_event: tk.Event | None = None) -> None:
            code = _lang_by_label.get(lang_cb.get(), UI_LANG_DEFAULT)
            if code == self._ui_lang:
                return
            self._ui_lang = code
            self._persist_language_pref()
            self.show_login_frame()

        lang_cb.bind("<<ComboboxSelected>>", on_lang_selected)

        platform_row = tk.Frame(login_card, bg=LOGIN_CARD_BG)
        platform_row.pack(anchor="w", fill=tk.X, pady=(0, 8))
        tk.Label(
            platform_row,
            text=self._t("label_platform"),
            font=(LOGIN_FONT_FAMILY, 10),
            bg=LOGIN_CARD_BG,
            fg=LOGIN_FG_MUTED,
            anchor="w",
        ).pack(side=tk.LEFT, padx=(0, 8))
        _platform_keys = tuple(sorted(PLATFORM_PRESETS.keys()))
        platform_cb = ttk.Combobox(
            platform_row,
            state="readonly",
            width=16,
            values=_platform_keys,
        )
        platform_cb.set(self._platform_key)
        platform_cb.pack(side=tk.LEFT)

        def on_platform_selected(_event: tk.Event | None = None) -> None:
            key = platform_cb.get()
            if key == self._platform_key:
                return
            self._platform_key = apply_platform_key(key)
            self._persist_platform_pref()

        platform_cb.bind("<<ComboboxSelected>>", on_platform_selected)

        login_btn = tk.Button(
            login_card,
            text=self._t("btn_login"),
            command=self.handle_login,
            font=(LOGIN_FONT_FAMILY, 11, "bold"),
            bg=LOGIN_BTN_BG,
            fg="#ffffff",
            activebackground=LOGIN_BTN_BG_ACTIVE,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            cursor="hand2",
            pady=10,
        )
        login_btn.pack(fill=tk.X, pady=(12, 0))

        system_txt, ops_txt, rev_txt = load_ui_guide_sections(self._ui_lang)

        self._login_section_header(sys_card, "🧠", self._t("section_system"))
        t_sys = self._login_readonly_text(
            sys_card, system_txt, height=12, tag_note_from=self._t("system_note_tag")
        )
        t_sys.pack(fill=tk.BOTH, expand=True)

        self._login_section_header(ops_card, "⚙", self._t("section_ops"))
        t_ops = self._login_readonly_text(ops_card, ops_txt, height=6)
        t_ops.pack(fill=tk.X)

        self._login_section_header(rev_card, "💰", self._t("section_revenue"))
        t_rev = self._login_readonly_text(rev_card, rev_txt, height=8, vnd_green=True)
        t_rev.pack(fill=tk.X)

    def _report_download_account_async(self, username: str) -> None:
        """登入成功後向 openclawData 回報帳號與站台（目前作用中訪客頁），不阻塞介面。"""

        def run() -> None:
            try:
                API.save_downloadaccount(username, get_guest_url())
            except Exception as e:
                print(f"[openclawData] save_downloadaccount 失敗: {e}")

        threading.Thread(target=run, daemon=True).start()

    def handle_login(self) -> None:
        username = self.entry_user.get()
        password = self.entry_pass.get()

        if not username or not password:
            messagebox.showerror(self._t("err_title"), self._t("err_need_account_password"))
            return

        self._guest_site_url()
        api_info = API.get_user_info(username.strip())
        if not API.user_info_looks_valid(api_info, username):
            messagebox.showerror(self._t("err_login_failed"), self._t("err_bad_credentials"))
            self.show_login_frame()
            return

        self.save_config(username, password)
        self.userinfo = {"username": username, "password": password}
        self._report_download_account_async(username.strip())

        mapped = API.map_to_dashboard(api_info)
        self._dashboard_data = {
            "level": "—",
            "game_count": "—",
            "balance": "—",
            "referral_code": "—",
            "downline_count": "—",
            "active_count": "—",
            "lottery_reward": "—",
            "roulette_reward": "—",
            "ref_10_pct": "—",
            "ref_30_pct": "—",
            "ref_60_pct": "—",
            "ref_100_pct": "—",
            "challenge_pct": "—",
            "commission": "—",
            "lottery_time": "—",
            "lottery_number": 0,
        }
        self._dashboard_data.update(mapped)
        self._refresh_job: str | None = None
        self.show_main_frame(username)

    def _get_game_params(self) -> dict:
        """取得參數設定欄位的值（希望金額與訊息區 +/- 同源）。"""
        try:
            amt = max(HOPE_MIN, min(WIN_MAX, self._hope_amount_int()))
        except (ValueError, AttributeError):
            amt = WIN_DEFAULT
        return {
            "ai_system": self._var_ai.get(),
            "win_amount": amt,
            "open_fb": self._var_open_fb.get(),
            "open_ig": self._var_open_ig.get(),
            "open_threads": self._var_open_threads.get(),
            "open_whatsapp": self._var_open_whatsapp.get(),
            "play_lottery": self._var_play_lottery.get(),
            # "play_roulette": self._var_play_roulette.get(),
            "claim_rewards": self._var_claim_rewards.get(),
        }

    @staticmethod
    def _parse_balance_to_int(raw: object) -> int | None:
        """儀表板錢包餘額字串轉整數；無法解析時回傳 None（不阻擋玩遊戲以免誤擋）。"""
        if raw is None:
            return None
        s = str(raw).strip()
        if not s or s == "—":
            return None
        try:
            return int(s.replace(",", "").replace(" ", ""))
        except ValueError:
            return None

    def _sync_dashboard_from_api(self) -> None:
        """向 API 拉一次即時資訊寫入 _dashboard_data（供錢包與希望金額比對）。"""
        username = (self.userinfo.get("username") or "").strip()
        if not username:
            return
        try:
            api_data = API.get_user_info(username)
            if api_data:
                self._dashboard_data.update(API.map_to_dashboard(api_data))
                try:
                    if getattr(self, "root", None) and self.root.winfo_exists():
                        self.root.after(0, self._update_extra_balance_line)
                except tk.TclError:
                    pass
        except Exception as e:
            print(f"同步儀表板失敗: {e}")

    def _wallet_over_hope_amount(self, win_amount: int | None = None) -> bool:
        """錢包餘額是否已達或超過希望金額（>=，僅在餘額可解析為數字時為 True）。"""
        if win_amount is None:
            win_amount = self._get_game_params()["win_amount"]
        bal = self._parse_balance_to_int(self._dashboard_data.get("balance"))
        if bal is None:
            return False
        return bal >= win_amount

    def _auto_refresh(self) -> None:
        """每5分鐘自動更新即時資訊"""
        self._refresh_dashboard()
        if hasattr(self, "root") and self.root.winfo_exists():
            self._refresh_job = self.root.after(5 * 60 * 1000, self._auto_refresh)

    def _refresh_dashboard(self) -> None:
        """即時更新：呼叫 API 取得資料並更新畫面（背景執行，不阻塞 TK）"""
        username = self.userinfo.get("username", "")
        if not username:
            return

        def fetch_and_update():
            api_data = API.get_user_info(username)
            if api_data:
                mapped = API.map_to_dashboard(api_data)
                self._dashboard_data.update(mapped)
            if self.root.winfo_exists():
                self.root.after(0, self._update_info_labels)

        threading.Thread(target=fetch_and_update, daemon=True).start()

    @staticmethod
    def _parse_pct(s: str) -> int:
        """從 '5/10 (50%)' 格式解析百分比，回傳 0~100"""
        if not s or s == "—":
            return 0
        m = re.search(r"\((\d+)%\)", s)
        return int(m.group(1)) if m else 0

    def _create_bar_canvas(self, parent: tk.Frame, width: int = 100, height: int = 14) -> tk.Canvas:
        """建立長條圖 Canvas，可後續用 _update_bar 更新"""
        c = tk.Canvas(parent, width=width, height=height, highlightthickness=0, bg="#f0f0f0")
        return c

    def _update_bar(self, canvas: tk.Canvas, pct: int, fill_color: str = "#4CAF50") -> None:
        """更新長條圖 (pct 0~100)"""
        w = int(canvas["width"])
        h = int(canvas["height"])
        canvas.delete("all")
        canvas.create_rectangle(0, 0, w, h, fill="#e8e8e8", outline="#ccc")
        fill_w = max(0, min(100, pct)) / 100 * w
        if fill_w > 0:
            canvas.create_rectangle(0, 0, fill_w, h, fill=fill_color, outline="")

    def _update_bar_dark(self, canvas: tk.Canvas, pct: int) -> None:
        """主畫面深色卡片內的推薦進度條"""
        w = int(canvas["width"])
        h = int(canvas["height"])
        canvas.delete("all")
        canvas.create_rectangle(0, 0, w, h, fill="#2d3848", outline="#455064")
        fill_w = max(0, min(100, pct)) / 100 * w
        if fill_w > 0:
            canvas.create_rectangle(0, 0, fill_w, h, fill=MAIN_ACCENT, outline="")

    def _copy_referral_code(self) -> None:
        """點擊推廣碼時複製到剪貼簿"""
        code = self._dashboard_data.get("referral_code", "—")
        if code and code != "—":
            self.root.clipboard_clear()
            self.root.clipboard_append(code)
            self.root.update()
            # messagebox.showinfo("已複製", "推廣碼已複製到剪貼簿")

    def _format_ref_tier_progress_text(self, key: str, progress_str: str) -> str:
        """分享區推薦進度列：加上各階佣金（REF_REFERRAL_COMMISSION_VND）。"""
        m = re.match(r"^ref_(\d+)_pct$", key)
        if not m:
            return progress_str
        n = int(m.group(1))
        comm = REF_REFERRAL_COMMISSION_VND.get(n)
        if comm is None:
            return progress_str
        comm_s = f"{comm:,}"
        return self._t("row_ref_commission_and_progress", commission=comm_s, progress=progress_str)

    def _update_info_labels(self) -> None:
        """在主線程更新即時資訊標籤"""
        if not hasattr(self, "_info_labels"):
            return
        try:
            for k, lbl in self._info_labels.items():
                if k in self._dashboard_data:
                    val = str(self._dashboard_data[k])
                    if k == "referral_code":
                        lbl.config(
                            text=val,
                            fg=MAIN_ACCENT,
                            font=(LOGIN_FONT_FAMILY, 9, "underline"),
                            wraplength=0,
                        )
                    elif re.match(r"^ref_\d+_pct$", k):
                        lbl.config(text=self._format_ref_tier_progress_text(k, val))
                    else:
                        lbl.config(text=val)
                    if k in getattr(self, "_info_bars", {}):
                        bc = self._info_bars[k]
                        if getattr(bc, "_dark_bar", False):
                            self._update_bar_dark(bc, self._parse_pct(val))
                        else:
                            self._update_bar(bc, self._parse_pct(val))
            self._update_extra_balance_line()
        except Exception as e:
            print(f"更新即時資訊標籤失敗: {e}")

    def _main_on_mousewheel(self, event: tk.Event) -> None:
        c = self._main_scroll_canvas
        if not c:
            return
        try:
            if getattr(event, "delta", 0):
                d = int(event.delta)
                if abs(d) < 120:
                    c.yview_scroll(-1 if d > 0 else 1, "units")
                else:
                    c.yview_scroll(int(-1 * (d / 120)), "units")
        except tk.TclError:
            pass

    def _main_scroll_linux_up(self, event: tk.Event) -> None:
        c = self._main_scroll_canvas
        if not c:
            return
        try:
            c.yview_scroll(-1, "units")
        except tk.TclError:
            pass

    def _main_scroll_linux_down(self, event: tk.Event) -> None:
        c = self._main_scroll_canvas
        if not c:
            return
        try:
            c.yview_scroll(1, "units")
        except tk.TclError:
            pass

    def show_main_frame(self, username: str) -> None:
        """登入後：橫幅、工具列、AI 跑馬燈、即時資訊卡片、參數設定（對齊 logindemo 深色風格）"""
        self.clear_frame()
        self.root.title(self._app_window_title())
        BG = MAIN_UI_BG
        try:
            self.root.configure(bg=BG)
            self.main_container.configure(bg=BG)
        except tk.TclError:
            pass

        scroll_wrap = tk.Frame(self.main_container, bg=BG)
        scroll_wrap.pack(expand=True, fill="both")

        canvas = tk.Canvas(scroll_wrap, bg=BG, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(scroll_wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        frame = tk.Frame(canvas, padx=12, pady=12, bg=BG)
        win_id = canvas.create_window((0, 0), window=frame, anchor="nw")

        def _sync_scroll_width(event: tk.Event) -> None:
            try:
                canvas.itemconfigure(win_id, width=max(event.width, 1))
            except tk.TclError:
                pass

        def _sync_scroll_region(_event: tk.Event | None = None) -> None:
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except tk.TclError:
                pass

        canvas.bind("<Configure>", _sync_scroll_width)
        frame.bind("<Configure>", lambda e: _sync_scroll_region(e))

        self._main_scroll_canvas = canvas
        self.root.bind_all("<MouseWheel>", self._main_on_mousewheel)
        self.root.bind_all("<Button-4>", self._main_scroll_linux_up)
        self.root.bind_all("<Button-5>", self._main_scroll_linux_down)

        self._show_main_screen_banner(frame, BG)

        # --- 即時資訊：標題獨占第一列（長語系可換行），按鈕列在下方，避免擠壓 ---
        info_row = tk.Frame(frame, bg=BG)
        info_row.pack(anchor="w", pady=(0, 8), fill="x")
        self._lbl_main_live_title = tk.Label(
            info_row,
            text=self._t("main_live_info"),
            font=(LOGIN_FONT_FAMILY, 14, "bold"),
            bg=BG,
            fg="#ffffff",
            anchor="nw",
            justify="left",
        )
        self._lbl_main_live_title.pack(anchor="w", fill="x")

        def _sync_main_live_title_wrap(_event: tk.Event | None = None) -> None:
            try:
                w = int(info_row.winfo_width())
            except tk.TclError:
                return
            if w > 48:
                self._lbl_main_live_title.config(wraplength=max(w - 8, 80))

        info_row.bind("<Configure>", _sync_main_live_title_wrap)
        self.root.after_idle(_sync_main_live_title_wrap)

        info_header = tk.Frame(info_row, bg=BG)
        info_header.pack(anchor="w", pady=(6, 0))
        self._main_outline_button(info_header, self._t("btn_refresh"), self._refresh_dashboard, width=8).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self._btn_start_stop = self._main_outline_button(
            info_header, self._t("btn_start"), self.toggle_start_stop, width=10
        )
        self._btn_start_stop.pack(side=tk.LEFT, padx=(0, 6))
        self._main_outline_button(info_header, self._t("btn_update"), self.check_update, width=10).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self._main_outline_button(info_header, self._t("btn_logout"), self.show_login_frame, width=8).pack(
            side=tk.LEFT, padx=(0, 6)
        )

        ai_wrap = tk.Frame(
            frame,
            bg=MAIN_MARQUEE_BG,
            highlightbackground=MAIN_CARD_BORDER,
            highlightthickness=1,
        )
        ai_wrap.pack(anchor="w", fill="x", pady=(0, 10))
        ai_bar = tk.Frame(ai_wrap, bg=MAIN_MARQUEE_BG, padx=10, pady=8)
        ai_bar.pack(anchor="w", fill="x")
        self._lbl_ai_title = tk.Label(
            ai_bar,
            text="",
            font=(LOGIN_FONT_FAMILY, 10, "bold"),
            anchor="w",
            fg=MAIN_ACCENT,
            bg=MAIN_MARQUEE_BG,
        )
        self._lbl_ai_title.pack(anchor="w")
        self._ai_msg_var = tk.StringVar(value="")
        self._lbl_ai_msg = tk.Label(
            ai_bar,
            textvariable=self._ai_msg_var,
            wraplength=520,
            justify="left",
            anchor="w",
            font=(LOGIN_FONT_FAMILY, 9),
            fg=MAIN_FG,
            bg=MAIN_MARQUEE_BG,
        )
        self._lbl_ai_msg.pack(anchor="w")
        self._ai_show_ready_until_start = True
        self._ai_msg_var.set(self._t("ai_msg_ready_settings"))

        hope_saved = self._normalize_hope_amount(self.load_config().get("hope_amount"))
        hope_init = hope_saved if hope_saved is not None else WIN_DEFAULT
        self._var_hope_amount = tk.StringVar(value=str(hope_init))
        self._var_play_lottery = tk.BooleanVar(value=True)
        self._var_claim_rewards = tk.BooleanVar(value=True)
        self._var_open_fb = tk.BooleanVar(value=False)
        self._var_open_ig = tk.BooleanVar(value=False)
        self._var_open_threads = tk.BooleanVar(value=False)
        self._var_open_whatsapp = tk.BooleanVar(value=False)

        extra_info_card = tk.Frame(
            frame,
            bg=MAIN_CARD_BG,
            highlightbackground=MAIN_CARD_BORDER,
            highlightthickness=1,
            padx=12,
            pady=12,
        )
        extra_info_card.pack(anchor="w", fill="x", pady=(0, 10))
        ei_text_kw = {
            "bg": MAIN_CARD_BG,
            "fg": MAIN_FG,
            "font": (LOGIN_FONT_FAMILY, 9),
            "anchor": "w",
            "justify": "left",
        }
        ei_strip_kw = {**ei_text_kw, "bg": MAIN_SECTION_BG}
        self._extra_info_wrap_labels: list[tk.Label] = []
        self._extra_info_wrap_texts: list[tk.Text] = []

        def _ei_pack_paragraph(body: str, pady: tuple[int, int] = (0, 8)) -> None:
            """以 WORD 換行（較不易在英文詞中間斷行）；搭配翻譯字串內 \\u00A0 固定詞組。"""
            t = tk.Text(
                extra_info_card,
                wrap=tk.WORD,
                height=1,
                width=1,
                borderwidth=0,
                highlightthickness=0,
                padx=0,
                pady=2,
                bg=MAIN_CARD_BG,
                fg=MAIN_FG,
                font=(LOGIN_FONT_FAMILY, 9),
                cursor="arrow",
                relief=tk.FLAT,
                undo=False,
            )
            t.insert("1.0", body)
            t.configure(state=tk.DISABLED)
            t.pack(anchor="w", fill="x", pady=pady)
            self._extra_info_wrap_texts.append(t)

        _ei_pack_paragraph(self._t("extra_intro_1"), pady=(0, 4))
        _ei_pack_paragraph(self._t("extra_intro_2"), pady=(0, 10))

        hope_balance_strip = tk.Frame(extra_info_card, bg=MAIN_SECTION_BG)
        hope_balance_strip.pack(anchor="w", fill="x", pady=(0, 8))
        hope_balance_inner = tk.Frame(hope_balance_strip, bg=MAIN_SECTION_BG)
        hope_balance_inner.pack(fill="x", padx=10, pady=8)

        hope_row = tk.Frame(hope_balance_inner, bg=MAIN_SECTION_BG)
        hope_row.pack(anchor="w", fill="x", pady=(0, 6))
        tk.Label(
            hope_row,
            text=self._t("extra_hope_prefix"),
            bg=MAIN_SECTION_BG,
            fg=MAIN_FG,
            font=(LOGIN_FONT_FAMILY, 9),
        ).pack(side=tk.LEFT)
        btn_kw = {
            "bg": MAIN_BTN_BG,
            "fg": MAIN_ACCENT,
            "activebackground": MAIN_BTN_ACTIVE_BG,
            "activeforeground": MAIN_ACCENT,
            "font": (LOGIN_FONT_FAMILY, 10, "bold"),
            "width": 3,
            "relief": tk.FLAT,
            "highlightthickness": 1,
            "highlightbackground": MAIN_ACCENT,
            "cursor": "hand2",
        }
        tk.Button(hope_row, text="−", command=lambda: self._adjust_hope_by_step(-HOPE_STEP), **btn_kw).pack(
            side=tk.LEFT, padx=(4, 2)
        )
        self._lbl_extra_hope_value = tk.Label(
            hope_row,
            text=f"{hope_init:,}",
            bg=MAIN_SECTION_BG,
            fg=MAIN_FG,
            font=(LOGIN_FONT_FAMILY, 10, "bold"),
        )
        self._lbl_extra_hope_value.pack(side=tk.LEFT, padx=4)
        tk.Button(hope_row, text="+", command=lambda: self._adjust_hope_by_step(HOPE_STEP), **btn_kw).pack(
            side=tk.LEFT, padx=(2, 4)
        )

        self._extra_balance_rate_pct = 0
        self._extra_balance_frame = tk.Frame(hope_balance_inner, bg=MAIN_SECTION_BG)
        self._extra_balance_frame.pack(anchor="w", fill="x", pady=(0, 8))
        self._lbl_extra_balance_achievement = tk.Label(self._extra_balance_frame, text="", **ei_strip_kw)
        self._lbl_extra_balance_achievement.pack(anchor="w", fill="x")
        self._canvas_extra_balance_rate = self._create_bar_canvas(self._extra_balance_frame, width=200, height=12)
        self._canvas_extra_balance_rate.config(bg=MAIN_SECTION_BG)
        self._canvas_extra_balance_rate._dark_bar = True  # type: ignore[attr-defined]
        self._canvas_extra_balance_rate.pack(anchor="w", fill="x", pady=(4, 0))
        self._extra_balance_frame.bind("<Configure>", lambda e: self._sync_extra_balance_bar_width(e))
        self._extra_info_wrap_labels.append(self._lbl_extra_balance_achievement)

        self._lbl_extra_lottery_schedule = tk.Label(
            extra_info_card,
            text=self._format_extra_lottery_schedule_text(),
            **ei_text_kw,
        )
        self._lbl_extra_lottery_schedule.pack(anchor="w", fill="x", pady=(0, 8))
        self._extra_info_wrap_labels.append(self._lbl_extra_lottery_schedule)
        self._lbl_extra_deposit_privilege = tk.Label(extra_info_card, text="", **ei_text_kw)
        self._lbl_extra_deposit_privilege.pack(anchor="w", fill="x", pady=(0, 8))
        self._extra_info_wrap_labels.append(self._lbl_extra_deposit_privilege)
        _ei_pack_paragraph(self._t("extra_share_upgrade_advice"), pady=(0, 8))

        cb_kw_ei = {
            "bg": MAIN_CARD_BG,
            "fg": MAIN_FG,
            "selectcolor": "#0d3d47",
            "activebackground": MAIN_CARD_BG,
            "activeforeground": MAIN_FG,
            "highlightthickness": 0,
            "font": (LOGIN_FONT_FAMILY, 9),
        }
        tk.Checkbutton(
            extra_info_card,
            text=self._t("chk_lottery"),
            variable=self._var_play_lottery,
            **cb_kw_ei,
        ).pack(anchor="w", pady=(4, 0))
        tk.Checkbutton(
            extra_info_card,
            text=self._t("chk_claim"),
            variable=self._var_claim_rewards,
            **cb_kw_ei,
        ).pack(anchor="w", pady=(4, 0))

        def _sync_extra_info_wrap(_event: tk.Event | None = None) -> None:
            try:
                w = int(extra_info_card.winfo_width())
            except tk.TclError:
                return
            if w < 80:
                return
            wl = max(200, w - 28)
            fn = tkfont.Font(root=self.root, family=LOGIN_FONT_FAMILY, size=9)
            ch_w = max(6, (fn.measure("0") + fn.measure("中") + fn.measure("M")) // 3)
            chars = max(14, int(wl // ch_w))
            for t in self._extra_info_wrap_texts:
                try:
                    if not t.winfo_exists():
                        continue
                    t.configure(state=tk.NORMAL)
                    t.configure(width=chars)
                    t.update_idletasks()
                    # wrap=word：必須用 displaylines，用 index 只會得到「邏輯行」導致高度不足裁切
                    line = int(
                        t.count("1.0", "end", "update", "displaylines", return_ints=True)
                    )
                    t.configure(height=max(1, line))
                    t.configure(state=tk.DISABLED)
                except tk.TclError:
                    pass
            for lb in self._extra_info_wrap_labels:
                try:
                    if lb.winfo_exists():
                        lb.config(wraplength=wl)
                except tk.TclError:
                    pass
            try:
                self._sync_extra_balance_bar_width()
            except Exception:
                pass

        extra_info_card.bind("<Configure>", lambda e: _sync_extra_info_wrap(e))
        self.root.after_idle(lambda: _sync_extra_info_wrap(None))

        self._update_extra_balance_line()

        self._info_labels = {}
        self._info_bars = {}

        # --- 分享、推薦進度、七階獎金與社群選項（原即時資訊內推薦列／七階圖、原參數內 FB／IG／Threads）---
        share_bonus_card = tk.Frame(
            frame,
            bg=MAIN_CARD_BG,
            highlightbackground=MAIN_CARD_BORDER,
            highlightthickness=1,
            padx=12,
            pady=12,
        )
        share_bonus_card.pack(anchor="w", fill="x", pady=(0, 10))
        tk.Label(
            share_bonus_card,
            text=self._t("share_bonus_card_title"),
            font=(LOGIN_FONT_FAMILY, 12, "bold"),
            bg=MAIN_CARD_BG,
            fg="#ffffff",
            anchor="w",
        ).pack(anchor="w", pady=(0, 10))

        self._share_wrap_texts: list[tk.Text] = []

        def _sb_pack_paragraph(
            body: str,
            pady: tuple[int, int] = (0, 8),
            *,
            inset_bg: str | None = None,
        ) -> None:
            bg = inset_bg if inset_bg is not None else MAIN_CARD_BG
            if inset_bg is not None:
                strip = tk.Frame(share_bonus_card, bg=inset_bg)
                strip.pack(anchor="w", fill="x", pady=pady)
                holder = tk.Frame(strip, bg=inset_bg)
                holder.pack(fill="x", padx=10, pady=8)
                parent = holder
                pack_pady: tuple[int, int] = (0, 0)
            else:
                parent = share_bonus_card
                pack_pady = pady
            t = tk.Text(
                parent,
                wrap=tk.WORD,
                height=1,
                width=1,
                borderwidth=0,
                highlightthickness=0,
                padx=0,
                pady=2,
                bg=bg,
                fg=MAIN_FG,
                font=(LOGIN_FONT_FAMILY, 9),
                cursor="arrow",
                relief=tk.FLAT,
                undo=False,
            )
            t.insert("1.0", body)
            t.configure(state=tk.DISABLED)
            t.pack(anchor="w", fill="x", pady=pack_pady)
            self._share_wrap_texts.append(t)

        _sb_pack_paragraph(self._t("share_box_body"), pady=(0, 10))

        tk.Label(
            share_bonus_card,
            text=self._t("share_box_bonus_title"),
            anchor="w",
            bg=MAIN_CARD_BG,
            fg="#ffffff",
            font=(LOGIN_FONT_FAMILY, 10, "bold"),
        ).pack(anchor="w", pady=(0, 4))

        ref_strip = tk.Frame(share_bonus_card, bg=MAIN_SECTION_BG)
        ref_strip.pack(anchor="w", fill="x", pady=(0, 10))
        ref_outer = tk.Frame(ref_strip, bg=MAIN_SECTION_BG)
        ref_outer.pack(fill="x", padx=10, pady=8)
        sb_lbl_kw = {"bg": MAIN_SECTION_BG, "fg": "#ffffff", "font": (LOGIN_FONT_FAMILY, 9)}
        for j, (n, k) in enumerate([(10, "ref_10_pct"), (30, "ref_30_pct"), (60, "ref_60_pct"), (100, "ref_100_pct")]):
            tk.Label(ref_outer, text=self._t("row_ref_cumulative_active", n=n), anchor="w", **sb_lbl_kw).grid(
                row=j, column=0, sticky="nw", padx=(0, 10), pady=2
            )
            bar_frame = tk.Frame(ref_outer, bg=MAIN_SECTION_BG)
            bar_frame.grid(row=j, column=1, sticky="nw", pady=2)
            bar_canvas = self._create_bar_canvas(bar_frame, width=100, height=12)
            bar_canvas.config(bg=MAIN_SECTION_BG)
            bar_canvas.pack(side=tk.LEFT, padx=(0, 6))
            bar_canvas._dark_bar = True  # type: ignore[attr-defined]
            val = str(self._dashboard_data.get(k, "—"))
            rlbl = tk.Label(
                bar_frame,
                text=self._format_ref_tier_progress_text(k, val),
                anchor="w",
                bg=MAIN_SECTION_BG,
                fg=MAIN_FG,
                font=(LOGIN_FONT_FAMILY, 9),
            )
            rlbl.pack(side=tk.LEFT)
            self._info_labels[k] = rlbl
            self._info_bars[k] = bar_canvas
            self._update_bar_dark(bar_canvas, self._parse_pct(val))

        _sb_pack_paragraph(self._t("share_box_lv1_block"), pady=(0, 8), inset_bg=MAIN_SECTION_BG)
        _sb_pack_paragraph(self._t("share_box_trial"), pady=(0, 8))

        self._tier_bonus_pil_src = None
        self._tier_bonus_photo_ref = None
        self._lbl_tier_bonus_img = None
        img_path = resolve_data_asset(TIER_BONUS_TABLE_IMAGE)
        if img_path is not None and Image is not None:
            try:
                self._tier_bonus_pil_src = Image.open(img_path).convert("RGBA")
            except Exception as e:
                print(f"[七階獎金圖] 讀取失敗 {img_path}: {e}")
        if self._tier_bonus_pil_src is not None:
            self._lbl_tier_bonus_img = tk.Label(share_bonus_card, bg=MAIN_CARD_BG)
            self._lbl_tier_bonus_img.pack(anchor="w", fill="x", pady=(0, 10))
        else:
            tk.Label(
                share_bonus_card,
                text=self._t("tier_bonus_image_missing"),
                anchor="w",
                justify="left",
                bg=MAIN_CARD_BG,
                fg=MAIN_FG_MUTED,
                font=(LOGIN_FONT_FAMILY, 8),
                wraplength=400,
            ).pack(anchor="w", pady=(0, 10))

        cb_kw_sb = {
            "bg": MAIN_CARD_BG,
            "fg": MAIN_FG,
            "selectcolor": "#0d3d47",
            "activebackground": MAIN_CARD_BG,
            "activeforeground": MAIN_FG,
            "highlightthickness": 0,
            "font": (LOGIN_FONT_FAMILY, 9),
        }
        cb_dis_kw_sb = {**cb_kw_sb, "fg": MAIN_FG_MUTED}
        tk.Label(
            share_bonus_card,
            text=self._t("share_group_section_title"),
            anchor="w",
            bg=MAIN_CARD_BG,
            fg="#ffffff",
            font=(LOGIN_FONT_FAMILY, 10, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        tk.Checkbutton(
            share_bonus_card,
            text=self._t("chk_fb"),
            variable=self._var_open_fb,
            **cb_kw_sb,
        ).pack(anchor="w", pady=(2, 0))
        tk.Checkbutton(
            share_bonus_card,
            text=self._t("chk_ig"),
            variable=self._var_open_ig,
            state="disabled",
            **cb_dis_kw_sb,
        ).pack(anchor="w", pady=(2, 0))
        tk.Checkbutton(
            share_bonus_card,
            text=self._t("chk_threads"),
            variable=self._var_open_threads,
            state="disabled",
            **cb_dis_kw_sb,
        ).pack(anchor="w", pady=(2, 0))
        tk.Checkbutton(
            share_bonus_card,
            text=self._t("chk_whatsapp"),
            variable=self._var_open_whatsapp,
            state="disabled",
            **cb_dis_kw_sb,
        ).pack(anchor="w", pady=(2, 0))

        def _sync_share_bonus_wrap(_event: tk.Event | None = None) -> None:
            try:
                w = int(share_bonus_card.winfo_width())
            except tk.TclError:
                return
            if w < 80:
                return
            wl = max(200, w - 28)
            fn = tkfont.Font(root=self.root, family=LOGIN_FONT_FAMILY, size=9)
            ch_w = max(6, (fn.measure("0") + fn.measure("中") + fn.measure("M")) // 3)
            chars = max(14, int(wl // ch_w))
            for t in self._share_wrap_texts:
                try:
                    if not t.winfo_exists():
                        continue
                    t.configure(state=tk.NORMAL)
                    t.configure(width=chars)
                    t.update_idletasks()
                    line = int(
                        t.count("1.0", "end", "update", "displaylines", return_ints=True)
                    )
                    t.configure(height=max(1, line))
                    t.configure(state=tk.DISABLED)
                except tk.TclError:
                    pass
            self._resize_tier_bonus_table_image(w)

        share_bonus_card.bind("<Configure>", lambda e: _sync_share_bonus_wrap(e))
        self.root.after_idle(lambda: _sync_share_bonus_wrap(None))

        # --- 相關參數（AI、帳號列；希望金額僅在上方訊息區調整）---
        param_card = tk.Frame(
            frame,
            bg=MAIN_CARD_BG,
            highlightbackground=MAIN_CARD_BORDER,
            highlightthickness=1,
            padx=12,
            pady=12,
        )
        param_card.pack(anchor="w", fill="x", pady=(0, 8))

        rb_kw = {
            "bg": MAIN_CARD_BG,
            "fg": MAIN_FG,
            "selectcolor": "#0d3d47",
            "activebackground": MAIN_CARD_BG,
            "activeforeground": MAIN_FG,
            "highlightthickness": 0,
            "font": (LOGIN_FONT_FAMILY, 9),
        }
        cb_kw = {**rb_kw}
        cb_dis_kw = {**rb_kw, "fg": MAIN_FG_MUTED}
        lbl_kw = {"bg": MAIN_CARD_BG, "fg": MAIN_FG_MUTED, "font": (LOGIN_FONT_FAMILY, 9)}

        self._var_ai = tk.StringVar(value="gemini")

        def _on_hope_var_write(*_a: object) -> None:
            self._sync_extra_hope_display()
            self._update_extra_balance_line()

        self._var_hope_amount.trace_add("write", _on_hope_var_write)

        # Facebook／IG／Threads 勾選已移至「分享、推薦與獎金」區塊

        tk.Label(
            param_card,
            text=self._t("related_params_title"),
            font=(LOGIN_FONT_FAMILY, 11, "bold"),
            bg=MAIN_CARD_BG,
            fg="#ffffff",
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))
        account_info_card = tk.Frame(param_card, bg=MAIN_CARD_BG)
        account_info_card.pack(anchor="w", fill="x", pady=(0, 0))
        ai_frame = tk.Frame(account_info_card, bg=MAIN_CARD_BG)
        ai_frame.pack(anchor="w", fill="x", pady=(0, 6))
        tk.Label(ai_frame, text=self._t("ai_system_label"), bg=MAIN_CARD_BG, fg=MAIN_FG_MUTED, font=(LOGIN_FONT_FAMILY, 9)).pack(
            side=tk.LEFT, padx=(0, 5)
        )
        tk.Radiobutton(ai_frame, text="Gemini", variable=self._var_ai, value="gemini", **rb_kw).pack(side=tk.LEFT, padx=2)
        tk.Radiobutton(ai_frame, text="OpenAI", variable=self._var_ai, value="openai", **rb_kw).pack(side=tk.LEFT, padx=2)
        info_frame = tk.Frame(account_info_card, bg=MAIN_CARD_BG)
        info_frame.pack(anchor="w", fill="x")
        info_frame.grid_columnconfigure(1, weight=1)
        self._dashboard_wrap_labels = []
        self._dash_wrap_last_w = -1

        val_kw = {
            "bg": MAIN_CARD_BG,
            "fg": MAIN_FG,
            "font": (LOGIN_FONT_FAMILY, 9),
            "anchor": "nw",
            "justify": "left",
        }
        base_rows = [
            (self._t("row_account"), "account"),
            (self._t("row_level"), "level"),
            (self._t("row_game_count"), "game_count"),
            (self._t("row_referral_code"), "referral_code"),
            (self._t("row_downline"), "downline_count"),
            (self._t("row_active"), "active_count"),
        ]
        for i, (label, key) in enumerate(base_rows):
            tk.Label(info_frame, text=f"{label}:", anchor="w", **lbl_kw).grid(
                row=i, column=0, sticky="nw", padx=(0, 10), pady=2
            )
            val = username if key == "account" else self._dashboard_data.get(key, "—")
            if key == "referral_code":
                lbl = tk.Label(
                    info_frame,
                    text=str(val),
                    anchor="w",
                    justify="left",
                    wraplength=0,
                    fg=MAIN_ACCENT,
                    cursor="hand2",
                    font=(LOGIN_FONT_FAMILY, 9, "underline"),
                    bg=MAIN_CARD_BG,
                )
                lbl.grid(row=i, column=1, sticky="w", pady=2)
                lbl.bind("<Button-1>", lambda e: self._copy_referral_code())
            else:
                lbl = tk.Label(info_frame, text=str(val), **val_kw)
                lbl.grid(row=i, column=1, sticky="new", pady=2)
                self._dashboard_wrap_labels.append(lbl)
            if key != "account":
                self._info_labels[key] = lbl

        def _sync_dash_value_wrap(event: tk.Event | None = None) -> None:
            try:
                w = int(account_info_card.winfo_width())
            except tk.TclError:
                return
            if w < 80:
                return
            if event is not None and w == self._dash_wrap_last_w:
                return
            self._dash_wrap_last_w = w
            wl = max(120, w - 32 - 200)
            for lb in self._dashboard_wrap_labels:
                try:
                    if lb.winfo_exists():
                        lb.config(wraplength=wl)
                except tk.TclError:
                    pass

        account_info_card.bind("<Configure>", _sync_dash_value_wrap)
        self.root.after_idle(lambda: _sync_dash_value_wrap(None))

        # self._var_play_roulette = tk.BooleanVar(value=False)
        # tk.Checkbutton(
        #     param_frame,
        #     text=self._t("chk_roulette"),
        #     variable=self._var_play_roulette,
        #     state="disabled",
        #     **cb_dis_kw,
        # ).grid(row=4, column=1, sticky="w", pady=2)
        # 「每小時玩樂透」「協助提領獎金」勾選已移至訊息框下方資訊欄

        if self._refresh_job:
            self.root.after_cancel(self._refresh_job)
        self._refresh_job = self.root.after(5 * 60 * 1000, self._auto_refresh)
        self._refresh_dashboard()

    def _cancel_ai_fake_timers(self) -> None:
        for aid in getattr(self, "_ai_fake_after_ids", []):
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
        self._ai_fake_after_ids = []

    def _stop_in_game_ai_marquee(self) -> None:
        """停止遊戲中文字輪播（不變更標題／內文）。"""
        self._ai_game_marquee_active = False
        aid = getattr(self, "_ai_game_marquee_after_id", None)
        if aid is not None:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
            self._ai_game_marquee_after_id = None

    def _clear_ai_marquee_idle(self) -> None:
        """停止遊戲中動畫並清空跑馬燈文字（若尚未按啟動則顯示就緒提示）。"""
        self._stop_in_game_ai_marquee()
        try:
            mbg = self._lbl_ai_msg.cget("bg") if hasattr(self, "_lbl_ai_msg") else MAIN_MARQUEE_BG
            if hasattr(self, "_lbl_ai_title") and self._lbl_ai_title.winfo_exists():
                self._lbl_ai_title.config(text="", bg=mbg)
            if hasattr(self, "_ai_msg_var"):
                if getattr(self, "_ai_show_ready_until_start", False):
                    self._ai_msg_var.set(self._t("ai_msg_ready_settings"))
                else:
                    self._ai_msg_var.set("")
            if hasattr(self, "_lbl_ai_msg") and self._lbl_ai_msg.winfo_exists():
                self._lbl_ai_msg.config(fg=MAIN_FG, bg=mbg)
        except tk.TclError:
            pass

    def _start_in_game_ai_marquee(self) -> None:
        """主遊戲執行中：輪播 ai_game_marquee_lines（多語系、每行一則）。"""
        if not hasattr(self, "_lbl_ai_msg") or not self._lbl_ai_msg.winfo_exists():
            return
        self._stop_in_game_ai_marquee()
        self._ai_game_marquee_active = True
        raw = self._t("ai_game_marquee_lines")
        msgs = [ln.strip() for ln in str(raw).splitlines() if ln.strip()]
        if not msgs:
            msgs = [self._t("ai_title_in_game")]
        self._ai_game_marquee_phase = 0
        try:
            mbg = self._lbl_ai_msg.cget("bg")
            if hasattr(self, "_lbl_ai_title") and self._lbl_ai_title.winfo_exists():
                self._lbl_ai_title.config(text=self._t("ai_title_in_game"), bg=mbg, fg=MAIN_ACCENT)
            self._lbl_ai_msg.config(fg=MAIN_FG, bg=mbg)
        except tk.TclError:
            pass
        self._ai_msg_var.set(msgs[0])
        n = len(msgs)

        def tick() -> None:
            try:
                if not self.root.winfo_exists() or not self._lbl_ai_msg.winfo_exists():
                    return
            except tk.TclError:
                return
            if not getattr(self, "_ai_game_marquee_active", False):
                return
            self._ai_game_marquee_phase = (getattr(self, "_ai_game_marquee_phase", 0) + 1) % n
            self._ai_msg_var.set(msgs[self._ai_game_marquee_phase])
            self._ai_game_marquee_after_id = self.root.after(IN_GAME_AI_MARQUEE_INTERVAL_MS, tick)

        self._ai_game_marquee_after_id = self.root.after(IN_GAME_AI_MARQUEE_INTERVAL_MS, tick)

    def _show_ai_dialog(self, duration_sec: int = 20, on_done=None) -> None:
        """在主介面即時資訊下方顯示 AI 策略跑馬燈（無獨立視窗）。"""
        if not hasattr(self, "_lbl_ai_msg") or not self._lbl_ai_msg.winfo_exists():
            if on_done:
                on_done()
            return
        self._stop_in_game_ai_marquee()
        self._cancel_ai_fake_timers()
        self._ai_fake_after_ids = []

        try:
            mbg = self._lbl_ai_msg.cget("bg")
            self._lbl_ai_title.config(bg=mbg, fg=MAIN_ACCENT)
            self._lbl_ai_msg.config(fg=MAIN_FG, bg=mbg)
        except tk.TclError:
            pass

        ai_name = "Gemini" if self._get_game_params().get("ai_system") == "gemini" else "OpenAI"
        p = self._get_game_params()
        msgs = [
            self._t("ai_msg_connecting", name=ai_name),
            self._t("ai_msg_history", name=ai_name),
            self._t("ai_msg_timing", name=ai_name),
            self._t("ai_msg_roulette_ev", name=ai_name),
        ]
        if p.get("play_lottery"):
            msgs.append(self._t("ai_msg_lottery", name=ai_name))
        if p.get("play_roulette"):
            msgs.append(self._t("ai_msg_roulette_enter", name=ai_name))
        msgs.extend(
            [
                self._t("ai_msg_conservative", name=ai_name),
                self._t("ai_msg_winrate", name=ai_name),
                self._t("ai_msg_ready", name=ai_name),
            ]
        )

        self._ai_msg_idx = 0
        self._ai_msgs = msgs
        self._lbl_ai_title.config(text=self._t("ai_analyzing_title", name=ai_name))
        self._ai_msg_var.set(msgs[0])

        def update_msg() -> None:
            self._ai_msg_idx += 1
            if self._ai_msg_idx < len(self._ai_msgs):
                self._ai_msg_var.set(self._ai_msgs[self._ai_msg_idx])
                aid = self.root.after(2500, update_msg)
                self._ai_fake_after_ids.append(aid)
            else:
                self._ai_msg_var.set(self._t("ai_done_line"))

        aid0 = self.root.after(2500, update_msg)
        self._ai_fake_after_ids.append(aid0)

        def finish_ai_bar() -> None:
            self._cancel_ai_fake_timers()
            try:
                mbg = self._lbl_ai_msg.cget("bg")
                if hasattr(self, "_lbl_ai_title") and self._lbl_ai_title.winfo_exists():
                    self._lbl_ai_title.config(text="", bg=mbg)
                if hasattr(self, "_ai_msg_var"):
                    self._ai_msg_var.set("")
                if hasattr(self, "_lbl_ai_msg") and self._lbl_ai_msg.winfo_exists():
                    self._lbl_ai_msg.config(fg=MAIN_FG, bg=mbg)
            except tk.TclError:
                pass
            if on_done:
                on_done()

        aid_end = self.root.after(duration_sec * 1000, finish_ai_bar)
        self._ai_fake_after_ids.append(aid_end)

    def _show_ai_strategy_marquee_completed(self) -> None:
        """本輪遊戲依 betCount 達標後，於 AI 策略跑馬燈顯示「已完成AI策略」（可從非主執行緒呼叫）。"""
        def apply() -> None:
            try:
                if not self.root.winfo_exists():
                    return
                self._stop_in_game_ai_marquee()
                self._cancel_ai_fake_timers()
                if hasattr(self, "_lbl_ai_title") and self._lbl_ai_title.winfo_exists():
                    try:
                        mbg = self._lbl_ai_msg.cget("bg")
                        self._lbl_ai_title.config(text="", bg=mbg)
                    except tk.TclError:
                        self._lbl_ai_title.config(text="")
                if hasattr(self, "_ai_msg_var"):
                    self._ai_msg_var.set(self._t("ai_strategy_done"))
                if hasattr(self, "_lbl_ai_msg") and self._lbl_ai_msg.winfo_exists():
                    try:
                        mbg = self._lbl_ai_msg.cget("bg")
                        self._lbl_ai_msg.config(fg=MAIN_ACCENT, bg=mbg)
                    except tk.TclError:
                        pass
            except tk.TclError:
                pass

        try:
            if hasattr(self, "root") and self.root.winfo_exists():
                self.root.after(0, apply)
        except tk.TclError:
            pass

    def _create_driver(self) -> tuple[webdriver.Chrome, WebDriverWait]:
        options = Options()
        options.add_argument("--mute-audio")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options_hide_automation_infobar(options)
        chrome_options_suppress_prompts(options)

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        chrome_driver_patch_automation_detection(driver)
        wait = WebDriverWait(driver, 15)
        return driver, wait

    def _fb_cookies_path(self) -> Path:
        return app_base_dir() / FACEBOOK_COOKIES_FILE

    def _save_fb_cookies(self, driver: webdriver.Chrome) -> None:
        """將 Facebook 瀏覽器 Cookie 寫入本機（供下次還原登入）。"""
        try:
            cookies = driver.get_cookies()
            path = self._fb_cookies_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            print(f"[FB分享] 已儲存 {len(cookies)} 筆 Cookie → {path}")
        except Exception as e:
            print(f"[FB分享] 儲存 Cookie 失敗: {e}")

    def _load_and_apply_fb_cookies(self, driver: webdriver.Chrome) -> bool:
        """若存在本機 Cookie 檔，注入後 refresh。回傳是否成功讀取並嘗試套用。"""
        path = self._fb_cookies_path()
        if not path.is_file():
            return False
        try:
            with open(path, encoding="utf-8") as f:
                cookies = json.load(f)
        except Exception as e:
            print(f"[FB分享] 讀取 Cookie 檔失敗: {e}")
            return False
        if not isinstance(cookies, list) or not cookies:
            return False
        try:
            driver.get(FB_HOME_URL)
            time.sleep(1)
            now = time.time()
            added = 0
            for c in cookies:
                if not isinstance(c, dict):
                    continue
                exp = c.get("expiry")
                if exp is not None:
                    try:
                        if float(exp) < now:
                            continue
                    except (TypeError, ValueError):
                        pass
                allowed: dict = {}
                for k in ("name", "value", "domain", "path", "expiry", "secure", "httpOnly", "sameSite"):
                    if k in c and c[k] is not None:
                        allowed[k] = c[k]
                if "name" not in allowed or "value" not in allowed:
                    continue
                if "expiry" in allowed:
                    try:
                        allowed["expiry"] = int(float(allowed["expiry"]))
                    except (TypeError, ValueError):
                        del allowed["expiry"]
                try:
                    driver.add_cookie(allowed)
                    added += 1
                except Exception:
                    pass
            driver.refresh()
            time.sleep(2)
            print(f"[FB分享] 已套用本機 Cookie（成功寫入 {added} 筆）")
            return True
        except Exception as e:
            print(f"[FB分享] 套用 Cookie 失敗: {e}")
            return False

    def _quit_fb_share_browser(self) -> None:
        """關閉 Facebook 分享專用瀏覽器。"""
        if self._fb_driver is None:
            return
        try:
            try:
                if self._fb_share_session_logged_in(self._fb_driver):
                    self._save_fb_cookies(self._fb_driver)
            except Exception:
                pass
            self._fb_driver.quit()
        except Exception:
            pass
        self._fb_driver = None

    @staticmethod
    def _fb_share_session_logged_in(driver: webdriver.Chrome) -> bool:
        """Facebook 登入後通常會有 c_user cookie。"""
        try:
            for c in driver.get_cookies():
                if c.get("name") == "c_user" and str(c.get("value", "")).strip():
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _fb_page_indicates_group_content_unavailable(driver: webdriver.Chrome) -> bool:
        """群組連結失效、無權限等時 FB 常出現的提示；此時應重新向 API 取網址再 driver.get。"""
        try:
            src = driver.page_source or ""
        except Exception:
            return False
        markers = (
            "目前無法查看此內容",
            "This content isn't available right now",
            "This content isn't available at the moment",
        )
        return any(m in src for m in markers)

    def _fb_send_keys_promo_to_active_element(self, driver: webdriver.Chrome, code: str) -> bool:
        """點「留個言吧」後輸入框已聚焦：用 ActionChains 送鍵到「瀏覽器目前焦點」，避免 active_element 的 WebElement 因 FB 重繪變 stale。"""
        if self._stop_requested:
            return False
        try:
            time.sleep(random.uniform(1.4, 3.2))
            ActionChains(driver).send_keys(Keys.ENTER).perform()
            time.sleep(random.uniform(1.4, 3.2))
            ActionChains(driver).send_keys(code).perform()
            time.sleep(random.uniform(1.4, 3.2))
            ActionChains(driver).send_keys(Keys.ENTER).perform()
        except Exception as e:
            print(f"[FB分享] 無法對焦點輸入推廣碼: {type(e).__name__}: {e}")
            return False
        time.sleep(random.uniform(3.2, 6.2))
        print("[FB分享] 已貼上推廣碼。")
        return True

    # 留言／發文送出鈕的 aria-label（精確比對，避免誤點其他「Post」連結）
    _FB_PUBLISH_ARIA_LABELS = (
        "發佈",
        "发布",
        "Post",
        "Comment",
        "Publish",
        "Publicar",
        "Publier",
        "Veröffentlichen",
        "投稿",
    )

    def _fb_try_click_publish_submit(self, driver: webdriver.Chrome) -> bool:
        """貼上內容後點「發佈」等送出鈕；按鈕可能稍晚才出現，故短時間輪詢。"""
        deadline = time.time() + 20
        span_xpath_zh = (
            "//span[normalize-space()='發佈']/ancestor::div[@role='button'][1]",
            "//span[normalize-space()='发布']/ancestor::div[@role='button'][1]",
        )
        while time.time() < deadline and not self._stop_requested:
            for label in self._FB_PUBLISH_ARIA_LABELS:
                try:
                    xp = f"//div[@role='button' and normalize-space(@aria-label)='{label}']"
                    for btn in driver.find_elements(By.XPATH, xp):
                        try:
                            if not btn.is_displayed():
                                continue
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                            time.sleep(random.uniform(0.25, 0.5))
                            try:
                                ActionChains(driver).move_to_element(btn).click().perform()
                            except Exception:
                                driver.execute_script("arguments[0].click();", btn)
                            print(f"[FB分享] 已點擊發佈／送出（aria-label={label!r}）")
                            return True
                        except Exception:
                            continue
                except Exception:
                    continue
            for xp in span_xpath_zh:
                try:
                    for btn in driver.find_elements(By.XPATH, xp):
                        try:
                            if not btn.is_displayed():
                                continue
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                            time.sleep(random.uniform(0.25, 0.5))
                            try:
                                ActionChains(driver).move_to_element(btn).click().perform()
                            except Exception:
                                driver.execute_script("arguments[0].click();", btn)
                            print("[FB分享] 已點擊發佈（span 文案 → role=button）")
                            return True
                        except Exception:
                            continue
                except Exception:
                    continue
            time.sleep(0.4)
        return False

    # Facebook 發文／留言觸發鈕的 aria-label 常見片段（多語系，長字串優先比對減少誤點）
    _FB_COMPOSER_ARIA_NEEDLES = (
        "create a public post",
        "write something",
        "what's on your mind",
        "create post",
        "write on this",
        "write on",
        "publicación",  # es
        "escribe algo",  # es
        "publicar",  # es/pt
        "écrire quelque chose",  # fr
        "publier",  # fr
        "viết bài viết",  # vi
        "viết nội dung",  # vi
        "schreib etwas",  # de
        "beitrag",  # de
        "kommentieren",  # de comment
        "コメント",  # ja
        "投稿",  # ja/zh
        "留言",  # zh-HK/TW 等
        "發文",  # zh-TW
        "寫點什麼",  # zh（備援，非唯一依賴）
    )

    @staticmethod
    def _fb_try_click_leave_comment_span_trigger(driver: webdriver.Chrome) -> bool:
        """繁中介面「留個言吧……」：依 span 文案 + 截斷樣式（與你提供的 DOM）向上找可點擊的 role=button。"""
        xpaths = (
            # 最準：文案 + style 含 line-clamp（不依賴易變的 x1lliihq class）
            "//span[contains(.,'留個言吧') and contains(@style,'-webkit-line-clamp')]/ancestor::div[@role='button'][1]",
            "//span[contains(.,'留個言吧') and contains(@style,'-webkit-box')]/ancestor::div[@role='button'][1]",
            # 備援：同文案 + FB 常見 utility class（改版可能失效）
            "//span[contains(@class,'x1lliihq') and contains(.,'留個言吧')]/ancestor::div[@role='button'][1]",
            # 最寬鬆：僅文案（僅在 zh-TW/zh-HK 介面有效）
            "//span[contains(.,'留個言吧')]/ancestor::div[@role='button'][1]",
        )
        for xp in xpaths:
            try:
                for el in driver.find_elements(By.XPATH, xp):
                    try:
                        if not el.is_displayed():
                            continue
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        time.sleep(random.uniform(0.55, 1.0))
                        try:
                            ActionChains(driver).move_to_element(el).click().perform()
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        print("[FB分享] 已點擊「留個言吧」對應區塊（span → role=button）")
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _fb_try_click_composer_trigger(self, driver: webdriver.Chrome) -> bool:
        """掃描 role=button 且帶 aria-label 的元件，以多語關鍵字點開留言區。"""
        needles = sorted(self._FB_COMPOSER_ARIA_NEEDLES, key=len, reverse=True)
        deadline = time.time() + 28
        while time.time() < deadline and not self._stop_requested:
            try:
                for btn in driver.find_elements(By.XPATH, "//div[@role='button'][@aria-label]"):
                    try:
                        if not btn.is_displayed():
                            continue
                        raw = btn.get_attribute("aria-label") or ""
                        al = raw.lower()
                        if len(al) < 4 or len(al) > 220:
                            continue
                        for nd in needles:
                            if nd.lower() in al:
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                                time.sleep(random.uniform(0.55, 1.0))
                                try:
                                    ActionChains(driver).move_to_element(btn).click().perform()
                                except Exception:
                                    driver.execute_script("arguments[0].click();", btn)
                                print(f"[FB分享] 已點擊發文／留言觸發（aria 關鍵字: {nd[:30]}…）")
                                return True
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.45)
        return False

    def _fb_share_post_promo_to_group(self, driver: webdriver.Chrome) -> None:
        """登入成功後：get_group_link → 進群組 → 開啟留言／發文框 → 貼推廣碼 → 按發佈。結束後關閉 FB 瀏覽器（與遊戲站同理，避免下次殘留狀態）。"""
        if self._stop_requested:
            self._quit_fb_share_browser()
            return
        try:
            code = str(self._dashboard_data.get("referral_code", "") or "").strip()
            if not code or code == "—":
                print("[FB分享] 無推廣碼（referral_code），無法貼上。")
                return

            max_group_load_attempts = 5
            try:
                for attempt in range(max_group_load_attempts):
                    if self._stop_requested:
                        return
                    group_url = API.get_group_link(FB_GROUP_LINK_COUNTRY)
                    if not group_url:
                        print("[FB分享] 未取得群組連結，略過留言。")
                        return
                    print(f"[FB分享] 前往群組 ({attempt + 1}/{max_group_load_attempts}): {group_url}")
                    driver.get(group_url)
                    time.sleep(random.uniform(3.5, 5.5))
                    if not LoginApp._fb_page_indicates_group_content_unavailable(driver):
                        break
                    print("[FB分享] 頁面顯示「目前無法查看此內容」等，改向 API 取新連結並重新載入…")
                    time.sleep(random.uniform(0.6, 1.2))
                else:
                    print("[FB分享] 已達重試上限仍無法開啟群組頁，略過留言。")
                    return

                # 先點正確的「留個言吧」觸發區，避免誤抓頁面上其他 contenteditable
                opened_by_leave = self._fb_try_click_leave_comment_span_trigger(driver)
                if not opened_by_leave:
                    if not self._fb_try_click_composer_trigger(driver):
                        print("[FB分享] 無法開啟留言／發文區（可檢查語系、群組權限或介面改版）。")
                        return
                    time.sleep(random.uniform(0.9, 1.7))
                else:
                    print("[FB分享] 已點「留個言吧」，等待留言對話框出現…")
                    time.sleep(random.uniform(2.2, 4.2))
                if not self._fb_send_keys_promo_to_active_element(driver, code):
                    return
                time.sleep(random.uniform(0.4, 0.85))
                if not self._fb_try_click_publish_submit(driver):
                    print("[FB分享] 未偵測到可點擊的「發佈」按鈕，請手動送出留言。")
            except Exception as e:
                print(f"[FB分享] 群組留言流程失敗: {type(e).__name__}: {e}")
        finally:
            print("[FB分享] 本次流程結束，關閉 Facebook 瀏覽器（已登入時會先儲存 Cookie）。")
            self._quit_fb_share_browser()

    def _start_facebook_share_browser_and_wait_login(self) -> None:
        """另開 Chrome 開啟 www.facebook.com，背景執行緒輪詢直到登入成功或停止。"""

        def worker() -> None:
            try:
                d = self._fb_driver
                if d is not None:
                    try:
                        d.window_handles
                    except Exception:
                        self._quit_fb_share_browser()
                        d = None

                if d is None:
                    print("[FB分享] 正在開啟新瀏覽器前往 Facebook…")
                    opts = Options()
                    opts.add_argument("--mute-audio")
                    opts.add_argument("--start-maximized")
                    opts.add_argument("--disable-blink-features=AutomationControlled")
                    chrome_options_hide_automation_infobar(opts)
                    chrome_options_suppress_prompts(opts)
                    svc = Service(ChromeDriverManager().install())
                    d = webdriver.Chrome(service=svc, options=opts)
                    chrome_driver_patch_automation_detection(d)
                    self._fb_driver = d
                    had_file = self._load_and_apply_fb_cookies(d)
                    if not had_file:
                        d.get(FB_HOME_URL)
                    if had_file and self._fb_share_session_logged_in(d):
                        print("[FB分享] 本機 Cookie 仍有效，已還原登入。")
                        self._save_fb_cookies(d)
                        self._fb_share_post_promo_to_group(d)
                        self.root.after(
                            0,
                            # lambda: messagebox.showinfo(
                            #     "Facebook",
                            #     "已使用本機儲存的 Cookie 還原 Facebook 登入。",
                            # ),
                        )
                        return
                    if had_file:
                        print("[FB分享] 本機 Cookie 已過期或無效，請重新登入。")
                    else:
                        print("[FB分享] 請在新視窗完成 Facebook 登入；偵測成功後會提示。")
                else:
                    if self._fb_share_session_logged_in(d):
                        print("[FB分享] 既有視窗已登入 Facebook。")
                        self._fb_share_post_promo_to_group(d)
                        self.root.after(
                            0,
                            # lambda: messagebox.showinfo("Facebook", "Facebook 已處於登入狀態。"),
                        )
                        return
                    print("[FB分享] 沿用既有 Facebook 視窗，等待登入…")
                    try:
                        cur = (d.current_url or "").lower()
                        if "facebook.com" not in cur:
                            d.get(FB_HOME_URL)
                    except Exception:
                        try:
                            d.get(FB_HOME_URL)
                        except Exception:
                            pass

                while not self._stop_requested:
                    try:
                        if self._fb_share_session_logged_in(d):
                            self._save_fb_cookies(d)
                            print("[FB分享] 已偵測登入成功，Cookie 已寫入本機。")
                            self._fb_share_post_promo_to_group(d)
                            self.root.after(
                                0,
                                # lambda: messagebox.showinfo("Facebook", "Facebook 登入成功。"),
                            )
                            break
                    except NoSuchWindowException:
                        print("[FB分享] Facebook 視窗已關閉。")
                        self._fb_driver = None
                        break
                    time.sleep(2)
            except Exception as e:
                print(f"[FB分享] 錯誤: {type(e).__name__}: {e}")
                self._quit_fb_share_browser()

        threading.Thread(target=worker, daemon=True).start()

    def _get_or_create_driver(self) -> tuple[webdriver.Chrome, WebDriverWait, bool]:
        """取得現有瀏覽器或建立新的。回傳 (driver, wait, is_new)"""
        if self._driver is not None:
            try:
                handles = self._driver.window_handles
                if handles and self._wait is not None:
                    return self._driver, self._wait, False
            except Exception:
                pass
            self._driver = None
            self._wait = None

        driver, wait = self._create_driver()
        self._driver = driver
        self._wait = wait
        return driver, wait, True

    @staticmethod
    def _login_error_detected(driver: webdriver.Chrome) -> bool:
        """網頁出現帳密錯誤區塊（#error-msg.bg-danger 等）。"""
        try:
            el = driver.find_element(By.ID, "error-msg")
            if not el.is_displayed():
                return False
            cls = el.get_attribute("class") or ""
            tx = el.text or ""
            if "bg-danger" in cls:
                return True
            if "帳號" in tx and "密碼" in tx:
                return True
            if "錯誤" in tx and ("帳號" in tx or "密碼" in tx):
                return True
        except (NoSuchElementException, StaleElementReferenceException):
            pass
        return False

    def _handle_login_failure(self) -> None:
        """關閉瀏覽器、回到登入畫面、提示帳密錯誤（僅在主執行緒更新 UI）。"""
        try:
            if self._driver:
                try:
                    self._driver.quit()
                except Exception:
                    pass
                self._driver = None
                self._wait = None
        except Exception:
            self._driver = None
            self._wait = None
        self._quit_fb_share_browser()
        self._is_platform_running = False
        self._worker_running = False
        self._stop_requested = True
        if getattr(self, "_refresh_job", None):
            try:
                self.root.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None
        try:
            if hasattr(self, "_btn_start_stop") and self._btn_start_stop.winfo_exists():
                self._btn_start_stop.config(text=self._t("btn_start"))
        except Exception:
            pass
        self.show_login_frame()
        messagebox.showerror(self._t("err_login_failed"), self._t("err_bad_credentials"))

    def _schedule_login_failure_ui(self) -> None:
        """從背景執行緒呼叫時，排程回主執行緒處理。"""
        self.root.after(0, self._handle_login_failure)

    def _login_site(self, driver: webdriver.Chrome, wait: WebDriverWait, acc: str, pwd: str) -> bool:
        """在遊戲站台執行登入流程。成功 True；帳密錯誤（#error-msg）False。"""
        print("開始網站登入流程")

        login_btn = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[@class='button--solid' and contains(text(), '登入')]")
            )
        )
        login_btn.click()
        time.sleep(3)

        username_field = wait.until(EC.presence_of_element_located((By.ID, "username")))
        username_field.clear()
        username_field.send_keys(acc)

        password_field = wait.until(EC.presence_of_element_located((By.ID, "password")))
        password_field.clear()
        password_field.send_keys(pwd)

        wait.until(EC.element_to_be_clickable((By.ID, "btnLogon"))).click()

        try:
            WebDriverWait(driver, 12).until(lambda d: self._login_error_detected(d))
            print("偵測到登入錯誤訊息（帳號或密碼錯誤）")
            return False
        except TimeoutException:
            pass

        time.sleep(1)
        if self._login_error_detected(driver):
            print("偵測到登入錯誤訊息（帳號或密碼錯誤）")
            return False

        time.sleep(4)
        return True

    def toggle_start_stop(self) -> None:
        """啟動/停止：點啟動開始執行，點停止可中斷目前操作"""
        if self._is_platform_running:
            self._is_platform_running = False
            self._worker_running = False
            self._stop_requested = True
            self._btn_start_stop.config(text=self._t("btn_start"))
            if self._driver:
                try:
                    self._driver.quit()
                except Exception:
                    pass
                self._driver = None
                self._wait = None
            self._quit_fb_share_browser()
            self._rest_countdown_hide()
            print("已停止")
        else:
            # 須先清除，否則 open_platform 內啟動的 FB 等待迴圈會因 _stop_requested 仍為 True 而立刻結束
            self._stop_requested = False
            acc = self.userinfo.get("username", "")
            pwd = self.userinfo.get("password", "")
            if not acc or not pwd:
                messagebox.showerror(self._t("err_title"), self._t("err_no_account_info"))
                return

            # 先同步錢包再比對希望金額，避免餘額已達標仍先開啟遊戲站台瀏覽器
            self._sync_dashboard_from_api()
            params = self._get_game_params()
            win_amount = params["win_amount"]
            over_hope = self._wallet_over_hope_amount(win_amount)
            if over_hope and not params["open_fb"]:
                bal_disp = format_wallet_balance_display(self._dashboard_data.get("balance", "—"))
                messagebox.showinfo(
                    self._t("hint_title"),
                    self._t("msg_balance_over_hope", bal=bal_disp, hope=win_amount),
                )
                return
            if over_hope and params["open_fb"]:
                bal_disp = format_wallet_balance_display(self._dashboard_data.get("balance", "—"))
                messagebox.showinfo(
                    self._t("hint_title"),
                    self._t("msg_balance_over_hope", bal=bal_disp, hope=win_amount),
                )

            # 餘額已達標且要 FB 時略過遊戲站台登入，只開 FB 分享用瀏覽器
            skip_site_login = bool(over_hope and params["open_fb"])
            if not self.open_platform(skip_site_login=skip_site_login):
                return

            self._ai_show_ready_until_start = False
            self._clear_ai_marquee_idle()

            self._sync_dashboard_from_api()
            self._is_platform_running = True
            self._worker_running = True
            self._btn_start_stop.config(text=self._t("btn_stop"))
            run_fb_registration_in_background(self.userinfo.get("username", ""))
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._worker_thread = threading.Thread(target=self._run_worker_loop, daemon=True)
                self._worker_thread.start()

    def _close_game_browser(self) -> None:
        """關閉遊戲站台用 Chrome；下次週期由 _get_or_create_driver 重建，避免殘留分頁／重複登入狀態錯亂。"""
        if self._driver is None:
            return
        try:
            self._driver.quit()
        except Exception as e:
            print(f"關閉遊戲瀏覽器: {type(e).__name__}: {e}")
        self._driver = None
        self._wait = None

    def check_update(self) -> None:
        """檢查遠端清單並下載覆寫 test.py／version_info.py（與 launcher 相同邏輯）；成功後重新啟動程式。"""
        root_dir = app_base_dir()
        url = get_manifest_url(root_dir)
        if not url:
            messagebox.showinfo(
                self._t("update_title"),
                self._t("update_no_manifest_url"),
            )
            return

        def worker() -> None:
            try:
                status, msg = check_and_apply_update(url)
                self.root.after(0, lambda: self._check_update_ui_done(status, msg))
            except Exception as e:
                self.root.after(0, lambda: self._check_update_ui_done("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _check_update_ui_done(self, status: str, msg: str) -> None:
        try:
            if status == "updated":
                messagebox.showinfo(
                    self._t("update_title"),
                    self._t("update_success_restart", version=msg),
                )
                self._restart_application_after_update()
            elif status == "latest":
                messagebox.showinfo(
                    self._t("update_title"),
                    self._t("update_latest_detail", version=msg),
                )
            else:
                messagebox.showerror(
                    self._t("update_title"),
                    self._t("update_failed_detail", detail=msg),
                )
        except tk.TclError:
            pass

    def _restart_application_after_update(self) -> None:
        """覆寫檔案後以新行程取代目前行程（開發：python test.py；打包：同一路徑 exe）。"""
        root_dir = app_base_dir()
        try:
            if getattr(sys, "frozen", False):
                os.execv(sys.executable, [sys.executable])
            else:
                script = root_dir / "test.py"
                os.execv(sys.executable, [sys.executable, str(script)])
        except OSError as e:
            print(f"[更新] 無法重新啟動: {e}")

    @staticmethod
    def _format_hms(total_sec: int) -> str:
        s = max(0, int(total_sec))
        h, r = divmod(s, 3600)
        m, sec = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"

    def _rest_countdown_hide(self) -> None:
        """結束休息倒數，清空 AI 跑馬燈區。"""
        self._clear_ai_marquee_idle()

    def _rest_countdown_update(self, seconds_left: int) -> None:
        """在主執行緒於 AI 跑馬燈顯示休息倒數（須由 root.after 呼叫）。"""
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        if not hasattr(self, "_lbl_ai_msg") or not self._lbl_ai_msg.winfo_exists():
            return
        self._stop_in_game_ai_marquee()
        self._cancel_ai_fake_timers()
        hms = self._format_hms(seconds_left)
        mbg = MAIN_MARQUEE_BG
        try:
            if hasattr(self, "_lbl_ai_title") and self._lbl_ai_title.winfo_exists():
                self._lbl_ai_title.config(text=self._t("rest_title"), bg=mbg, fg=MAIN_ACCENT)
            self._ai_msg_var.set(self._t("rest_body", hms=hms))
            if hasattr(self, "_lbl_ai_msg") and self._lbl_ai_msg.winfo_exists():
                self._lbl_ai_msg.config(fg=MAIN_FG, bg=mbg)
        except tk.TclError:
            pass

    def _run_worker_loop(self) -> None:
        """背景週期：跑完一輪後若 WORKER_CYCLE_REST_SEC>0 才休息並顯示倒數；0 則立即下一輪。"""
        while self._worker_running:
            try:
                self._run_one_cycle()
            except Exception as e:
                print(f"執行緒錯誤: {e}")
            if not self._worker_running:
                break
            wait_sec = WORKER_CYCLE_REST_SEC
            if wait_sec <= 0:
                continue
            for sec_left in range(wait_sec, 0, -1):
                if not self._worker_running:
                    break
                try:
                    self.root.after(0, lambda s=sec_left: self._rest_countdown_update(s))
                except tk.TclError:
                    break
                time.sleep(1)
            try:
                self.root.after(0, self._rest_countdown_hide)
            except tk.TclError:
                pass

    def _run_one_cycle(self) -> None:
        """執行一輪：檢查贏額→AI對話期間樂透/輪盤→（玩遊戲前領獎）→玩遊戲→FB/IG/Threads"""
        self._sync_dashboard_from_api()
        params = self._get_game_params()

        win_amount = params["win_amount"]
        skip_play_game = self._wallet_over_hope_amount(win_amount)
        if skip_play_game:
            print(
                f"[週期] 錢包餘額 {format_wallet_balance_display(self._dashboard_data.get('balance', '—'))} 已達或超過希望金額 {win_amount}，跳過玩遊戲"
            )

        # 2. 假裝詢問 AI（20秒）期間，背景執行樂透／輪盤
        def lottery_roulette_during_ai() -> None:
            if not self._worker_running:
                return
            if params["play_lottery"]:
                self._do_play_lottery()
            if params["play_roulette"] and self._driver:
                self._do_play_roulette_once()

        threading.Thread(target=lottery_roulette_during_ai, daemon=True).start()
        if self.root.winfo_exists():
            self.root.after(0, lambda: self._show_ai_dialog(20))
        time.sleep(20)

        # 3. 玩五選1主遊戲：單次進入後持續 SPIN 直到錢包餘額>=希望金額（以 API balance／betCount 為準）
        #    有要玩遊戲時，領獎改在 _execute_play_game 內（登入／關廣告後、進遊戲前）執行
        ran_game_this_cycle = bool(self._driver and not skip_play_game)
        if params["claim_rewards"] and self._driver and not ran_game_this_cycle:
            self._do_claim_rewards()
        if ran_game_this_cycle:
            self._do_play_game_until_hope_met(win_amount)

        if ran_game_this_cycle:
            self._close_game_browser()

        # 5. FB/IG/Threads 分享（依參數；Facebook 登入另開瀏覽器已在「啟動」時處理）
        if params["open_fb"] and self._driver:
            pass  # TODO: FB 貼文7天一次、社團8h 4則、每小時留言5則（使用 _fb_driver）
        if params["open_ig"] and self._driver:
            pass  # TODO: IG 8小時不超過4則
        if params["open_threads"] and self._driver:
            pass  # TODO: Threads 8小時不超過4則
        if params["open_whatsapp"] and self._driver:
            pass  # TODO: Whatsapp 分享

        # 樂透／輪盤已在步驟 2（AI 對話框期間）執行

    def _do_play_game(self) -> None:
        """玩遊戲：進入後持續 SPIN 直到餘額達希望金額（或停止）。"""
        try:
            self._execute_play_game()
        except Exception as e:
            print(f"玩遊戲錯誤: {e}")

    def _do_play_game_until_hope_met(self, win_amount: int, *, from_worker: bool = True) -> None:
        """進入遊戲後一路打到錢包餘額>=希望金額，或收到停止／worker 結束。"""
        if self._stop_requested:
            return
        if from_worker and not self._worker_running:
            return
        self._sync_dashboard_from_api()
        if self._wallet_over_hope_amount(win_amount):
            print(f"[遊戲] 錢包餘額已達希望金額 {win_amount}，跳過玩遊戲")
            return
        self._do_play_game()

    def _do_claim_rewards(self) -> None:
        """前往獎勵中心頁，若有 gamePoint「領獎」按鈕（data-url 含 ajaxGamePointReward/item/…）則依序點擊；
        每次領取後與結束時嘗試關閉 SweetAlert2 OK；若完全無可領按鈕則改前往遊戲列表頁以利後續進遊戲。"""
        driver = self._driver
        if not driver:
            return
        base = site_origin_base_url()
        if not base:
            print("[領獎] 無法由目前站台網址解析 origin")
            return
        award_url = f"{base}{MEMBER_AWARD_CENTER_PATH}"
        games_url = f"{base}{SITE_GAMES_PATH}"
        try:
            driver.switch_to.default_content()
            handles = driver.window_handles
            if len(handles) > 1:
                driver.switch_to.window(handles[0])
            print(f"[領獎] 前往 {award_url}")
            driver.get(award_url)
            wait = WebDriverWait(driver, 20)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(0.5)
            # d-type、data-url、button id 皆可能隨站台改版，只依屬性與路徑片段辨識；每輪重查避免點擊後 DOM 刷新造成 stale
            clicked = 0
            max_clicks = 30
            while clicked < max_clicks and not self._stop_requested:
                buttons = driver.find_elements(By.CSS_SELECTOR, 'button[d-type="gamePoint"]')
                if not buttons:
                    buttons = driver.find_elements(By.XPATH, "//button[@d-type='gamePoint']")
                btn = None
                du = ""
                for b in buttons:
                    try:
                        cand = b.get_attribute("data-url") or ""
                        if GAME_POINT_REWARD_URL_MARKER in cand:
                            btn, du = b, cand
                            break
                    except Exception:
                        continue
                if btn is None:
                    if clicked == 0:
                        print("[領獎] 頁面上無可領取的 gamePoint 按鈕（data-url 需含 ajaxGamePointReward/item/）")
                    break
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.15)
                    try:
                        WebDriverWait(driver, 8).until(EC.element_to_be_clickable(btn))
                    except Exception:
                        pass
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    clicked += 1
                    print(f"[領獎] 已點領取 ({clicked}) data-url={du!r}")
                    time.sleep(0.5)
                    try_click_swal2_confirm_ok(driver, timeout_sec=5.0)
                    time.sleep(0.35)
                except Exception as e:
                    print(f"[領獎] 點擊失敗: {type(e).__name__}: {e}")
                    break
            if clicked > 0:
                try_click_swal2_confirm_ok(driver, timeout_sec=6.0)
                print(f"[領獎] 領取流程結束，前往遊戲列表: {games_url}")
            else:
                print(f"[領獎] 無可領取按鈕，前往遊戲列表: {games_url}")
            driver.get(games_url)
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(0.4)
        except Exception as e:
            print(f"[領獎] 錯誤: {type(e).__name__}: {e}")

    def _load_lottery_record(self) -> dict:
        """讀取樂透紀錄（每個整點是否已玩過）"""
        if os.path.exists(LOTTERY_RECORD_FILE):
            try:
                with open(LOTTERY_RECORD_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_lottery_record(self, username: str, hour_key: str) -> None:
        """儲存樂透紀錄（依帳號與整點）"""
        try:
            record = self._load_lottery_record()
            if "hours" not in record:
                record["hours"] = {}
            record["hours"][username] = hour_key
            with open(LOTTERY_RECORD_FILE, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"儲存樂透紀錄失敗: {e}")

    def _do_play_lottery(self) -> None:
        """樂透：00~99 隨機下注 1 個號碼，每個整點每帳號最多玩 1 次"""
        username = self.userinfo.get("username", "")
        if not username:
            return
        current_hour = time.strftime("%Y-%m-%d-%H")
        record = self._load_lottery_record()
        last = record.get("hours", {}).get(username, "")
        if last == current_hour:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 本整點已玩過樂透，跳過")
            return
        number = f"{random.randint(0, 99):02d}"
        result = API.lottery_bet(username, number)
        self._save_lottery_record(username, current_hour)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 玩樂透：下注號碼 {number}")

    def _do_play_roulette_once(self) -> None:
        """玩一次輪盤"""
        # TODO: 可呼叫現有輪盤邏輯一次
        print("玩輪盤一次...")

    def open_platform(self, skip_site_login: bool = False) -> bool:
        """登入網站後停下來，保留瀏覽器不關閉。成功回傳 True；帳密錯誤回傳 False 並已處理 UI。
        skip_site_login=True 時不開啟／登入遊戲站台（僅依參數執行 Facebook 分享等），供餘額已達或超過希望金額且仍要 FB 時使用。"""
        acc = self.userinfo.get("username", "")
        pwd = self.userinfo.get("password", "")
        if not acc or not pwd:
            messagebox.showerror(self._t("err_title"), self._t("err_no_account_info"))
            return False

        params = self._get_game_params()
        if skip_site_login:
            if not params["open_fb"]:
                return True
            try:
                self._start_facebook_share_browser_and_wait_login()
                if params["open_ig"]:
                    pass
                return True
            except Exception as e:
                print(f"開啟平台錯誤 (僅 FB): {type(e).__name__}: {e}")
                return False

        try:
            driver, wait, is_new = self._get_or_create_driver()
            if is_new:
                guest_url = self._guest_site_url()
                print(f"正在前往: {guest_url}")
                driver.get(guest_url)
                if not self._login_site(driver, wait, acc, pwd):
                    self._handle_login_failure()
                    return False
                print("平台已登入（瀏覽器保持開啟）")
            else:
                print("使用現有瀏覽器（平台已開啟）")
            if params["open_fb"]:
                self._start_facebook_share_browser_and_wait_login()
            if params["open_ig"]:
                # driver.execute_script("window.open('https://www.instagram.com');")
                pass
            return True
        except Exception as e:
            print(f"開啟平台錯誤: {type(e).__name__}: {e}")
            return False

    def play_lottery(self) -> None:
        if not self._get_game_params()["play_lottery"]:
            messagebox.showinfo(self._t("hint_title"), self._t("lottery_enable_hint"))
            return
        messagebox.showinfo(self._t("hint_title"), self._t("lottery_not_impl"))

    def play_game(self) -> None:
        """在背景執行主遊戲（若仍有外部呼叫）"""
        acc = self.userinfo.get("username", "")
        pwd = self.userinfo.get("password", "")
        if not acc or not pwd:
            messagebox.showerror(self._t("err_title"), self._t("err_no_account_info"))
            return

        def run():
            self._stop_requested = False
            try:
                self._sync_dashboard_from_api()
                hope = self._get_game_params()["win_amount"]
                if self._wallet_over_hope_amount(hope):
                    bal_disp = format_wallet_balance_display(self._dashboard_data.get("balance", "—"))
                    self.root.after(
                        0,
                        lambda bd=bal_disp, hp=hope: messagebox.showinfo(
                            self._t("hint_title"),
                            self._t("msg_balance_over_hope_skip_game", bal=bd, hope=hp),
                        ),
                    )
                    return
                self._do_play_game_until_hope_met(hope, from_worker=False)
            except NoSuchWindowException:
                self._driver = None
                self._wait = None
                print("瀏覽器已關閉，請再按一次「啟動」重新開始")
            except Exception as e:
                print(f"主 錯誤: {type(e).__name__}: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _try_dismiss_site_ad_popup(self, driver: webdriver.Chrome) -> None:
        """登入站台後、進入遊戲圖示前：若有全頁廣告，點 div.bgclosePopup 關閉（可連關多層）。"""
        t0 = time.time()
        deadline = t0 + 12.0
        time.sleep(0.35)
        clicked_once = False
        last_action = t0
        while time.time() < deadline and not self._stop_requested:
            clicked = False
            try:
                driver.switch_to.default_content()
                for el in driver.find_elements(By.CSS_SELECTOR, "div.bgclosePopup"):
                    try:
                        if not el.is_displayed():
                            continue
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        time.sleep(0.1)
                        try:
                            el.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                        print("[站台] 已關閉廣告彈層（.bgclosePopup）")
                        clicked = True
                        clicked_once = True
                        last_action = time.time()
                        time.sleep(0.45)
                        break
                    except Exception:
                        continue
            except Exception:
                pass
            if clicked:
                continue
            if not clicked_once and time.time() - t0 > 5.0:
                break
            if clicked_once and time.time() - last_action > 2.0:
                break
            time.sleep(0.25)

    def _sleep_if_not_stopped(self, sec: float, step: float = 0.25) -> bool:
        """睡眠；若收到停止則回傳 False。"""
        end = time.time() + sec
        while time.time() < end:
            if self._stop_requested:
                return False
            time.sleep(min(step, end - time.time()))
        return True

    def _post_spin_jackpot_canvas_taps(
        self,
        drv: webdriver.Chrome,
        canvas,
        grid: list[dict],
        confirm_records: tuple[dict[str, int | str], ...] | None,
    ) -> None:
        """每次 SPIN 且 betCount 已增加後：自網格座標隨機點 5 處（點與點間隔 1s）→ 再隔 3s → 依序點確認鍵（點與點間隔 0.5s）。"""
        if len(grid) < 5:
            return
        try:
            picks = random.sample(grid, 5)
        except ValueError:
            return
        for i, rec in enumerate(picks):
            if self._stop_requested:
                return
            try:
                ox, oy = int(rec["ox"]), int(rec["oy"])
                ActionChains(drv).move_to_element_with_offset(canvas, ox, oy).click().perform()
                print(
                    f"[遊戲] 大獎/遮罩掃點 ({i + 1}/5) ox={ox} oy={oy} note={rec.get('note', '')}"
                )
            except Exception as e:
                print(f"[遊戲] 掃點失敗: {e}")
            if i < 4:
                if not self._sleep_if_not_stopped(1.0):
                    return
        if self._stop_requested:
            return
        if not confirm_records:
            return
        if not self._sleep_if_not_stopped(3.0):
            return
        if self._stop_requested:
            return
        n = len(confirm_records)
        for i, rec in enumerate(confirm_records):
            if self._stop_requested:
                return
            try:
                ox, oy = int(rec["ox"]), int(rec["oy"])
                ActionChains(drv).move_to_element_with_offset(canvas, ox, oy).click().perform()
                print(
                    f"[遊戲] 大獎/遮罩確認鍵 ({i + 1}/{n}) ox={ox} oy={oy} note={rec.get('note', '')}"
                )
            except Exception as e:
                print(f"[遊戲] 確認鍵失敗: {e}")
            if i < n - 1:
                if not self._sleep_if_not_stopped(0.5):
                    return

    def _execute_play_game(self) -> None:
        """實際執行玩遊戲：開局前記錄 betCount，每局 SPIN 後輪詢 API，直到餘額達希望金額。"""
        acc = self.userinfo.get("username", "")
        pwd = self.userinfo.get("password", "")
        if not acc or not pwd:
            return
        driver, wait, is_new = self._get_or_create_driver()
        try:
            try:
                self.root.after(0, self._start_in_game_ai_marquee)
            except tk.TclError:
                pass
            if is_new:
                guest_url = self._guest_site_url()
                print(f"正在前往: {guest_url}")
                driver.get(guest_url)
                if not self._login_site(driver, wait, acc, pwd):
                    self._schedule_login_failure_ui()
                    try:
                        self.root.after(0, self._clear_ai_marquee_idle)
                    except tk.TclError:
                        pass
                    return
            else:
                driver.switch_to.default_content()
                if len(driver.window_handles) > 1:
                    driver.switch_to.window(driver.window_handles[0])
                time.sleep(1)

            self._try_dismiss_site_ad_popup(driver)

            if self._get_game_params()["claim_rewards"]:
                self._do_claim_rewards()

            game_icon = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//img[contains(@src, 'game122.png')]")
                )
            )
            game_icon.click()
            time.sleep(5)

            # 如遊戲開在新分頁 / 新視窗，切換到新視窗
            current_window = driver.current_window_handle
            WebDriverWait(driver, 10).until(lambda d: len(d.window_handles) > 1)
            for handle in driver.window_handles:
                if handle != current_window:
                    driver.switch_to.window(handle)
                    break

            fullscreen_p = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//p[contains(text(), '點擊螢幕開啟全螢幕')]")
                )
            )
            fullscreen_p.click()
            time.sleep(4)

            alert = driver.switch_to.alert
            alert.dismiss()
            # print("已成功點擊彈窗的『取消』")

            time.sleep(12)
            wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "gameIframe")))
            time.sleep(2)

            game_view_btn = wait.until(EC.element_to_be_clickable((By.ID, "game_view")))
            time.sleep(2)
            game_view_btn.click()
            # time.sleep(2)
            # game_view_btn.click()
            # print("成功點擊 game_view！")
            time.sleep(6)

            original_w = 537
            original_h = 954
            target_x = 268.5
            target_y = 842.5
            wait_min, wait_max = 5.0, 8.0

            def fetch_bet_count() -> int | None:
                return API.parse_bet_count(API.get_user_info(acc))

            baseline: int | None = None
            for _ in range(BETCOUNT_BASELINE_FETCH_RETRIES):
                if self._stop_requested:
                    print("已收到停止，中止玩遊戲")
                    return
                baseline = fetch_bet_count()
                if baseline is not None:
                    break
                time.sleep(BETCOUNT_BASELINE_FETCH_SLEEP_SEC)
            if baseline is None:
                print("[遊戲] 無法取得 API betCount，中止精準局數（請檢查網路或帳號）")
                try:
                    self.root.after(0, self._clear_ai_marquee_idle)
                except tk.TclError:
                    pass
                return

            hope_amt = self._get_game_params()["win_amount"]
            print(
                f"[遊戲] 起始 betCount={baseline}，本輪目標：餘額達希望金額 {hope_amt:,}（持續 SPIN 至達成）"
            )

            jackpot_grid = CANVAS_JACKPOT_GRID_RECORDS
            jackpot_confirm_recs = CANVAS_JACKPOT_CONFIRM_RECORDS

            def start_auto_spin_until_hope(
                drv: webdriver.Chrome,
                wait_min: float,
                wait_max: float,
            ) -> None:
                try:
                    drv.switch_to.default_content()
                    drv.switch_to.frame("gameIframe")
                    canvas = drv.find_element(By.TAG_NAME, "canvas")
                    ox = int(target_x - (original_w / 2))
                    oy = int(target_y - (original_h / 2))

                    last_bc = baseline
                    click_attempt = 0
                    max_click_attempts = 10**9

                    while True:
                        if self._stop_requested:
                            print("已收到停止，中斷玩遊戲")
                            return
                        self._sync_dashboard_from_api()
                        if self._wallet_over_hope_amount():
                            print(
                                f"[遊戲] 錢包餘額已達希望金額 {self._get_game_params()['win_amount']:,}，結束本輪遊戲"
                            )
                            self._show_ai_strategy_marquee_completed()
                            return
                        if click_attempt >= max_click_attempts:
                            print(
                                f"[遊戲] 已達本輪點擊安全上限（{max_click_attempts}），"
                                f"目前 betCount={last_bc}（起點 {baseline}，+{last_bc - baseline}）"
                            )
                            return

                        actions = ActionChains(drv)
                        actions.move_to_element_with_offset(canvas, ox, oy).click().perform()
                        click_attempt += 1
                        print(
                            f"[{time.strftime('%H:%M:%S')}] 第 {click_attempt} 次 SPIN 已點擊，輪詢 betCount 是否 +1…"
                        )

                        deadline = time.time() + BETCOUNT_SPIN_ACK_TIMEOUT_SEC
                        acknowledged = False
                        while time.time() < deadline:
                            if self._stop_requested:
                                return
                            cur = fetch_bet_count()
                            if cur is not None and cur > last_bc:
                                last_bc = cur
                                print(
                                    f"[API] betCount={last_bc}（較起點 +{last_bc - baseline} / 希望金額 {hope_amt:,}）"
                                )
                                acknowledged = True
                                break
                            time.sleep(BETCOUNT_SPIN_ACK_INTERVAL_SEC)

                        if not acknowledged:
                            print(
                                f"[遊戲] {BETCOUNT_SPIN_ACK_TIMEOUT_SEC:.0f}s 內未觀測到 betCount 增加，再 SPIN 一次"
                            )
                            continue

                        if not self._sleep_if_not_stopped(SPIN_ACK_TO_JACKPOT_SWEEP_DELAY_SEC):
                            return

                        self._post_spin_jackpot_canvas_taps(
                            drv, canvas, jackpot_grid, jackpot_confirm_recs
                        )

                        self._sync_dashboard_from_api()
                        if self._wallet_over_hope_amount():
                            print(
                                f"[遊戲] 錢包餘額已達希望金額 {self._get_game_params()['win_amount']:,}，結束本輪遊戲"
                            )
                            self._show_ai_strategy_marquee_completed()
                            return

                        wait_time = random.uniform(wait_min, wait_max)
                        for _ in range(int(wait_time * 10)):
                            if self._stop_requested:
                                return
                            time.sleep(0.1)

                except NoSuchWindowException:
                    self._driver = None
                    self._wait = None
                    print("瀏覽器已關閉，請再按一次「啟動」重新開始")
                    return
                except Exception as e:
                    print(f"loop error: {e}")

            start_auto_spin_until_hope(driver, wait_min, wait_max)
        finally:
            try:
                self.root.after(0, self._stop_in_game_ai_marquee)
            except tk.TclError:
                pass

    def save_config(self, username: str, password: str) -> None:
        data = self.load_config()
        data["username"] = username
        data["password"] = password
        data["ui_language"] = self._ui_lang
        data["platform"] = getattr(self, "_platform_key", DEFAULT_PLATFORM_KEY)
        ha = self._normalize_hope_amount(data.get("hope_amount"))
        data["hope_amount"] = ha if ha is not None else WIN_DEFAULT
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def load_config(self) -> dict:
        base: dict = {
            "username": "",
            "password": "",
            "ui_language": UI_LANG_DEFAULT,
            "hope_amount": WIN_DEFAULT,
            "platform": DEFAULT_PLATFORM_KEY,
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for k in ("username", "password", "ui_language", "hope_amount", "platform"):
                        if k in raw and raw[k] is not None:
                            base[k] = raw[k]
            except Exception:
                pass
        base["ui_language"] = self._normalize_ui_language(base.get("ui_language", UI_LANG_DEFAULT))
        base["platform"] = normalize_platform_key(base.get("platform", DEFAULT_PLATFORM_KEY))
        ha = self._normalize_hope_amount(base.get("hope_amount"))
        base["hope_amount"] = ha if ha is not None else WIN_DEFAULT
        return base

    def clear_frame(self) -> None:
        if self._main_scroll_canvas is not None:
            try:
                self.root.unbind_all("<MouseWheel>")
            except Exception:
                pass
            try:
                self.root.unbind_all("<Button-4>")
                self.root.unbind_all("<Button-5>")
            except Exception:
                pass
            self._main_scroll_canvas = None
        self._cancel_login_media()
        self._stop_in_game_ai_marquee()
        self._cancel_ai_fake_timers()
        self._rest_countdown_hide()
        for widget in self.main_container.winfo_children():
            widget.destroy()
if __name__ == "__main__":
    root = tk.Tk()
    _ico = resolve_data_asset("openclaw.ico")
    if _ico is not None:
        try:
            root.iconbitmap(str(_ico))
        except Exception:
            pass
    root.attributes("-topmost", True)  # 預設置頂，不被瀏覽器蓋住
    app = LoginApp(root)
    root.mainloop()


    # 依語言導向對應平台頁面
    # print(API.save_downloadaccount("autogame@test.com","VN"))
    # print(API.get_group_link("VN"))
    # API.lottery_bet('autogame@test.com', 45)
    # print(API.get_user_info('autogame0325@test.com'))
    # {"code":200,"msg":"success","data":{"username":"autogame@test.com","level":"2","betCount":"152","balance":3368400,"
    # 打包 exe（輸出檔名固定 TreasureClaw.exe）：專案根目錄執行
    # python -m PyInstaller --noconsole --onefile --clean --name TreasureClaw --add-data "data;data" --icon="data/openclaw.ico" --collect-all selenium test.py
    # → dist\TreasureClaw.exe ；主程式勿加 --uac-admin（Chrome/Selenium 在提權環境易空白分頁）
    # onedir：將 --onefile 改成 --onedir，輸出 dist\TreasureClaw\TreasureClaw.exe
