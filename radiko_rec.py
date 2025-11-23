import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import base64
import hashlib
import time
import subprocess
import threading
import os
from datetime import datetime, timedelta
import queue
import xml.etree.ElementTree as ET

# --- 設定と定数 ---

# Radiko認証に使用される静的な秘密鍵 (rec_radiko_tsから引用された値のバイト表現)
# 認証プロトコルの核心部分であり、正確性が要求されます。
AUTHKEY_VALUE = b"bcd151073c03b352e1ef2fd66c32209da9ca0afa"

# Radiko API エンドポイント
URL_AUTH1 = "https://radiko.jp/v2/api/auth1"
URL_AUTH2 = "https://radiko.jp/v2/api/auth2"
URL_PREMIUM_LOGIN = "https://radiko.jp/v4/api/member/login"
URL_PREMIUM_LOGOUT = "https://radiko.jp/v4/api/member/logout"

# --- 認証とメタデータ処理クラス ---

class RadikoAuth:
    """
    Radikoの多段階認証とPartialKey生成を管理するクラス。
    シェルスクリプトのdd/base64/curlロジックをPythonネイティブで再現する 。
    """
    def __init__(self, log_callback):
        self.authtoken = None
        self.area_id = None
        self.radiko_session = None
        self.log = log_callback
        self.session = requests.Session()

    def _generate_partial_key(self, keyoffset, keylength):
        """
        Auth1で取得したオフセットと長さに基づき、静的キーからPartialKeyを生成する。
        これはrec_radiko_tsにおけるdd/base64コマンド処理を代替する 。
        """
        try:
            offset = int(keyoffset)
            length = int(keylength)
            
            # Pythonのバイトスライスでddコマンドのロジックを再現
            partial_key_bytes = AUTHKEY_VALUE[offset : offset + length]
            
            # Base64エンコードでbase64コマンドのロジックを再現
            partial_key = base64.b64encode(partial_key_bytes).decode('utf-8')
            return partial_key
        except Exception as e:
            self.log(f"エラー: PartialKey生成中に失敗しました: {e}")
            return None

    def auth(self, mail=None, password=None):
        """
        Radiko認証フロー（Auth1 -> Premium Login -> Auth2）を実行する。
        """
        self.log("Radiko認証を開始します...")
        
        # 0. プレミアムログイン (オプション)
        if mail and password:
            if not self._premium_login(mail, password):
                return False
        
        # 1. Auth1: AuthToken, KeyOffset, KeyLengthの取得
    # Auth1
        auth1_headers = {
            "User-Agent": "curl/7.52.1",
            "Accept": "*/*",
            "X-Radiko-App": "pc_html5",
            "X-Radiko-App-Version": "0.0.1",
            "X-Radiko-Device": "pc",
            "X-Radiko-User": "dummy_user",
        }

        try:
            res1 = self.session.get(URL_AUTH1, headers=auth1_headers, timeout=5)
            res1.raise_for_status()
        except requests.RequestException as e:
            self.log(f"エラー: Auth1リクエストに失敗しました: {e} ")
            return False

        # AuthTokenとKey情報をレスポンスヘッダから抽出 
        self.authtoken = res1.headers.get("X-Radiko-AuthToken")
        keyoffset = res1.headers.get("X-Radiko-KeyOffset")
        keylength = res1.headers.get("X-Radiko-KeyLength")

        if not all([self.authtoken, keyoffset, keylength]):
            self.log("エラー: Auth1応答ヘッダから必須情報(Token, Offset, Length)が取得できませんでした。")
            return False
        
        self.log("Auth1成功: 認証トークンを取得しました。")
        
        # PartialKeyの生成
        partial_key = self._generate_partial_key(keyoffset, keylength)
        if not partial_key:
            return False

        # 2. Auth2: PartialKeyとAuthTokenを送信し、エリアIDを取得
        auth2_headers = {
            "User-Agent": "curl/7.52.1",
            "Accept": "*/*",
            "X-Radiko-Device": "pc",
            "X-Radiko-User": "dummy_user",
            "X-Radiko-App": "pc_html5",
            "X-Radiko-App-Version": "0.0.1",
            "X-Radiko-AuthToken": self.authtoken,
            "X-Radiko-PartialKey": partial_key,
        }

        
        # Premiumセッションがある場合はURLにクエリパラメータを追加 
        auth2_url = URL_AUTH2
        if self.radiko_session:
            auth2_url += f"?radiko_session={self.radiko_session}"

        try:
            res2 = self.session.get(auth2_url, headers=auth2_headers, timeout=5)
            res2.raise_for_status()
        except requests.RequestException as e:
            self.log(f"エラー: Auth2リクエストに失敗しました: {e} ")
            return False

        # エリアIDは応答ボディに含まれる（CSV風テキスト）
        body = res2.text.strip()
        # デバッグしたくなったらコメントアウトを外す
        self.log(f"Auth2レスポンス: {body}")

        # OUT または空文字はエリア判定失敗
        if not body or body == "OUT":
            self.log("エラー: Auth2でエリアが判定されませんでした。(レスポンスが空 or OUT)")
            return False

        # 1行目を取り出してカンマ区切りの先頭要素が area_id
        first_line = body.splitlines()[0]
        self.area_id = first_line.split(",")[0].strip()

        if not self.area_id:
            self.log("エラー: Auth2応答ボディからエリアIDが抽出できませんでした。")
            return False

        self.log(f"Auth2成功: エリアID '{self.area_id}' を取得しました。")
        return True
            
    def _premium_login(self, mail, password):
        """Radiko Premiumログインを実行し、radiko_sessionを取得する """
        login_data = {"mail": mail, "pass": password}
        try:
            res = self.session.post(URL_PREMIUM_LOGIN, data=login_data, timeout=5)
            res.raise_for_status()

            data = res.json()
            self.radiko_session = data.get("radiko_session")
            areafree = data.get("areafree")

            if self.radiko_session and areafree == "1":
                self.log("Premiumログインに成功しました。エリアフリー録音が可能です。")
                # cookie は self.session.cookies に自動で入っている
                return True
            else:
                self.log("エラー: Premiumログインに失敗しました。認証情報をご確認ください。")
                return False
        except Exception as e:
            self.log(f"エラー: Premiumログイン中に例外が発生しました: {e} ")
            return False

    def logout(self):
        """Premiumセッションを終了する """
        if self.radiko_session:
            self.log("Premiumセッションをログアウトします...")
            logout_data = {"radiko_session": self.radiko_session}
            try:
                requests.post(URL_PREMIUM_LOGOUT, data=logout_data, timeout=5)
            except requests.RequestException:
                # ログアウトの失敗は致命的ではないが記録
                self.log("警告: ログアウト処理中にエラーが発生しました。")
            finally:
                self.radiko_session = None
        
