import os
import re
import sys
import json
import sqlite3
import datetime
import calendar
import tempfile
import threading
import subprocess
from pathlib import Path

# ==========================================
# 辨識環境：如果是 PyInstaller 打包的 exe，使用 sys.executable 所在真實目錄
# 如果是一般 .py 腳本執行，使用 __file__ 所在目錄
# 避免抓到 Windows AppData\Local\Temp 臨時解壓縮目錄
# ==========================================
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

try:
    os.chdir(APP_DIR)
except Exception:
    pass

# 設定 matplotlib 設定檔暫存路徑，避免權限警告
os.environ["MPLCONFIGDIR"] = os.path.join(tempfile.gettempdir(), "matplotlib_ctdose")

import pandas as pd
from pydicom import dcmread

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# 設定 Matplotlib 中文字型與負號顯示
plt.rcParams["font.sans-serif"] = [
    "Arial Unicode MS", "Heiti TC", "PingFang HK", "Microsoft JhengHei",
    "Microsoft YaHei", "SimHei", "DejaVu Sans", "sans-serif"
]
plt.rcParams["axes.unicode_minus"] = False

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText


# ==========================================
# 常數與預設配置（使用絕對路徑 APP_DIR）
# ==========================================
DEFAULT_PATH = r"\\Aria15sql\DICOM\CT_DOSE"
CONFIG_FILE = str(APP_DIR / "metadata.json")
DEFAULT_DB_PATH = str(APP_DIR / "CTdose_eval.db")
OUTPUT_DIR = str(APP_DIR / "output")


def resolve_db_path(custom_path=None):
    """
    智能解析資料庫路徑：
    優先檢查自訂路徑 -> exe/腳本所在同目錄的 CTdose_eval.db -> 當前執行目錄的 CTdose_eval.db
    """
    if custom_path and os.path.exists(custom_path):
        return str(Path(custom_path).resolve())

    candidates = [
        APP_DIR / "CTdose_eval.db",
        Path.cwd() / "CTdose_eval.db",
        Path("CTdose_eval.db").resolve(),
    ]
    for p in candidates:
        if p.exists():
            return str(p.resolve())
    return DEFAULT_DB_PATH


def get_default_metadata():
    """回傳標準預設 metadata 設定"""
    return {
        "說明1": 'path為路徑名稱，有預設值，若為"\\"符號請重複兩次。',
        "說明2": "last_reading_date為最後讀取日期，當執行時候必為當日，不用更改。",
        "說明3": "omit_year為忽略年分，請在方括號裡面填入不要輸出的年份並用,號分開",
        "path": DEFAULT_PATH,
        "last_reading_date": str(datetime.date.today()),
        "omit_year": []
    }


def load_metadata():
    """安全讀取 metadata.json，若檔案不存在或格式損毀則建立並回傳預設設定"""
    if not os.path.exists(CONFIG_FILE):
        meta = get_default_metadata()
        save_metadata(meta)
        return meta

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        defaults = get_default_metadata()
        for k, v in defaults.items():
            if k not in data:
                data[k] = v

        if isinstance(data.get("omit_year"), str):
            raw = data["omit_year"].strip()
            nums = re.findall(r"\d+", raw)
            data["omit_year"] = [int(n) for n in nums]
        elif isinstance(data.get("omit_year"), list):
            data["omit_year"] = [int(x) for x in data["omit_year"] if str(x).isdigit()]
        else:
            data["omit_year"] = []

        return data
    except Exception as e:
        print(f"讀取 metadata.json 失敗: {e}，將重新生成預設設定。")
        meta = get_default_metadata()
        save_metadata(meta)
        return meta


def save_metadata(metadata):
    """安全寫入 metadata.json"""
    try:
        if isinstance(metadata.get("omit_year"), str):
            nums = re.findall(r"\d+", metadata["omit_year"])
            metadata["omit_year"] = [int(n) for n in nums]

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"儲存 metadata.json 發生錯誤: {e}")
        return False


def get_db_distinct_years(db_path=None):
    """從資料庫讀取所有已記錄的不重複年份"""
    real_path = resolve_db_path(db_path)
    if not os.path.exists(real_path):
        return []
    try:
        conn = sqlite3.connect(real_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT substr(study_date, 1, 4) as yr 
            FROM CT_dose 
            WHERE study_date IS NOT NULL AND length(study_date) >= 4 
            ORDER BY yr
        """)
        rows = cur.fetchall()
        conn.close()
        years = []
        for r in rows:
            if r[0] and r[0].isdigit():
                years.append(int(r[0]))
        return sorted(list(set(years)))
    except Exception as e:
        print(f"讀取資料庫年份失敗: {e}")
        return []


def get_db_distinct_sites(db_path=None):
    """從資料庫讀取所有已記錄的不重複部位"""
    real_path = resolve_db_path(db_path)
    if not os.path.exists(real_path):
        return []
    try:
        conn = sqlite3.connect(real_path)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT sites FROM CT_dose WHERE sites IS NOT NULL ORDER BY sites")
        rows = cur.fetchall()
        conn.close()
        sites = [r[0] if r[0] and str(r[0]).strip() else "未指定部位" for r in rows]
        return sorted(list(set(sites)))
    except Exception as e:
        print(f"讀取資料庫檢查部位失敗: {e}")
        return []


def get_db_date_range(db_path=None):
    """取得資料庫中 study_date 的最小值與最大值 (datetime.date)"""
    real_path = resolve_db_path(db_path)
    today = datetime.date.today()
    if not os.path.exists(real_path):
        return today, today
    try:
        conn = sqlite3.connect(real_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT min(study_date), max(study_date) 
            FROM CT_dose 
            WHERE study_date IS NOT NULL AND length(study_date) >= 8
        """)
        row = cur.fetchone()
        conn.close()
        if row and row[0] and row[1]:
            d1_raw = str(row[0]).strip().replace("/", "-")
            d2_raw = str(row[1]).strip().replace("/", "-")
            min_d = pd.to_datetime(d1_raw, errors="coerce")
            max_d = pd.to_datetime(d2_raw, errors="coerce")
            if pd.notnull(min_d) and pd.notnull(max_d):
                return min_d.date(), max_d.date()
    except Exception as e:
        print(f"取得資料庫日期範圍失敗: {e}")
    return today, today


# ==========================================
# 檔案掃描與 DICOM 解析核心
# ==========================================
def find_dose_dicom_files(root_dir, max_depth=3, log_func=None):
    """
    遍歷 root_dir 及其子目錄（預設最多 3 層深度），
    篩選主檔名以 'SC.' 開頭且以 '.DCM' 結尾（不區分大小寫）的檔案。
    """
    if log_func:
        log_func(f"開始搜尋 DICOM 檔案... 母目錄: {root_dir} (最多搜尋 {max_depth} 層子目錄)")

    matched_files = []
    root_p = Path(root_dir)

    for dirpath, dirnames, filenames in os.walk(root_dir):
        try:
            rel = Path(dirpath).relative_to(root_p)
            depth = 0 if str(rel) == "." else len(rel.parts)
        except Exception:
            depth = 0

        if depth >= max_depth:
            dirnames.clear()

        for fname in filenames:
            fname_upper = fname.upper()
            if fname_upper.startswith("SC.") and fname_upper.endswith(".DCM"):
                matched_files.append(Path(dirpath) / fname)

    if log_func:
        log_func(f"搜尋完成！共找到 {len(matched_files)} 個符合 'SC.*.DCM' 的劑量報告檔案。")

    return matched_files