import xml.etree.ElementTree as ET

class RadikoMetadata:
    def __init__(self, auth, log_callback):
        self.auth = auth
        self.log = log_callback
        self.STATIONS = {
            "TBS": "TBSラジオ",
            "QRR": "文化放送",
            "LFR": "ニッポン放送",
            "RN1": "ラジオNIKKEI第1",
            "FMJ": "J-WAVE",
            # 必要に応じて追加
        }

    def get_stations(self):
        return self.STATIONS

    def get_programs(self, station_id, date_str):
        """
        Radiko公式の番組表APIから、指定局・指定日の番組一覧を取得する。
        date_str: 'YYYYMMDD'
        """
        if station_id not in self.STATIONS:
            self.log(f"警告: 未知の局IDが指定されました: {station_id}")
            return []

        url = f"https://radiko.jp/v3/program/station/date/{date_str}/{station_id}.xml"
        self.log(f"番組表APIにアクセス: {url}")

        try:
            # 認証用セッションがあるならそれを使う（Cookie共有）
            session = self.auth.session if getattr(self.auth, "session", None) else requests
            res = session.get(url, timeout=10)
            res.raise_for_status()
        except requests.RequestException as e:
            self.log(f"エラー: 番組表取得に失敗しました: {e}")
            return []

        try:
            root = ET.fromstring(res.content)
        except ET.ParseError as e:
            self.log(f"エラー: 番組表XMLの解析に失敗しました: {e}")
            return []

        program_data = []
        # //prog ノードを全部拾う
        for p in root.findall(".//prog"):
            ft = p.attrib.get("ft")  # 例: '20240521050000'
            to = p.attrib.get("to")
            title = p.findtext("title", default="(タイトル不明)")

            if not ft or not to:
                continue

            try:
                start_dt = datetime.strptime(ft, "%Y%m%d%H%M%S")
                end_dt   = datetime.strptime(to, "%Y%m%d%H%M%S")
            except ValueError:
                continue

            program_data.append(
                {
                    "title": title,
                    "start_time_dt": start_dt,
                    "end_time_dt": end_dt,
                    "start_time_str": ft,
                    "end_time_str": to,
                }
            )

        self.log(f"番組表取得: {len(program_data)} 件")
        return program_data

class StreamDownloader:
    """
    FFmpegをsubprocessで実行し、Radikoストリームを高速にM4Aファイルとしてダウンロードするクラス。
    """
    def __init__(self, auth, log_callback):
        self.auth = auth
        self.log = log_callback
        self.process = None

    def _generate_tracking_key(self):
        """
        Radiko追跡キー (lsid) のための擬似ランダムMD5ハッシュを生成する。
        rec_radiko_tsの`/dev/random` + `base64`ロジックをPythonで再現する 。
        """
        # 100バイトのランダムデータを生成
        random_bytes = os.urandom(100)
        
        # Base64エンコード
        encoded_bytes = base64.b64encode(random_bytes)
        
        # MD5ハッシュを計算し、32文字の小文字の16進数文字列として返す
        tracking_key = hashlib.md5(encoded_bytes).hexdigest()
        return tracking_key

    def download(self, station_id, start_time_str, end_time_str, output_path, progress_callback):
        """
        FFmpegプロセスを起動し、ストリームをコピーする。
        start_time_str, end_time_str は YYYYMMDDHHMMSS 形式 。
        """
        if not self.auth.authtoken:
            self.log("エラー: 認証トークンがありません。ダウンロード前に認証を実行してください。")
            return False

        # M3U8ストリームURLの構築 
        lsid = self._generate_tracking_key()
        
        # ts/playlist.m3u8 へのリクエストに必要なパラメータ
        url_params = {
            "station_id": station_id,
            "start_at": start_time_str,
            "ft": start_time_str,
            "end_at": end_time_str,
            "to": end_time_str,
            "seek": start_time_str,
            "l": "15", # 固定パラメータ 
            "lsid": lsid,
            "type": "c", # 固定パラメータ 
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in url_params.items())
        m3u8_url = f"https://radiko.jp/v2/api/ts/playlist.m3u8?{query_string}"
        
        # FFmpegコマンドの構築
        # 認証トークンは -headers オプションで渡す 
        # -acodec copy と -bsf:a aac_adtstoasc は高速化とM4A互換性のために必須 
        # FFmpeg用ヘッダ（Authtoken + AreaId）
        header_lines = [
            f"X-Radiko-Authtoken: {self.auth.authtoken}",
        ]
        if self.auth.area_id:
            header_lines.append(f"X-Radiko-AreaId: {self.auth.area_id}")
        
        headers_str = "\r\n".join(header_lines) + "\r\n"
        
        ffmpeg_command = [
            "ffmpeg",
            "-loglevel", "error",
            "-fflags", "+discardcorrupt",
            "-headers", headers_str,
            "-i", m3u8_url,
            "-acodec", "copy",
            "-vn",
            "-bsf:a", "aac_adtstoasc",
            "-y",
            output_path,
        ]
        
        self.log(f"FFmpegで録音を開始: {output_path}")
        
        try:
            # subprocess.Popen でプロセスを起動し、非同期で実行する
            self.process = subprocess.Popen(
                ffmpeg_command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,  # エラーだけ取りたいならこのままでもOK
                universal_newlines=True
            )
            
            # ダウンロード進捗の監視を開始
            self._monitor_progress(start_time_str, end_time_str, progress_callback)
            
            # FFmpegプロセスの終了を待つ (タイムアウトなし)
            stdout, stderr = self.process.communicate() 
            return_code = self.process.returncode

            if return_code!= 0:
                self.log(f"エラー: FFmpegプロセスが非ゼロコード {return_code} で終了しました。")
                self.log(f"FFmpeg出力:\n{stderr}")
                return False
            
            self.log("録音成功: ファイルがM4A形式で保存されました。")
            return True

        except FileNotFoundError:
            self.log("エラー: 'ffmpeg' コマンドが見つかりません。FFmpegがインストールされ、PATHが通っていることを確認してください。")
            return False
        except Exception as e:
            self.log(f"エラー: ダウンロード中に予期せぬエラーが発生しました: {e}")
            return False

    def _monitor_progress(self, start_time_str, end_time_str, progress_callback):
        """
        FFmpegの進捗をログパースなしのタイマーで擬似的に監視する。
        """
        start_dt = datetime.strptime(start_time_str, '%Y%m%d%H%M%S')
        end_dt = datetime.strptime(end_time_str, '%Y%m%d%H%M%S')
        total_duration = (end_dt - start_dt).total_seconds()
        
        start_time = time.time()
        
        while self.process and self.process.poll() is None:
            elapsed_time = time.time() - start_time
            # 経過時間に基づいた擬似的な進捗計算
            progress_percent = min(100, (elapsed_time / total_duration) * 100)
            
            # GUIへ進捗をフィードバック
            progress_callback(progress_percent)
            time.sleep(1)

        # 終了時には100%に設定
        progress_callback(100)

    def stop_download(self):
        """実行中のFFmpegプロセスを安全に停止する"""
        if self.process and self.process.poll() is None:
            self.log("ダウンロードを中断しています...")
            # SIGINT/SIGTERMを送信してプロセスを終了させる
            self.process.terminate() 
            try:
                self.process.wait(timeout=5)
                self.log("ダウンロードが中断されました。")
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.log("警告: プロセスを強制終了しました。")