def parse_dicom_files(file_list, log_func=None, progress_func=None):
    """解析篩選出的 DICOM 劑量螢幕截圖檔案並整理為 DataFrame"""
    name_list = []
    id_list = []
    site_list = []
    study_date_list = []
    study_id_list = []
    ctdivol_list = []
    dlp_number_list = []
    comment_list = []
    event_rec_list = []
    acq_type_list = []

    total = len(file_list)
    valid_count = 0

    for idx, fpath in enumerate(file_list):
        if progress_func:
            progress_func(idx + 1, total)

        try:
            obj = dcmread(fpath)
        except Exception as e:
            if log_func:
                log_func(f"[讀取失敗] 檔案損毀或非 DICOM: {fpath.name} ({e})")
            continue

        pt_name = str(getattr(obj, "PatientName", "")).split("^")[0]
        pt_id = getattr(obj, "PatientID", "Unknown")

        image_type = getattr(obj, "ImageType", [])
        if not image_type or image_type[-1] != "SCREEN SAVE":
            if log_func:
                log_func(f"[略過] {pt_id} ({pt_name}) 影像非 SCREEN SAVE: {fpath.name}")
            continue

        site = getattr(obj, "StudyDescription", "未指定部位")
        if not site or not str(site).strip():
            site = "未指定部位"

        study_date_raw = str(getattr(obj, "StudyDate", ""))
        if len(study_date_raw) == 8:
            study_date = f"{study_date_raw[0:4]}-{study_date_raw[4:6]}-{study_date_raw[6:8]}"
        else:
            study_date = study_date_raw

        study_id_raw = getattr(obj, "StudyID", None)
        try:
            study_id = int(study_id_raw)
        except (ValueError, TypeError):
            study_id = hash(f"{pt_id}_{study_date}_{site}") % 100000000

        # 解析 ExposureDoseSequence
        ctdivol = []
        event_no = []
        acq_type = []
        exposure_sq = getattr(obj, "ExposureDoseSequence", [])
        for event, sq in enumerate(exposure_sq):
            try:
                ctdivol.append(sq[0x00189345].value)
                event_no.append(event + 1)
                acq_t = getattr(sq, "AcquisitionType", "")
                if acq_t == "STATIONARY":
                    acq_type.append("4DCT")
                else:
                    acq_type.append(acq_t)
            except Exception:
                pass

        if len(ctdivol) > 0:
            ctdivol_list.append(ctdivol[-1])
            event_rec_list.append(event_no[-1])
            acq_type_list.append(acq_type[-1])
        else:
            ctdivol_list.append(None)
            event_rec_list.append(None)
            acq_type_list.append(None)

        # 正則表達式抓取 DLP (注意前導空白，避免抓取到 Total DLP)
        comment_text = getattr(obj, "CommentsOnRadiationDose", "")
        try:
            dlp_values = re.findall(r" DLP=([\d.]+)", comment_text)
            if dlp_values:
                dlp_number = float(dlp_values[-1])
            else:
                dlp_number = None
                if log_func:
                    log_func(f"[提示] 病患 {pt_id} {pt_name} 無 DLP 數值")
        except Exception:
            dlp_number = None

        study_id_list.append(study_id)
        study_date_list.append(study_date)
        id_list.append(pt_id)
        name_list.append(pt_name)
        dlp_number_list.append(dlp_number)
        site_list.append(site)
        comment_list.append(comment_text if comment_text else None)

        valid_count += 1
        if log_func and valid_count % 10 == 0:
            log_func(f"已解析 {valid_count} 筆有效紀錄 (目前處理到: {pt_id} {pt_name})")

    df = pd.DataFrame({
        "study_ID": study_id_list,
        "study_date": study_date_list,
        "sites": site_list,
        "ID": id_list,
        "Name": name_list,
        "acq_type": acq_type_list,
        "CTDIVol": ctdivol_list,
        "DLP": dlp_number_list,
        "Rec_event": event_rec_list,
        "Comment": comment_list
    })

    if log_func:
        log_func(f"DICOM 處理完畢！成功解析有效筆數: {len(df)}")

    return df


def backup_sql(df, db_path=None, log_func=None):
    """將解析結果寫入 SQLite 資料庫（具備 UNIQUE INDEX 去重），並回傳完整資料庫 DataFrame"""
    target_db = resolve_db_path(db_path)
    if log_func:
        log_func(f"正在更新 SQLite 資料庫: {target_db} ...")

    conn = sqlite3.connect(target_db)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS CT_dose (
            study_ID INTEGER,
            study_date TEXT,
            sites TEXT,
            ID TEXT,
            Name TEXT,
            acq_type TEXT,
            CTDIVol REAL,
            DLP REAL,
            Rec_event INTEGER,
            Comment TEXT
        )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_key_index ON CT_dose (study_ID)")
    conn.execute("PRAGMA synchronous = OFF;")

    inserted_count = 0
    for _, item in df.iterrows():
        cur.execute("""
            INSERT OR IGNORE INTO CT_dose (
                study_ID, study_date, sites, ID, Name, acq_type, CTDIVol, DLP, Rec_event, Comment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["study_ID"], item["study_date"], item["sites"],
            item["ID"], item["Name"], item["acq_type"],
            item["CTDIVol"], item["DLP"], item["Rec_event"], item["Comment"]
        ))
        if cur.rowcount > 0:
            inserted_count += 1

    conn.commit()

    ct_database = pd.read_sql_query("SELECT * FROM CT_dose", conn, parse_dates=["study_date"])
    conn.close()

    if log_func:
        log_func(f"資料庫更新完成！本次新增 {inserted_count} 筆，資料庫總累積 {len(ct_database)} 筆紀錄。")

    return ct_database


def export_csv(ct_database, metadata, output_dir=OUTPUT_DIR, log_func=None):
    """根據年份與部位匯出 CSV，並更新 metadata.json 中的最後執行日期"""
    if ct_database.empty:
        if log_func:
            log_func("資料庫無任何資料，略過 CSV 匯出。")
        return

    if log_func:
        log_func(f"開始匯出 CSV 報表至 {output_dir}/ 目錄...")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    dates = pd.to_datetime(ct_database["study_date"], errors="coerce")
    valid_dates = dates.dropna()
    year_label = sorted(list(set(valid_dates.dt.year.astype(int))))

    omit_years = [int(y) for y in metadata.get("omit_year", []) if int(y) in year_label]
    if omit_years and log_func:
        log_func(f"本次依設定忽略匯出的年份: {omit_years}")

    target_years = sorted(list(set(year_label) - set(omit_years)))

    exported_files = 0
    for yr in target_years:
        sub_df = ct_database[dates.dt.year == yr]
        sub_df = sub_df.sort_values(by="study_ID")
        sites_label = list(sub_df["sites"].dropna().unique())

        for site in sites_label:
            clean_site = re.sub(r'[\\/*?:"<>|]', "_", str(site)).strip()
            if not clean_site:
                clean_site = "未指定部位"
            site_df = sub_df[sub_df["sites"] == site]
            target_csv = out_path / f"{yr}_{clean_site}.csv"
            site_df.to_csv(target_csv, index=False, encoding="utf-8-sig")
            exported_files += 1

    today_str = str(datetime.date.today())
    metadata["last_reading_date"] = today_str
    save_metadata(metadata)

    if log_func:
        log_func(f"CSV 匯出完成！共產生 {exported_files} 個報表檔案。")
        log_func(f"已更新 metadata.json 的最後執行日期為: {today_str}")


# ==========================================
# 原生月曆彈出對話框 (純標準庫 Calendar)
# ==========================================
class CalendarDialog(tk.Toplevel):
    def __init__(self, parent, target_var, title="選擇日期"):
        super().__init__(parent)
        self.title(title)
        self.target_var = target_var
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        cur_val = target_var.get().strip()
        try:
            init_dt = datetime.datetime.strptime(cur_val, "%Y-%m-%d").date()
        except Exception:
            init_dt = datetime.date.today()

        self.year = init_dt.year
        self.month = init_dt.month
        self.selected_day = init_dt.day

        self._build_ui()
        self.geometry(f"+{parent.winfo_rootx() + 100}+{parent.winfo_rooty() + 100}")

    def _build_ui(self):
        hdr = ttk.Frame(self, padding="8 8 8 4")
        hdr.pack(fill=tk.X)
        ttk.Button(hdr, text="◀ 上月", width=6, command=self._prev_month).pack(side=tk.LEFT)
        self.hdr_lbl = ttk.Label(hdr, text="", font=("", 10, "bold"))
        self.hdr_lbl.pack(side=tk.LEFT, expand=True)
        ttk.Button(hdr, text="下月 ▶", width=6, command=self._next_month).pack(side=tk.RIGHT)

        # 單一網格 Frame，星期列與日期列都在此 frame 內，保證 100% 精準對齊
        self.cal_frame = ttk.Frame(self, padding="8 4 8 8")
        self.cal_frame.pack(fill=tk.BOTH, expand=True)

        for col in range(7):
            self.cal_frame.grid_columnconfigure(col, weight=1, uniform="cal_col", minsize=38)

        self._refresh_calendar()

    def _refresh_calendar(self):
        for w in self.cal_frame.winfo_children():
            w.destroy()

        self.hdr_lbl.config(text=f"{self.year} 年 {self.month:02d} 月")

        # 星期標題 (Row 0)
        weekdays = ["一", "二", "三", "四", "五", "六", "日"]
        for c, w in enumerate(weekdays):
            fg = "#c62828" if c >= 5 else "#333333"
            lbl = tk.Label(
                self.cal_frame,
                text=w,
                fg=fg,
                font=("", 9, "bold"),
                pady=2
            )
            lbl.grid(row=0, column=c, sticky="nsew", padx=2, pady=(0, 4))

        # 日期網格 (Row 1 ~ 6)
        cal = calendar.monthcalendar(self.year, self.month)
        for r, week in enumerate(cal):
            for c, day in enumerate(week):
                if day != 0:
                    btn = tk.Button(
                        self.cal_frame,
                        text=str(day),
                        relief="groove",
                        font=("", 9),
                        command=lambda d=day: self._pick_date(d)
                    )
                    if day == self.selected_day:
                        btn.config(bg="#1976d2", fg="white", font=("", 9, "bold"))
                    btn.grid(row=r + 1, column=c, sticky="nsew", padx=2, pady=2)
                else:
                    lbl_empty = tk.Label(self.cal_frame, text="")
                    lbl_empty.grid(row=r + 1, column=c, sticky="nsew")

    def _prev_month(self):
        if self.month == 1:
            self.year -= 1
            self.month = 12
        else:
            self.month -= 1
        self._refresh_calendar()

    def _next_month(self):
        if self.month == 12:
            self.year += 1
            self.month = 1
        else:
            self.month += 1
        self._refresh_calendar()

    def _pick_date(self, day):
        chosen = f"{self.year:04d}-{self.month:02d}-{day:02d}"
        self.target_var.set(chosen)
        self.destroy()


# ==========================================
# GUI 視窗應用程式 (Tkinter)
# ==========================================
class CTDoseApp:
    def __init__(self, root):
        self.root = root
        self.root.title("CT Dose 輻射劑量自動化評估與統計系統")
        self.root.geometry("1060x800")
        self.root.minsize(940, 680)

        # 讀取組態設定
        self.metadata = load_metadata()
        self.is_processing = False

        # 資料庫路徑（使用智能解析）
        self.db_path_var = tk.StringVar(value=resolve_db_path())

        # 年份勾選變數字典 {int(year): tk.BooleanVar}
        self.year_vars = {}
        # 部位勾選變數字典 {str(site): tk.BooleanVar}
        self.site_vars = {}

        # 暫存圖表分析資料
        self.current_report_df = pd.DataFrame()

        # 時間區間選取變數
        self.enable_range_var = tk.BooleanVar(value=True)  # 是否啟用時間區間篩選
        self.time_period_var = tk.StringVar(value="月")     # 年 / 季 / 月 / 日

        # 年區間
        self.start_year_var = tk.StringVar()
        self.end_year_var = tk.StringVar()

        # 季區間
        self.start_q_year_var = tk.StringVar()
        self.start_q_val_var = tk.StringVar(value="Q1")
        self.end_q_year_var = tk.StringVar()
        self.end_q_val_var = tk.StringVar(value="Q4")

        # 月區間
        self.start_m_year_var = tk.StringVar()
        self.start_m_val_var = tk.StringVar(value="01")
        self.end_m_year_var = tk.StringVar()
        self.end_m_val_var = tk.StringVar(value="12")

        # 日區間
        self.start_d_var = tk.StringVar()
        self.end_d_var = tk.StringVar()

        self._build_ui()
        self._refresh_db_status()
        self._refresh_years_checklist()
        self._refresh_analytics_sites()
        self._reset_time_ranges_to_db()

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # 建立主要分頁 (Notebook)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 頁籤 1: 資料讀取與同步
        self.tab_sync = ttk.Frame(self.notebook, padding="10 10 10 10")
        self.notebook.add(self.tab_sync, text=" 📂 資料讀取與同步 ")

        # 頁籤 2: 劑量統計與圖表分析
        self.tab_report = ttk.Frame(self.notebook, padding="10 10 10 10")
        self.notebook.add(self.tab_report, text=" 📊 劑量統計與圖表分析 ")

        self._build_tab_sync()
        self._build_tab_report()

    # ----------------------------------------------------
    # 頁籤 1: 資料讀取與同步介面
    # ----------------------------------------------------
    def _build_tab_sync(self):
        parent = self.tab_sync

        # --- 區塊 1: 母目錄設定 ---
        dir_frame = ttk.LabelFrame(parent, text=" 工作母目錄設定 ", padding="10 8 10 8")
        dir_frame.pack(fill=tk.X, pady=(0, 8))

        dir_inner = ttk.Frame(dir_frame)
        dir_inner.pack(fill=tk.X)

        ttk.Label(dir_inner, text="母目錄路徑:").pack(side=tk.LEFT, padx=(0, 5))

        self.path_var = tk.StringVar(value=self.metadata.get("path", DEFAULT_PATH))
        self.path_entry = ttk.Entry(dir_inner, textvariable=self.path_var)
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        browse_btn = ttk.Button(dir_inner, text="瀏覽...", command=self._browse_folder)
        browse_btn.pack(side=tk.LEFT, padx=(0, 5))

        reset_btn = ttk.Button(dir_inner, text="預設路徑", command=self._reset_to_default_path)
        reset_btn.pack(side=tk.LEFT)

        self.path_status_label = ttk.Label(dir_frame, text="", foreground="gray")
        self.path_status_label.pack(anchor=tk.W, pady=(4, 0))
        self._check_path_status()

        # --- 區塊 2: 搜尋與過濾設定 (含動態讀取 DB 年份勾選) ---
        config_frame = ttk.LabelFrame(parent, text=" 掃描與過濾設定 ", padding="10 8 10 8")
        config_frame.pack(fill=tk.X, pady=(0, 8))

        top_cfg = ttk.Frame(config_frame)
        top_cfg.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(top_cfg, text="遍歷子目錄深度:").pack(side=tk.LEFT, padx=(0, 5))
        self.depth_var = tk.IntVar(value=3)
        depth_spin = ttk.Spinbox(top_cfg, from_=1, to=10, textvariable=self.depth_var, width=5)
        depth_spin.pack(side=tk.LEFT, padx=(0, 20))

        rule_lbl = ttk.Label(top_cfg, text="檔名篩選: 主檔名以 'SC.' 開頭且副檔名為 '.DCM' (自動鎖定劑量報告)", foreground="#0b6623")
        rule_lbl.pack(side=tk.LEFT)

        # 年份勾選區塊 (從 DB 讀取)
        year_outer = ttk.Frame(config_frame)
        year_outer.pack(fill=tk.X, pady=(4, 0))

        ttk.Label(year_outer, text="忽略匯出年份 (勾選者不輸出):", font=("", 9, "bold")).pack(side=tk.LEFT, anchor=tk.N, padx=(0, 8), pady=2)

        self.years_scroll_frame = ttk.Frame(year_outer)
        self.years_scroll_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        year_btn_box = ttk.Frame(year_outer)
        year_btn_box.pack(side=tk.RIGHT, padx=(8, 0))

        ttk.Button(year_btn_box, text="全勾(全忽略)", width=10, command=self._select_all_years).pack(side=tk.TOP, pady=1)
        ttk.Button(year_btn_box, text="全取消(全輸出)", width=10, command=self._deselect_all_years).pack(side=tk.TOP, pady=1)
        ttk.Button(year_btn_box, text="🔄 刷新年份", width=10, command=self._refresh_years_checklist).pack(side=tk.TOP, pady=1)

        # --- 區塊 3: 執行操作列 ---
        action_frame = ttk.Frame(parent)
        action_frame.pack(fill=tk.X, pady=(0, 8))

        # 需求 1: “開始掃描和評估” 按鈕文字改為紅色
        self.run_btn = tk.Button(
            action_frame,
            text="▶ 開始掃描與評估",
            font=("Arial", 11, "bold"),
            fg="#d32f2f",                # 紅色文字
            activeforeground="#b71c1c",  # 點擊時深紅文字
            relief="raised",
            bd=2,
            padx=16,
            pady=6,
            command=self._start_processing
        )
        self.run_btn.pack(side=tk.LEFT, padx=(0, 10))

        save_cfg_btn = ttk.Button(action_frame, text="💾 儲存目錄與設定", command=self._save_current_settings)
        save_cfg_btn.pack(side=tk.LEFT, padx=(0, 6))

        open_out_btn = ttk.Button(action_frame, text="📁 開啟輸出資料夾 (output)", command=self._open_output_folder)
        open_out_btn.pack(side=tk.LEFT, padx=(0, 6))

        open_db_btn = ttk.Button(action_frame, text="💾 選擇/開啟資料庫...", command=self._choose_db_file)
        open_db_btn.pack(side=tk.LEFT, padx=(0, 6))

        clear_log_btn = ttk.Button(action_frame, text="清除日誌", command=self._clear_log)
        clear_log_btn.pack(side=tk.RIGHT)

        # --- 區塊 4: 進度條與狀態列 ---
        status_frame = ttk.Frame(parent)
        status_frame.pack(fill=tk.X, pady=(0, 6))

        self.progress_bar = ttk.Progressbar(status_frame, orient=tk.HORIZONTAL, mode="determinate")
        self.progress_bar.pack(fill=tk.X, side=tk.TOP, pady=(0, 2))

        self.status_label = ttk.Label(status_frame, text="就緒", foreground="#333333")
        self.status_label.pack(side=tk.LEFT)

        self.db_info_label = ttk.Label(status_frame, text="", foreground="#666666")
        self.db_info_label.pack(side=tk.RIGHT)

        # --- 區塊 5: 即時日誌視窗 ---
        log_frame = ttk.LabelFrame(parent, text=" 執行日誌 ", padding="6 6 6 6")
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = ScrolledText(log_frame, wrap=tk.WORD, font=("Menlo", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.log(f"系統啟動。程式所在目錄: {APP_DIR}")
        self.log(f"目前鎖定資料庫路徑: {self.db_path_var.get()}")
        self.log(f"預設工作母目錄: {self.path_var.get()}")

    # ----------------------------------------------------
    # 頁籤 2: 劑量統計與圖表分析介面
    # ----------------------------------------------------
    def _build_tab_report(self):
        parent = self.tab_report

        # 頂部：資料庫檔案路徑條與切換按鈕
        db_bar = ttk.Frame(parent)
        db_bar.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(db_bar, text="目前資料庫:", font=("", 9, "bold")).pack(side=tk.LEFT, padx=(0, 5))
        self.db_path_display = ttk.Label(db_bar, textvariable=self.db_path_var, foreground="#0066cc")
        self.db_path_display.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        change_db_btn = ttk.Button(db_bar, text="📁 切換/選取 DB 檔案...", command=self._choose_db_file)
        change_db_btn.pack(side=tk.RIGHT, padx=(4, 0))

        reload_btn = ttk.Button(db_bar, text="🔄 重新讀取 DB", command=self._reload_all_db_data)
        reload_btn.pack(side=tk.RIGHT)

        # 左右佈局 PanedWindow
        paned = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # --- 左側控制面板 ---
        left_ctrl = ttk.Frame(paned, padding="8 8 8 8", width=310)
        paned.add(left_ctrl, weight=1)

        # 1. 劑量指標選擇
        metric_frame = ttk.LabelFrame(left_ctrl, text=" 劑量指標 ", padding="6 4 6 4")
        metric_frame.pack(fill=tk.X, pady=(0, 5))

        self.metric_var = tk.StringVar(value="CTDIVol")
        ttk.Radiobutton(metric_frame, text="CTDIvol (mGy)", variable=self.metric_var, value="CTDIVol").pack(side=tk.LEFT, padx=6)
        ttk.Radiobutton(metric_frame, text="DLP (mGy·cm)", variable=self.metric_var, value="DLP").pack(side=tk.LEFT, padx=6)

        # 2. 圖表類型
        chart_type_frame = ttk.LabelFrame(left_ctrl, text=" 圖表型態 ", padding="6 4 6 4")
        chart_type_frame.pack(fill=tk.X, pady=(0, 5))

        self.chart_type_var = tk.StringVar(value="scatter")
        ttk.Radiobutton(chart_type_frame, text="🔵 點狀圖 (個案標示)", variable=self.chart_type_var, value="scatter").pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(chart_type_frame, text="📈 折線圖 (時間重複平均值)", variable=self.chart_type_var, value="line").pack(side=tk.LEFT, padx=4)

        # 3. 需求 2: 時間分類維度與時間區間選擇
        time_frame = ttk.LabelFrame(left_ctrl, text=" 時間維度與區間篩選 ", padding="6 6 6 6")
        time_frame.pack(fill=tk.X, pady=(0, 6))

        # (a) 時間維度單選按鈕列
        time_box = ttk.Frame(time_frame)
        time_box.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(time_box, text="維度:").pack(side=tk.LEFT, padx=(0, 4))
        for t_label in ["年", "季", "月", "日"]:
            ttk.Radiobutton(
                time_box,
                text=t_label,
                variable=self.time_period_var,
                value=t_label,
                command=self._on_time_period_mode_change
            ).pack(side=tk.LEFT, padx=4)

        # (b) 動態時間區間容器 (切換年/季/月/日時顯示對應的選取面板)
        self.range_container = ttk.Frame(time_frame)
        self.range_container.pack(fill=tk.X, pady=(2, 4))

        self._build_time_range_panels()

        # (c) 區間控制列 (是否限制區間 + 重設全區間)
        range_ctrl_bar = ttk.Frame(time_frame)
        range_ctrl_bar.pack(fill=tk.X, pady=(2, 0))
        ttk.Checkbutton(range_ctrl_bar, text="依指定區間篩選", variable=self.enable_range_var).pack(side=tk.LEFT)
        ttk.Button(range_ctrl_bar, text="全部時間 (重設)", width=12, command=self._reset_time_ranges_to_db).pack(side=tk.RIGHT)

        # 4. 部位篩選
        site_frame = ttk.LabelFrame(left_ctrl, text=" 檢查部位篩選 ", padding="6 6 6 6")
        site_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        site_btn_bar = ttk.Frame(site_frame)
        site_btn_bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(site_btn_bar, text="全選", width=7, command=self._select_all_sites).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(site_btn_bar, text="全取消", width=7, command=self._deselect_all_sites).pack(side=tk.LEFT)
        ttk.Button(site_btn_bar, text="🔄 刷新", width=7, command=self._refresh_analytics_sites).pack(side=tk.RIGHT)

        site_canvas = tk.Canvas(site_frame, highlightthickness=0, height=120)
        site_scrollbar = ttk.Scrollbar(site_frame, orient=tk.VERTICAL, command=site_canvas.yview)
        self.site_inner_frame = ttk.Frame(site_canvas)

        self.site_inner_frame.bind(
            "<Configure>",
            lambda e: site_canvas.configure(scrollregion=site_canvas.bbox("all"))
        )
        site_canvas.create_window((0, 0), window=self.site_inner_frame, anchor="nw")
        site_canvas.configure(yscrollcommand=site_scrollbar.set)

        site_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        site_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 5. 操作按鈕
        btn_frame = ttk.Frame(left_ctrl)
        btn_frame.pack(fill=tk.X, pady=(4, 0))

        plot_btn = tk.Button(
            btn_frame,
            text="📊 產生圖表",
            font=("Arial", 10, "bold"),
            fg="#d32f2f",
            activeforeground="#b71c1c",
            relief="raised",
            bd=2,
            pady=5,
            command=self._generate_chart
        )
        plot_btn.pack(fill=tk.X, pady=(0, 4))

        export_img_btn = tk.Button(
            btn_frame,
            text="🖼️ 匯出圖表圖片...",
            font=("Arial", 9, "bold"),
            fg="#d32f2f",
            activeforeground="#b71c1c",
            relief="groove",
            pady=3,
            command=self._export_chart_image
        )
        export_img_btn.pack(fill=tk.X, pady=(0, 3))

        export_csv_btn = ttk.Button(btn_frame, text="📄 匯出統計資料 CSV...", command=self._export_chart_csv)
        export_csv_btn.pack(fill=tk.X, pady=(0, 3))

        # --- 右側圖表與摘要面板 ---
        right_panel = ttk.Frame(paned, padding="6 6 6 6")
        paned.add(right_panel, weight=4)

        # 摘要狀態條
        self.chart_info_label = ttk.Label(right_panel, text="請選擇條件後點擊「產生圖表」", font=("", 10, "bold"), foreground="#1565c0")
        self.chart_info_label.pack(anchor=tk.W, pady=(0, 4))

        # Matplotlib 畫布嵌入
        self.fig = Figure(figsize=(7, 4.8), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.fig.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.fig, master=right_panel)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.toolbar = NavigationToolbar2Tk(self.canvas, right_panel)
        self.toolbar.update()
        self.toolbar.pack(fill=tk.X)

    # ----------------------------------------------------
    # 時間區間選取面板建置與切換
    # ----------------------------------------------------
    def _build_time_range_panels(self):
        """建立四種時間維度（年、季、月、日）的動態區間面板"""
        # 1. 年區間面板
        self.panel_year = ttk.Frame(self.range_container)
        ttk.Label(self.panel_year, text="從:").pack(side=tk.LEFT, padx=(0, 2))
        self.cb_start_y = ttk.Combobox(self.panel_year, textvariable=self.start_year_var, width=6, state="readonly")
        self.cb_start_y.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(self.panel_year, text="年  到:").pack(side=tk.LEFT, padx=2)
        self.cb_end_y = ttk.Combobox(self.panel_year, textvariable=self.end_year_var, width=6, state="readonly")
        self.cb_end_y.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(self.panel_year, text="年").pack(side=tk.LEFT)

        # 2. 季區間面板
        self.panel_quarter = ttk.Frame(self.range_container)
        ttk.Label(self.panel_quarter, text="從:").pack(side=tk.LEFT, padx=(0, 2))
        self.cb_start_qy = ttk.Combobox(self.panel_quarter, textvariable=self.start_q_year_var, width=6, state="readonly")
        self.cb_start_qy.pack(side=tk.LEFT, padx=(0, 2))
        self.cb_start_qv = ttk.Combobox(self.panel_quarter, textvariable=self.start_q_val_var, values=["Q1", "Q2", "Q3", "Q4"], width=4, state="readonly")
        self.cb_start_qv.pack(side=tk.LEFT, padx=(0, 2))

        ttk.Label(self.panel_quarter, text="到:").pack(side=tk.LEFT, padx=2)
        self.cb_end_qy = ttk.Combobox(self.panel_quarter, textvariable=self.end_q_year_var, width=6, state="readonly")
        self.cb_end_qy.pack(side=tk.LEFT, padx=(0, 2))
        self.cb_end_qv = ttk.Combobox(self.panel_quarter, textvariable=self.end_q_val_var, values=["Q1", "Q2", "Q3", "Q4"], width=4, state="readonly")
        self.cb_end_qv.pack(side=tk.LEFT)

        # 3. 月區間面板
        self.panel_month = ttk.Frame(self.range_container)
        months_list = [f"{m:02d}" for m in range(1, 13)]
        ttk.Label(self.panel_month, text="從:").pack(side=tk.LEFT, padx=(0, 2))
        self.cb_start_my = ttk.Combobox(self.panel_month, textvariable=self.start_m_year_var, width=6, state="readonly")
        self.cb_start_my.pack(side=tk.LEFT, padx=(0, 2))
        self.cb_start_mv = ttk.Combobox(self.panel_month, textvariable=self.start_m_val_var, values=months_list, width=4, state="readonly")
        self.cb_start_mv.pack(side=tk.LEFT, padx=(0, 2))

        ttk.Label(self.panel_month, text="到:").pack(side=tk.LEFT, padx=2)
        self.cb_end_my = ttk.Combobox(self.panel_month, textvariable=self.end_m_year_var, width=6, state="readonly")
        self.cb_end_my.pack(side=tk.LEFT, padx=(0, 2))
        self.cb_end_mv = ttk.Combobox(self.panel_month, textvariable=self.end_m_val_var, values=months_list, width=4, state="readonly")
        self.cb_end_mv.pack(side=tk.LEFT)

        # 4. 日區間面板 (含月曆選擇器按鈕)
        self.panel_day = ttk.Frame(self.range_container)
        ttk.Label(self.panel_day, text="從:").pack(side=tk.LEFT, padx=(0, 2))
        self.entry_start_d = ttk.Entry(self.panel_day, textvariable=self.start_d_var, width=10)
        self.entry_start_d.pack(side=tk.LEFT, padx=(0, 1))
        ttk.Button(self.panel_day, text="📅", width=2, command=lambda: CalendarDialog(self.root, self.start_d_var, "選擇起始日期")).pack(side=tk.LEFT, padx=(0, 4))

        ttk.Label(self.panel_day, text="到:").pack(side=tk.LEFT, padx=1)
        self.entry_end_d = ttk.Entry(self.panel_day, textvariable=self.end_d_var, width=10)
        self.entry_end_d.pack(side=tk.LEFT, padx=(0, 1))
        ttk.Button(self.panel_day, text="📅", width=2, command=lambda: CalendarDialog(self.root, self.end_d_var, "選擇結束日期")).pack(side=tk.LEFT)

        self._on_time_period_mode_change()

    def _on_time_period_mode_change(self):
        """依據選擇的時間維度切換顯示對應的選取面板"""
        for p in [self.panel_year, self.panel_quarter, self.panel_month, self.panel_day]:
            p.pack_forget()

        mode = self.time_period_var.get()
        if mode == "年":
            self.panel_year.pack(fill=tk.X)
        elif mode == "季":
            self.panel_quarter.pack(fill=tk.X)
        elif mode == "月":
            self.panel_month.pack(fill=tk.X)
        else:  # 日
            self.panel_day.pack(fill=tk.X)

    def _reset_time_ranges_to_db(self):
        """讀取資料庫最小與最大日期，重設所有時間區間控制項的預設值"""
        current_db = self.db_path_var.get()
        min_date, max_date = get_db_date_range(current_db)

        # 建立可選年份清單
        db_years = get_db_distinct_years(current_db)
        if not db_years:
            db_years = [min_date.year, max_date.year]
        all_years = sorted(list(set(db_years + list(range(min_date.year, max_date.year + 1)))))
        year_str_list = [str(y) for y in all_years]

        # 更新年份下拉選單選項
        for cb in [self.cb_start_y, self.cb_end_y, self.cb_start_qy, self.cb_end_qy, self.cb_start_my, self.cb_end_my]:
            cb["values"] = year_str_list

        # 1. 年設定
        self.start_year_var.set(str(min_date.year))
        self.end_year_var.set(str(max_date.year))

        # 2. 季設定
        min_q = f"Q{(min_date.month - 1) // 3 + 1}"
        max_q = f"Q{(max_date.month - 1) // 3 + 1}"
        self.start_q_year_var.set(str(min_date.year))
        self.start_q_val_var.set(min_q)
        self.end_q_year_var.set(str(max_date.year))
        self.end_q_val_var.set(max_q)

        # 3. 月設定
        self.start_m_year_var.set(str(min_date.year))
        self.start_m_val_var.set(f"{min_date.month:02d}")
        self.end_m_year_var.set(str(max_date.year))
        self.end_m_val_var.set(f"{max_date.month:02d}")

        # 4. 日設定
        self.start_d_var.set(min_date.strftime("%Y-%m-%d"))
        self.end_d_var.set(max_date.strftime("%Y-%m-%d"))

    # ----------------------------------------------------
    # 資料庫選擇與切換
    # ----------------------------------------------------
    def _choose_db_file(self):
        """讓使用者瀏覽並自訂 CTdose_eval.db 的檔案位置"""
        current = self.db_path_var.get()
        init_dir = str(Path(current).parent) if os.path.exists(current) else str(APP_DIR)
        selected = filedialog.askopenfilename(
            title="請選擇 CTdose_eval.db 資料庫檔案",
            initialdir=init_dir,
            filetypes=[("SQLite 資料庫", "*.db"), ("所有檔案", "*.*")]
        )
        if selected:
            self.db_path_var.set(selected)
            self._reload_all_db_data()
            self.log(f"已切換資料庫路徑為: {selected}")

    def _reload_all_db_data(self):
        """重新整理與資料庫相關的所有介面元件"""
        self._refresh_db_status()
        self._refresh_years_checklist()
        self._refresh_analytics_sites()
        self._reset_time_ranges_to_db()
        self.log("已從資料庫重新載入年份、部位清單與時間範圍。")

    # ----------------------------------------------------
    # 年份勾選清單處理 (從 DB 動態載入)
    # ----------------------------------------------------
    def _refresh_years_checklist(self):
        """讀取 DB 中的所有年份，並生成勾選框"""
        for widget in self.years_scroll_frame.winfo_children():
            widget.destroy()

        current_db = self.db_path_var.get()
        db_years = get_db_distinct_years(current_db)
        omit_list = self.metadata.get("omit_year", [])

        all_years = sorted(list(set(db_years + [int(y) for y in omit_list if str(y).isdigit()])))
        self.year_vars.clear()

        if not all_years:
            ttk.Label(self.years_scroll_frame, text="(目前資料庫尚無年份紀錄)", foreground="gray").pack(side=tk.LEFT)
            return

        for yr in all_years:
            is_omitted = yr in omit_list
            var = tk.BooleanVar(value=is_omitted)
            self.year_vars[yr] = var

            cb = ttk.Checkbutton(
                self.years_scroll_frame,
                text=str(yr),
                variable=var,
                command=self._on_year_toggle
            )
            cb.pack(side=tk.LEFT, padx=4, pady=2)

    def _on_year_toggle(self):
        omits = [yr for yr, var in self.year_vars.items() if var.get()]
        self.metadata["omit_year"] = omits
        save_metadata(self.metadata)

    def _select_all_years(self):
        for var in self.year_vars.values():
            var.set(True)
        self._on_year_toggle()

    def _deselect_all_years(self):
        for var in self.year_vars.values():
            var.set(False)
        self._on_year_toggle()

    # ----------------------------------------------------
    # 部位篩選清單處理 (頁籤 2)
    # ----------------------------------------------------
    def _refresh_analytics_sites(self):
        """從 DB 載入所有部位名稱並更新 Checkbuttons"""
        for widget in self.site_inner_frame.winfo_children():
            widget.destroy()

        current_db = self.db_path_var.get()
        sites = get_db_distinct_sites(current_db)
        self.site_vars.clear()

        if not sites:
            ttk.Label(self.site_inner_frame, text="(資料庫尚無部位資料)", foreground="gray").pack(anchor=tk.W)
            return

        for s in sites:
            var = tk.BooleanVar(value=True)  # 預設全選
            self.site_vars[s] = var
            cb = ttk.Checkbutton(self.site_inner_frame, text=s, variable=var)
            cb.pack(anchor=tk.W, pady=1)

    def _select_all_sites(self):
        for var in self.site_vars.values():
            var.set(True)

    def _deselect_all_sites(self):
        for var in self.site_vars.values():
            var.set(False)

    # ----------------------------------------------------
    # 圖表繪製與統計分析核心 (含時間區間篩選)
    # ----------------------------------------------------
    def _generate_chart(self):
        current_db = resolve_db_path(self.db_path_var.get())
        if not os.path.exists(current_db):
            if messagebox.askyesno(
                "找不到資料庫",
                f"在以下路徑找不到資料庫檔案：\n{current_db}\n\n是否立即手動選取已存在的 CTdose_eval.db 檔案？"
            ):
                self._choose_db_file()
            return

        self.db_path_var.set(current_db)

        selected_sites = [s for s, var in self.site_vars.items() if var.get()]
        if not selected_sites:
            messagebox.showwarning("提示", "請至少勾選一個檢查部位！")
            return

        metric = self.metric_var.get()
        chart_type = self.chart_type_var.get()
        time_period = self.time_period_var.get()

        # 讀取資料
        try:
            conn = sqlite3.connect(current_db)
            query = f"""
                SELECT study_ID, study_date, sites, ID, Name, acq_type, CTDIVol, DLP 
                FROM CT_dose 
                WHERE {metric} IS NOT NULL AND study_date IS NOT NULL
            """
            df = pd.read_sql_query(query, conn)
            conn.close()
        except Exception as e:
            messagebox.showerror("錯誤", f"讀取資料庫失敗: {e}")
            return

        if df.empty:
            messagebox.showinfo("提示", f"資料庫中無 {metric} 數值的有效資料！")
            return

        # 部位篩選
        df["sites"] = df["sites"].fillna("未指定部位")
        df["sites"] = df["sites"].apply(lambda s: str(s).strip() if str(s).strip() else "未指定部位")
        df = df[df["sites"].isin(selected_sites)].copy()

        if df.empty:
            messagebox.showinfo("提示", "所選部位無符合條件的數據！")
            return

        df["study_date"] = pd.to_datetime(df["study_date"], errors="coerce")
        df = df.dropna(subset=["study_date"])
        df = df.sort_values(by="study_date")

        # ------------------------------------------------
        # 需求 2: 執行時間區間過濾
        # ------------------------------------------------
        if self.enable_range_var.get():
            try:
                if time_period == "年":
                    s_y = int(self.start_year_var.get())
                    e_y = int(self.end_year_var.get())
                    if s_y > e_y:
                        s_y, e_y = e_y, s_y
                    df = df[(df["study_date"].dt.year >= s_y) & (df["study_date"].dt.year <= e_y)]

                elif time_period == "季":
                    s_qy = self.start_q_year_var.get().strip()
                    s_qv = self.start_q_val_var.get().strip()
                    e_qy = self.end_q_year_var.get().strip()
                    e_qv = self.end_q_val_var.get().strip()
                    p_start = pd.Period(f"{s_qy}{s_qv}", freq="Q")
                    p_end = pd.Period(f"{e_qy}{e_qv}", freq="Q")
                    if p_start > p_end:
                        p_start, p_end = p_end, p_start
                    periods = df["study_date"].dt.to_period("Q")
                    df = df[(periods >= p_start) & (periods <= p_end)]

                elif time_period == "月":
                    s_my = self.start_m_year_var.get().strip()
                    s_mv = self.start_m_val_var.get().strip()
                    e_my = self.end_m_year_var.get().strip()
                    e_mv = self.end_m_val_var.get().strip()
                    p_start = pd.Period(f"{s_my}-{s_mv}", freq="M")
                    p_end = pd.Period(f"{e_my}-{e_mv}", freq="M")
                    if p_start > p_end:
                        p_start, p_end = p_end, p_start
                    periods = df["study_date"].dt.to_period("M")
                    df = df[(periods >= p_start) & (periods <= p_end)]

                else:  # 日
                    d_start = pd.to_datetime(self.start_d_var.get().strip(), errors="coerce")
                    d_end = pd.to_datetime(self.end_d_var.get().strip(), errors="coerce")
                    if pd.notnull(d_start) and pd.notnull(d_end):
                        if d_start > d_end:
                            d_start, d_end = d_end, d_start
                        d_end_inclusive = d_end + pd.Timedelta(days=1, microseconds=-1)
                        df = df[(df["study_date"] >= d_start) & (df["study_date"] <= d_end_inclusive)]
            except Exception as e:
                messagebox.showwarning("時間區間格式錯誤", f"時間區間設定有誤: {e}，將顯示全區間資料。")

        if df.empty:
            messagebox.showinfo("提示", "所選時間區間內無符合條件的數據！")
            return

        # 設定時間聚合標籤
        if time_period == "年":
            df["period"] = df["study_date"].dt.strftime("%Y")
        elif time_period == "季":
            df["period"] = df["study_date"].dt.to_period("Q").astype(str)
        elif time_period == "月":
            df["period"] = df["study_date"].dt.strftime("%Y-%m")
        else:
            df["period"] = df["study_date"].dt.strftime("%Y-%m-%d")

        self.ax.clear()
        metric_unit = "mGy" if metric == "CTDIVol" else "mGy·cm"
        metric_name = "CTDIvol" if metric == "CTDIVol" else "DLP"

        unique_sites = sorted(df["sites"].unique())
        colors = plt.cm.tab10.colors

        if chart_type == "scatter":
            # --- 點狀圖 (個案標示) ---
            for idx, site in enumerate(unique_sites):
                sdf = df[df["sites"] == site]
                color = colors[idx % len(colors)]
                self.ax.scatter(
                    sdf["study_date"],
                    sdf[metric],
                    label=site,
                    alpha=0.75,
                    s=45,
                    color=color,
                    edgecolors="none"
                )

            self.ax.set_title(f"{metric_name} 個案分佈點狀圖 ({time_period}維度)", fontsize=13, fontweight="bold", pad=10)
            self.ax.set_xlabel("檢查日期", fontsize=10)
            self.ax.set_ylabel(f"{metric_name} ({metric_unit})", fontsize=10)
            self.ax.grid(True, linestyle="--", alpha=0.5)
            self.ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8.5, frameon=True)
            self.fig.autofmt_xdate()

            self.current_report_df = df[[
                "study_ID", "study_date", "period", "sites", "ID", "Name", "acq_type", metric
            ]].copy()

        else:
            # --- 折線圖 (時間重複取平均值) ---
            grouped = df.groupby(["period", "sites"])[metric].agg(["mean", "count"]).reset_index()
            all_periods = sorted(df["period"].unique())

            for idx, site in enumerate(unique_sites):
                sdf = grouped[grouped["sites"] == site].set_index("period").reindex(all_periods).reset_index()
                color = colors[idx % len(colors)]
                self.ax.plot(
                    sdf["period"],
                    sdf["mean"],
                    marker="o",
                    markersize=5,
                    linewidth=2,
                    label=site,
                    color=color
                )

            self.ax.set_title(f"{metric_name} 時間趨勢折線圖 (以{time_period}為週期計算平均值)", fontsize=13, fontweight="bold", pad=10)
            self.ax.set_xlabel(f"時間週期 ({time_period})", fontsize=10)
            self.ax.set_ylabel(f"平均 {metric_name} ({metric_unit})", fontsize=10)
            self.ax.grid(True, linestyle="--", alpha=0.5)

            if len(all_periods) > 15:
                step = max(1, len(all_periods) // 10)
                visible_ticks = [all_periods[i] for i in range(0, len(all_periods), step)]
                self.ax.set_xticks(range(0, len(all_periods), step))
                self.ax.set_xticklabels(visible_ticks, rotation=35, ha="right", fontsize=8.5)
            else:
                self.ax.set_xticks(range(len(all_periods)))
                self.ax.set_xticklabels(all_periods, rotation=30, ha="right", fontsize=8.5)

            self.ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8.5, frameon=True)

            summary_pivot = df.groupby(["period", "sites"])[metric].agg(
                平均值="mean", 筆數="count", 最大值="max", 最小值="min", 標準差="std"
            ).reset_index()
            self.current_report_df = summary_pivot

        self.fig.tight_layout()
        self.canvas.draw()

        total_records = len(df)
        overall_mean = df[metric].mean()
        overall_median = df[metric].median()
        overall_max = df[metric].max()
        overall_min = df[metric].min()

        info_txt = (
            f"納入分析: {total_records} 筆個案 | "
            f"部位: {len(unique_sites)} 類 | "
            f"平均值: {overall_mean:.2f} {metric_unit} | "
            f"中位數: {overall_median:.2f} | "
            f"區間: [{overall_min:.2f} ~ {overall_max:.2f}]"
        )
        self.chart_info_label.config(text=info_txt)

    def _export_chart_image(self):
        filepath = filedialog.asksaveasfilename(
            title="儲存圖表圖片",
            defaultextension=".png",
            filetypes=[("PNG 圖片", "*.png"), ("PDF 文件", "*.pdf"), ("JPEG 圖片", "*.jpg")]
        )
        if filepath:
            try:
                self.fig.savefig(filepath, dpi=300, bbox_inches="tight")
                messagebox.showinfo("成功", f"圖表已順利匯出至：\n{filepath}")
            except Exception as e:
                messagebox.showerror("錯誤", f"匯出圖表失敗: {e}")

    def _export_chart_csv(self):
        if self.current_report_df.empty:
            messagebox.showwarning("提示", "目前無圖表資料可匯出，請先點擊「產生圖表」！")
            return

        filepath = filedialog.asksaveasfilename(
            title="儲存報表資料為 CSV",
            defaultextension=".csv",
            filetypes=[("CSV 檔案", "*.csv")]
        )
        if filepath:
            try:
                self.current_report_df.to_csv(filepath, index=False, encoding="utf-8-sig")
                messagebox.showinfo("成功", f"統計報表資料已匯出至：\n{filepath}")
            except Exception as e:
                messagebox.showerror("錯誤", f"匯出 CSV 失敗: {e}")

    # ----------------------------------------------------
    # 介面共用輔助方法
    # ----------------------------------------------------
    def log(self, message):
        def _append():
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
            self.log_text.see(tk.END)
        self.root.after(0, _append)

    def _clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def _browse_folder(self):
        current = self.path_var.get().strip()
        init_dir = current if os.path.isdir(current) else "."
        selected = filedialog.askdirectory(title="請選擇 DICOM 工作母資料夾", initialdir=init_dir)
        if selected:
            self.path_var.set(selected)
            self._save_current_settings(silent=True)
            self._check_path_status()
            self.log(f"已更換工作母目錄: {selected}")

    def _reset_to_default_path(self):
        self.path_var.set(DEFAULT_PATH)
        self._save_current_settings(silent=True)
        self._check_path_status()
        self.log(f"已重設母目錄為預設值: {DEFAULT_PATH}")

    def _check_path_status(self):
        p = self.path_var.get().strip()
        if os.path.exists(p):
            self.path_status_label.config(text=f"✓ 目錄存在: {p}", foreground="green")
        else:
            self.path_status_label.config(text=f"⚠ 提示: 路徑不存在或網路磁碟未連線 ({p})", foreground="#b35900")

    def _refresh_db_status(self):
        """更新目前 SQLite 的資料筆數與路徑資訊"""
        current_db = resolve_db_path(self.db_path_var.get())
        if os.path.exists(current_db):
            try:
                conn = sqlite3.connect(current_db)
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM CT_dose")
                cnt = cur.fetchone()[0]
                conn.close()
                self.db_path_var.set(current_db)
                self.db_info_label.config(text=f"✓ 資料庫累計: {cnt} 筆紀錄")
                return
            except Exception:
                pass
        self.db_info_label.config(text="⚠ 資料庫: 尚未建立或找不到檔案")

    def _save_current_settings(self, silent=False):
        path_val = self.path_var.get().strip()
        omits = [yr for yr, var in self.year_vars.items() if var.get()]

        self.metadata["path"] = path_val
        self.metadata["omit_year"] = omits

        if save_metadata(self.metadata):
            self._check_path_status()
            if not silent:
                messagebox.showinfo("成功", "設定已成功儲存至 metadata.json！")
                self.log("設定已儲存至 metadata.json。")
        else:
            if not silent:
                messagebox.showerror("錯誤", "儲存 metadata.json 失敗，請確認檔案權限！")

    def _open_output_folder(self):
        out_path = Path(OUTPUT_DIR).resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        self._open_system_path(out_path)

    def _open_system_path(self, path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))
            elif sys.platform.startswith("darwin"):
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showwarning("提示", f"無法開啟路徑: {e}")

    # ----------------------------------------------------
    # 背景掃描執行緒
    # ----------------------------------------------------
    def _start_processing(self):
        if self.is_processing:
            return

        target_dir = self.path_var.get().strip()
        if not target_dir:
            messagebox.showwarning("警告", "請指定工作母目錄！")
            return

        if not os.path.exists(target_dir):
            if not messagebox.askyesno("路徑不存在", f"目錄不存在或尚未連線：\n{target_dir}\n\n是否仍要嘗試搜尋？"):
                return

        self._save_current_settings(silent=True)

        self.is_processing = True
        self.run_btn.config(state=tk.DISABLED, text="處理中...", fg="#d32f2f")
        self.progress_bar["value"] = 0
        self.status_label.config(text="正在掃描符合條件的 DICOM 檔案...")

        thread = threading.Thread(target=self._worker_thread, args=(target_dir,), daemon=True)
        thread.start()

    def _worker_thread(self, target_dir):
        try:
            depth = self.depth_var.get()
            self.log("=" * 50)
            self.log(f"任務開始！目標目錄: {target_dir}")

            files = find_dose_dicom_files(
                root_dir=target_dir,
                max_depth=depth,
                log_func=self.log
            )

            if not files:
                self.log("未在該目錄下找到符合開頭為 'SC.' 且結尾為 '.DCM' 的檔案。")
                self.root.after(0, lambda: self.status_label.config(text="未找到符合檔案"))
                self.root.after(0, lambda: messagebox.showinfo("提示", "未找到符合 'SC.*.DCM' 的檔案。"))
                return

            self.root.after(0, lambda: self.status_label.config(text=f"解析 DICOM 檔案中 (共 {len(files)} 個)..."))

            def update_progress(current, total):
                percent = int((current / total) * 100)
                self.root.after(0, lambda: self.progress_bar.configure(value=percent))
                self.root.after(0, lambda: self.status_label.config(text=f"正在解析: {current}/{total} ({percent}%)"))

            df = parse_dicom_files(
                file_list=files,
                log_func=self.log,
                progress_func=update_progress
            )

            if df.empty:
                self.log("解析完成，但無有效 SCREEN SAVE 劑量資料。")
                self.root.after(0, lambda: messagebox.showinfo("提示", "無符合 SCREEN SAVE 條件的有效劑量資料。"))
                return

            target_db = self.db_path_var.get()
            self.root.after(0, lambda: self.status_label.config(text="備份寫入 SQLite 資料庫..."))
            ct_database = backup_sql(df, db_path=target_db, log_func=self.log)

            self.root.after(0, lambda: self.status_label.config(text="匯出 CSV 報表中..."))
            export_csv(ct_database, self.metadata, log_func=self.log)

            self.log("所有處理已圓滿完成！")
            self.root.after(0, self._reload_all_db_data)
            self.root.after(0, lambda: self.status_label.config(text="處理完成！"))
            self.root.after(0, lambda: messagebox.showinfo("完成", "DICOM 劑量解析、資料庫更新與 CSV 匯出已全數完成！"))

        except Exception as e:
            self.log(f"處理過程發生未預期錯誤: {e}")
            self.root.after(0, lambda: messagebox.showerror("執行錯誤", f"發生錯誤:\n{e}"))
            self.root.after(0, lambda: self.status_label.config(text="處理失敗"))
        finally:
            self.is_processing = False
            self.root.after(0, lambda: self.run_btn.config(state=tk.NORMAL, text="▶ 開始掃描與評估", fg="#d32f2f"))


# ==========================================
# CLI 命令列模式支援 (--update)
# ==========================================
def setup_cli_console():
    """在 Windows GUI 模式下附加至呼叫端終端機 (cmd/powershell) 以正常輸出文字"""
    if sys.platform == "win32":
        try:
            import ctypes
            # 附加至父行程終端機 (ATTACH_PARENT_PROCESS = -1)
            if ctypes.windll.kernel32.AttachConsole(-1):
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        except Exception:
            pass


def run_cli_update():
    """CLI 模式：讀取 metadata.json 預設母目錄，解析 DICOM 並更新資料庫 (不輸出 CSV)"""
    setup_cli_console()
    print("=" * 60)
    print("  CT Dose 輻射劑量自動化評估系統 - CLI 自動更新模式")
    print("=" * 60)

    # 1. 讀取設定檔
    meta = load_metadata()
    target_dir = meta.get("path", DEFAULT_PATH)
    db_path = resolve_db_path()

    print(f"[*] 工作母目錄: {target_dir}")
    print(f"[*] 資料庫路徑: {db_path}")

    # 2. 檢查母目錄是否存在
    if not os.path.exists(target_dir):
        print(f"\n[錯誤] 工作母目錄不存在或網路磁碟未連線：\n{target_dir}")
        print("請確認網路連線，或在 metadata.json 中修改 path 設定。")
        sys.exit(1)

    # 3. 搜尋符合條件之檔案 (SC.*.DCM, 最多 3 層)
    print("\n[*] 正在掃描符合條件之 DICOM 報告 (SC.*.DCM，最多 3 層子目錄)...")
    files = find_dose_dicom_files(root_dir=target_dir, max_depth=3, log_func=print)

    if not files:
        print("[提示] 未在該目錄下找到符合開頭為 'SC.' 且結尾為 '.DCM' 的檔案。")
        sys.exit(0)

    # 4. 解析 DICOM 檔案
    print(f"\n[*] 開始解析 {len(files)} 個檔案...")
    last_pct = [-1]
    def cli_progress(curr, total):
        pct = int(curr / total * 100)
        if pct % 10 == 0 and pct != last_pct[0]:
            last_pct[0] = pct
            print(f"    處理進度: {curr}/{total} ({pct}%)")

    df = parse_dicom_files(file_list=files, log_func=None, progress_func=cli_progress)

    if df.empty:
        print("[提示] DICOM 解析完成，但無有效 SCREEN SAVE 劑量資料。")
        sys.exit(0)

    # 5. 寫入 / 更新 SQLite 資料庫 (自動去重，不輸出 CSV)
    print("\n[*] 寫入 SQLite 資料庫 (CTdose_eval.db)...")
    ct_db = backup_sql(df, db_path=db_path, log_func=print)

    # 6. 更新 metadata.json 的最後執行日期
    meta["last_reading_date"] = str(datetime.date.today())
    save_metadata(meta)
    print(f"[*] 已更新 metadata.json 最後讀取日期: {meta['last_reading_date']}")

    print("=" * 60)
    print(f"[完成] 資料庫更新成功！資料庫目前總累積: {len(ct_db)} 筆紀錄。")
    print("=" * 60)
    sys.exit(0)


# ==========================================
# 程式進入點
# ==========================================
def main():
    args = sys.argv[1:]

    # 判斷 CLI 參數
    if "--update" in args or "-u" in args:
        run_cli_update()
        return
    elif "--help" in args or "-h" in args:
        setup_cli_console()
        print("CT Dose 輻射劑量自動化評估系統")
        print("使用方式:")
        print("  ctdose.exe            啟動 GUI 視窗介面")
        print("  ctdose.exe --update   僅更新資料庫 (讀取預設母目錄，不產生 CSV)")
        print("  ctdose.exe --help     顯示此說明訊息")
        sys.exit(0)

    # 預設啟動 GUI 視窗
    root = tk.Tk()
    app = CTDoseApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