# --- Tkinter GUIとController ---

class RadikoGUI:
    def __init__(self, master):
        self.master = master
        master.title("Radiko Time-Free 高速ダウンローダー")
        
        # ログメッセージをGUIに表示するためのスレッドセーフなキュー
        self.log_queue = queue.Queue()

        # モデル層の初期化
        self.auth = RadikoAuth(self.add_log)
        self.metadata = RadikoMetadata(self.auth, self.add_log)
        self.downloader = StreamDownloader(self.auth, self.add_log)
        self.station_vars = {} # ステーションIDと番組情報の保持用

        # GUIコンポーネントの構築
        self._create_widgets(master)
        
        # ログの定期的な更新を開始
        self.master.after(100, self._process_log_queue)
        
        # アプリケーション終了時にログアウト処理を確実に実行
        master.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _create_widgets(self, master):
        # メインフレーム
        main_frame = ttk.Frame(master, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # --- 認証セクション ---
        auth_frame = ttk.LabelFrame(main_frame, text="認証情報 (Premium オプション)", padding="10")
        auth_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(auth_frame, text="Mail:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.mail_entry = ttk.Entry(auth_frame, width=30)
        self.mail_entry.grid(row=0, column=1, padx=5, pady=5, sticky=(tk.W, tk.E))
        
        ttk.Label(auth_frame, text="Password:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.pass_entry = ttk.Entry(auth_frame, width=30, show="*")
        self.pass_entry.grid(row=1, column=1, padx=5, pady=5, sticky=(tk.W, tk.E))
        
        self.auth_button = ttk.Button(auth_frame, text="認証 & 局リスト取得", command=self._start_auth_thread)
        self.auth_button.grid(row=2, column=0, columnspan=2, pady=10)

        # --- 選択セクション ---
        select_frame = ttk.LabelFrame(main_frame, text="番組選択", padding="10")
        select_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)
        
        # 放送局選択
        ttk.Label(select_frame, text="放送局:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.station_var = tk.StringVar(master)
        self.station_dropdown = ttk.Combobox(select_frame, textvariable=self.station_var, state='disabled')
        self.station_dropdown.grid(row=0, column=1, padx=5, pady=5, sticky=(tk.W, tk.E))
        self.station_dropdown.bind('<<ComboboxSelected>>', self._load_programs)

        # 日付選択 (簡易版として今日の日付)
        ttk.Label(select_frame, text="日付 (YYYYMMDD):").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.date_entry = ttk.Entry(select_frame, width=10)
        self.date_entry.insert(0, datetime.now().strftime('%Y%m%d'))
        self.date_entry.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)
        self.date_entry.bind('<Return>', self._load_programs)
        
        self.load_button = ttk.Button(select_frame, text="番組表ロード", command=self._load_programs)
        self.load_button.grid(row=1, column=2, padx=5, pady=5)

        # 番組リストボックス
        list_frame = ttk.Frame(select_frame)
        list_frame.grid(row=2, column=0, columnspan=3, pady=10, sticky=(tk.W, tk.E))
        self.program_list = tk.Listbox(list_frame, height=8, width=60)
        self.program_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, command=self.program_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.program_list.config(yscrollcommand=scrollbar.set)
        
        # --- ダウンロードセクション ---
        download_frame = ttk.LabelFrame(main_frame, text="ダウンロード", padding="10")
        download_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(download_frame, text="保存先:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.output_path_var = tk.StringVar(value=os.path.join(os.path.expanduser('~'), 'radiko_recordings'))
        self.output_entry = ttk.Entry(download_frame, textvariable=self.output_path_var, width=40)
        self.output_entry.grid(row=0, column=1, padx=5, pady=5, sticky=(tk.W, tk.E))
        ttk.Button(download_frame, text="参照", command=self._select_output_dir).grid(row=0, column=2, padx=5, pady=5)
        
        self.download_button = ttk.Button(download_frame, text="ダウンロード開始", command=self._start_download_thread, state='disabled')
        self.download_button.grid(row=1, column=0, columnspan=2, pady=10, sticky=tk.W)
        
        self.stop_button = ttk.Button(download_frame, text="中断", command=self._stop_download, state='disabled')
        self.stop_button.grid(row=1, column=2, pady=10, sticky=tk.E)

        # 進捗バー
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(download_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E))
        
        # --- ログセクション ---
        log_frame = ttk.LabelFrame(main_frame, text="ログ", padding="5")
        log_frame.grid(row=3, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        self.log_text = tk.Text(log_frame, height=8, width=70, state='disabled')
        self.log_text.pack(fill="both", expand=True)
        
        # グリッドの拡張設定
        main_frame.columnconfigure(0, weight=1)
        select_frame.columnconfigure(1, weight=1)
        download_frame.columnconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)

    # --- Controller/Thread管理メソッド ---

    def add_log(self, message):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.log_queue.put(f"{timestamp} {message}\n")

    def _process_log_queue(self):
        self.log_text.config(state='normal')
        while not self.log_queue.empty():
            log_entry = self.log_queue.get()
            self.log_text.insert(tk.END, log_entry)
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')
        self.master.after(100, self._process_log_queue)

    def _start_auth_thread(self):
        """認証処理をバックグラウンドスレッドで開始する。"""
        self.auth_button.config(state='disabled')
        self.add_log("認証スレッドを開始します...")
        
        mail = self.mail_entry.get()
        password = self.pass_entry.get()
        
        # 認証処理をメインスレッドをブロックしないようにスレッドで実行
        thread = threading.Thread(target=self._run_auth, args=(mail, password))
        thread.start()

    def _run_auth(self, mail, password):
        """認証処理の実体 (スレッド内実行)"""
        success = self.auth.auth(mail, password)
        
        # メインスレッドに戻ってGUIを更新
        self.master.after(0, lambda: self._update_gui_after_auth(success))

    def _update_gui_after_auth(self, success):
        """認証結果に基づいてGUIの状態を更新する。"""
        self.auth_button.config(state='normal')
        if success:
            self.add_log("認証完了。局リストを取得・更新します。")
            stations = self.metadata.get_stations()
            station_names = list(stations.values())
            self.station_vars = stations
            
            self.station_dropdown['values'] = station_names
            self.station_dropdown.config(state='readonly')
            if station_names:
                self.station_var.set(station_names[0])
                self._load_programs()
        else:
            messagebox.showerror("認証失敗", "Radiko認証に失敗しました。ログを確認してください。")
            self.station_dropdown.config(state='disabled')
            self.download_button.config(state='disabled')

    def _load_programs(self, event=None):
        """番組表ロードをメインスレッドをブロックしないようにスレッドで実行"""
        station_name = self.station_var.get()
        date_str = self.date_entry.get()
        
        if not station_name or not date_str or not self.auth.authtoken:
            return

        self.load_button.config(state='disabled')
        self.add_log(f"番組表をロード中 ({station_name}, {date_str})...")
        
        thread = threading.Thread(target=self._run_load_programs, args=(station_name, date_str))
        thread.start()

    def _run_load_programs(self, station_name, date_str):
        """番組表ロードの実体 (スレッド内実行)"""
        station_id = next((k for k, v in self.station_vars.items() if v == station_name), None)
        programs = self.metadata.get_programs(station_id, date_str)
        
        # メインスレッドに戻ってGUIを更新
        self.master.after(0, lambda: self._update_gui_after_program_load(programs))

    def _update_gui_after_program_load(self, programs):
        """番組表ロード結果に基づいてGUIを更新する。"""
        self.load_button.config(state='normal')
        self.program_list.delete(0, tk.END)
        self.program_data = programs # 全ての番組情報を保持
        
        if not programs:
            self.add_log("番組情報がありませんでした。")
            self.download_button.config(state='disabled')
            return

        for i, p in enumerate(programs):
            start = p['start_time_dt'].strftime('%H:%M')
            end = p['end_time_dt'].strftime('%H:%M')
            display_text = f"{start} - {end}: {p['title']}"
            self.program_list.insert(tk.END, display_text)
            
        self.add_log(f"{len(programs)} 件の番組をリストに表示しました。")
        self.download_button.config(state='normal')

    def _select_output_dir(self):
        """保存先ディレクトリを選択する"""
        folder_selected = filedialog.askdirectory(initialdir=self.output_path_var.get())
        if folder_selected:
            self.output_path_var.set(folder_selected)

    def _start_download_thread(self):
        """ダウンロード処理をバックグラウンドスレッドで開始する。"""
        selected_index = self.program_list.curselection()
        if not selected_index:
            messagebox.showerror("エラー", "ダウンロードする番組を選択してください。")
            return
        
        # 選択された番組データを取得
        program_index = selected_index[0]
        program = self.program_data[program_index]
        station_name = self.station_var.get()
        station_id = next((k for k, v in self.station_vars.items() if v == station_name), None)
        
        if not station_id:
            messagebox.showerror("エラー", "放送局IDが見つかりません。")
            return
        
        # 出力パスを決定
        output_dir = self.output_path_var.get()
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        filename = f"{station_id}_{program['start_time_str']}_{program['end_time_str']}.m4a"
        output_path = os.path.join(output_dir, filename)

        self.download_button.config(state='disabled')
        self.stop_button.config(state='normal')
        self.progress_var.set(0)
        
        self.add_log(f"ダウンロード準備中: {program['title']}")
        
        # ダウンロード処理をメインスレッドをブロックしないようにスレッドで実行
        thread = threading.Thread(target=self._run_download, args=(station_id, program, output_path))
        thread.start()

    def _run_download(self, station_id, program, output_path):
        """ダウンロード処理の実体 (スレッド内実行)"""
        
        def update_progress(percent):
            """進捗をメインスレッドに安全にフィードバックするコールバック"""
            self.master.after(0, lambda: self.progress_var.set(percent))

        success = self.downloader.download(
            station_id, 
            program['start_time_str'], 
            program['end_time_str'], 
            output_path, 
            update_progress
        )
        
        # 終了時にGUIの状態を更新
        self.master.after(0, lambda: self._update_gui_after_download(success))

    def _update_gui_after_download(self, success):
        """ダウンロード完了またはエラー時にGUIの状態を更新する。"""
        self.download_button.config(state='normal')
        self.stop_button.config(state='disabled')
        self.progress_var.set(100 if success else 0)
        
        if success:
            messagebox.showinfo("完了", "ダウンロードが正常に完了しました！")
        else:
            messagebox.showerror("失敗", "ダウンロードに失敗しました。ログを確認してください。")

    def _stop_download(self):
        """中断ボタンのコマンド"""
        self.downloader.stop_download()
        self.download_button.config(state='normal')
        self.stop_button.config(state='disabled')

    def _on_closing(self):
        """アプリケーション終了時のクリーンアップ処理"""
        # プレミアムログインしていた場合、ログアウトを試みる
        self.auth.logout() 
        # 実行中のダウンロードがあれば停止
        self.downloader.stop_download()
        self.master.destroy()

if __name__ == "__main__":
    # OSに応じて適切なスケーリングを有効にする
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass # Linux/macOSでは無視

    root = tk.Tk()
    app = RadikoGUI(root)
    root.mainloop()