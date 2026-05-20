"""
AIコメント太郎 - GUI管理アプリ v3.58
tkinterを使ったデスクトップGUIアプリです。
このファイルを実行するとGUIが起動します: python gui_app.py
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import queue
import logging
import sys
import os
import time

# ログをGUIに転送するハンドラー
class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))


class BotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AIコメント太郎 v3.58")
        self.root.geometry("820x660")
        self.root.resizable(True, True)
        self.root.configure(bg="#1a1a2e")

        # カラーテーマ
        self.colors = {
            "bg": "#1a1a2e",
            "panel": "#16213e",
            "accent": "#9147ff",  # Twitchパープル
            "accent_hover": "#772ce8",
            "success": "#00b894",
            "danger": "#d63031",
            "text": "#efeff1",
            "text_dim": "#adadb8",
            "border": "#2d2d4e",
            "log_bg": "#0e0e1a",
            "gemini": "#4285f4",  # Googleブルー
        }

        self.bot_thread = None
        self.bot_running = False
        self.log_queue = queue.Queue()
        self.bot_instance = None

        self._setup_styles()
        self._build_ui()
        self._start_log_polling()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("Panel.TFrame", background=self.colors["panel"])
        style.configure("TLabel",
                        background=self.colors["panel"],
                        foreground=self.colors["text"],
                        font=("Yu Gothic UI", 10))
        style.configure("Title.TLabel",
                        background=self.colors["bg"],
                        foreground=self.colors["text"],
                        font=("Yu Gothic UI", 14, "bold"))
        style.configure("Status.TLabel",
                        background=self.colors["panel"],
                        foreground=self.colors["text_dim"],
                        font=("Yu Gothic UI", 9))
        style.configure("TEntry",
                        fieldbackground=self.colors["log_bg"],
                        foreground=self.colors["text"],
                        insertcolor=self.colors["text"],
                        font=("Yu Gothic UI", 10))
        style.configure("TNotebook",
                        background=self.colors["bg"],
                        borderwidth=0)
        style.configure("TNotebook.Tab",
                        background=self.colors["panel"],
                        foreground=self.colors["text_dim"],
                        padding=[12, 6],
                        font=("Yu Gothic UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", self.colors["accent"])],
                  foreground=[("selected", self.colors["text"])])

    def _load_game_titles(self) -> list:
        """games/フォルダのJSONからゲームタイトル一覧を読み込む"""
        import json
        import os

        titles = ["なし（自動判断）"]
        base_dir = os.path.dirname(os.path.abspath(__file__))
        games_dir = os.path.join(base_dir, 'games')

        if not os.path.exists(games_dir):
            return titles

        for filename in sorted(os.listdir(games_dir)):
            if not filename.endswith('.json'):
                continue
            filepath = os.path.join(games_dir, filename)
            try:
                with open(filepath, encoding='utf-8') as f:
                    data = json.load(f)
                title = data.get('title')
                if title and title not in titles:
                    titles.append(title)
            except Exception:
                pass

        return titles

    def _build_ui(self):
        # ヘッダー
        header = tk.Frame(self.root, bg=self.colors["bg"], pady=10)
        header.pack(fill="x", padx=16)

        tk.Label(header, text="🎮  AIコメント太郎  v3.58",
                 bg=self.colors["bg"], fg=self.colors["text"],
                 font=("Yu Gothic UI", 16, "bold")).pack(side="left")

        # バージョンバッジ
        tk.Label(header, text="Gemini API",
                 bg=self.colors["gemini"], fg="white",
                 font=("Yu Gothic UI", 9, "bold"),
                 padx=8, pady=2).pack(side="left", padx=8)

        # ステータスバッジ
        self.status_badge = tk.Label(header, text="● 停止中",
                                     bg=self.colors["bg"],
                                     fg=self.colors["danger"],
                                     font=("Yu Gothic UI", 10, "bold"))
        self.status_badge.pack(side="right", padx=8)

        # タブ
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        # タブ1：コントロール＆ログ
        tab_main = ttk.Frame(notebook, style="TFrame")
        notebook.add(tab_main, text="  コントロール  ")

        # タブ2：設定
        tab_settings = ttk.Frame(notebook, style="TFrame")
        notebook.add(tab_settings, text="  設定  ")

        self._build_main_tab(tab_main)
        self._build_settings_tab(tab_settings)

    def _build_main_tab(self, parent):
        # ボタンエリア
        btn_frame = tk.Frame(parent, bg=self.colors["bg"], pady=10)
        btn_frame.pack(fill="x", padx=8)

        self.start_btn = tk.Button(
            btn_frame, text="▶  Bot 起動",
            bg=self.colors["accent"], fg="white",
            font=("Yu Gothic UI", 11, "bold"),
            relief="flat", bd=0, padx=24, pady=8,
            activebackground=self.colors["accent_hover"],
            activeforeground="white",
            cursor="hand2",
            command=self.start_bot
        )
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = tk.Button(
            btn_frame, text="■  Bot 停止",
            bg=self.colors["border"], fg=self.colors["text_dim"],
            font=("Yu Gothic UI", 11, "bold"),
            relief="flat", bd=0, padx=24, pady=8,
            activebackground=self.colors["danger"],
            activeforeground="white",
            cursor="hand2",
            state="disabled",
            command=self.stop_bot
        )
        self.stop_btn.pack(side="left", padx=(0, 8))

        clear_btn = tk.Button(
            btn_frame, text="🗑  ログ消去",
            bg=self.colors["border"], fg=self.colors["text_dim"],
            font=("Yu Gothic UI", 10),
            relief="flat", bd=0, padx=16, pady=8,
            cursor="hand2",
            command=self.clear_log
        )
        clear_btn.pack(side="left", padx=(0, 8))

        # 画面テストボタン
        screen_test_btn = tk.Button(
            btn_frame, text="📷  画面テスト",
            bg=self.colors["success"], fg="white",
            font=("Yu Gothic UI", 10, "bold"),
            relief="flat", bd=0, padx=16, pady=8,
            cursor="hand2",
            command=self.test_screen_capture
        )
        screen_test_btn.pack(side="left")

        # 会話ステート表示
        state_frame = tk.Frame(parent, bg=self.colors["panel"], pady=4)
        state_frame.pack(fill="x", padx=8, pady=(0, 4))

        tk.Label(state_frame, text="会話ステート:",
                 bg=self.colors["panel"], fg=self.colors["text_dim"],
                 font=("Yu Gothic UI", 9)).pack(side="left", padx=(8, 4))

        self.state_label = tk.Label(state_frame, text="待機中",
                                    bg=self.colors["panel"], fg=self.colors["gemini"],
                                    font=("Yu Gothic UI", 9, "bold"))
        self.state_label.pack(side="left")

        self.turns_label = tk.Label(state_frame, text="",
                                    bg=self.colors["panel"], fg=self.colors["text_dim"],
                                    font=("Yu Gothic UI", 9))
        self.turns_label.pack(side="left", padx=(8, 0))

        # APIリクエスト数表示
        tk.Label(state_frame, text="|",
                 bg=self.colors["panel"], fg=self.colors["border"],
                 font=("Yu Gothic UI", 9)).pack(side="left", padx=(12, 4))
        tk.Label(state_frame, text="API/分:",
                 bg=self.colors["panel"], fg=self.colors["text_dim"],
                 font=("Yu Gothic UI", 9)).pack(side="left", padx=(0, 4))
        self.api_count_label = tk.Label(state_frame, text="0 / 15",
                                        bg=self.colors["panel"], fg=self.colors["text"],
                                        font=("Yu Gothic UI", 9, "bold"))
        self.api_count_label.pack(side="left")

        # ログエリア
        log_frame = tk.Frame(parent, bg=self.colors["bg"])
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        tk.Label(log_frame, text="ログ",
                 bg=self.colors["bg"], fg=self.colors["text_dim"],
                 font=("Yu Gothic UI", 9)).pack(anchor="w", pady=(0, 4))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            bg=self.colors["log_bg"],
            fg=self.colors["text"],
            font=("Consolas", 9),
            relief="flat",
            bd=0,
            state="disabled",
            wrap="word"
        )
        self.log_text.pack(fill="both", expand=True)

        # ログの色設定
        self.log_text.tag_configure("INFO", foreground="#efeff1")
        self.log_text.tag_configure("ERROR", foreground="#ff6b6b")
        self.log_text.tag_configure("WARNING", foreground="#ffd93d")
        self.log_text.tag_configure("DEBUG", foreground="#6c757d")
        self.log_text.tag_configure("COMMENT", foreground="#9147ff")
        self.log_text.tag_configure("FILTER", foreground="#00b894")

    def _build_settings_tab(self, parent):
        # スクロール可能フレーム
        canvas = tk.Canvas(parent, bg=self.colors["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg=self.colors["bg"])

        scroll_frame.bind("<Configure>",
                          lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def make_section(title, color=None):
            f = tk.Frame(scroll_frame, bg=self.colors["panel"],
                         relief="flat", bd=0)
            f.pack(fill="x", padx=12, pady=(10, 0))
            label_color = color if color else self.colors["accent"]
            tk.Label(f, text=title,
                     bg=self.colors["panel"], fg=label_color,
                     font=("Yu Gothic UI", 10, "bold")).pack(anchor="w", padx=12, pady=(8, 4))
            return f

        def make_field(parent, label, var, show=""):
            row = tk.Frame(parent, bg=self.colors["panel"])
            row.pack(fill="x", padx=12, pady=3)
            tk.Label(row, text=label, width=26, anchor="w",
                     bg=self.colors["panel"], fg=self.colors["text"],
                     font=("Yu Gothic UI", 10)).pack(side="left")
            e = tk.Entry(row, textvariable=var, show=show,
                         bg=self.colors["log_bg"], fg=self.colors["text"],
                         insertbackground=self.colors["text"],
                         relief="flat", bd=4, font=("Yu Gothic UI", 10))
            e.pack(side="left", fill="x", expand=True)
            return e

        def make_note(parent, text):
            tk.Label(parent, text=text,
                     bg=self.colors["panel"], fg=self.colors["text_dim"],
                     font=("Yu Gothic UI", 8), wraplength=620, justify="left"
                     ).pack(anchor="w", padx=12, pady=(0, 6))

        # 設定変数
        self.var_bot_nick = tk.StringVar()
        self.var_bot_token = tk.StringVar()
        self.var_streamer_token = tk.StringVar()
        self.var_twitch_client_id = tk.StringVar()
        self.var_twitch_client_secret = tk.StringVar()
        self.var_channel = tk.StringVar()
        self.var_gemini_api_key = tk.StringVar()
        self.var_gemini_model = tk.StringVar()
        self.var_speech_min_length = tk.StringVar()
        self.var_comment_cooldown = tk.StringVar()
        self.var_silence_comment = tk.StringVar()
        self.var_max_turns = tk.StringVar()
        self.var_topic_cooldown = tk.StringVar()
        self.var_screen_enabled = tk.BooleanVar()
        self.var_screen_interval = tk.StringVar()
        self.var_monitor_index = tk.StringVar()
        self.var_retry_count = tk.StringVar()
        self.var_retry_interval = tk.StringVar()
        self.var_chat_mute_enabled = tk.BooleanVar()
        self.var_viewer_comment_reaction_enabled = tk.BooleanVar()
        self.var_reaction_bot_accounts = tk.StringVar()
        self.var_chat_threshold = tk.StringVar()
        self.var_chat_window = tk.StringVar()
        self.var_chat_quiet = tk.StringVar()
        self.var_excluded_accounts = tk.StringVar()
        self.var_ng_words = tk.StringVar()
        self.var_ai_name = tk.StringVar()
        self.var_streamer_name = tk.StringVar()
        self.var_comment_max_tokens = tk.StringVar()
        self.var_viewer_commands_enabled = tk.BooleanVar()
        self.var_viewer_command_prefix = tk.StringVar()
        self.var_command_hello_enabled = tk.BooleanVar()
        self.var_command_status_enabled = tk.BooleanVar()

        # Twitch設定
        sec1 = make_section("Twitch 接続設定")
        make_field(sec1, "Botアカウント名", self.var_bot_nick)
        make_field(sec1, "OAuthトークン (Bot)", self.var_bot_token, show="*")
        make_field(sec1, "チャンネル名", self.var_channel)
        make_field(sec1, "OAuthトークン (配信者)", self.var_streamer_token, show="*")
        make_field(sec1, "Client ID", self.var_twitch_client_id, show="*")
        make_field(sec1, "Client Secret", self.var_twitch_client_secret, show="*")
        make_note(sec1, "Client ID・Client SecretはTwitch配信情報の自動取得に使用。dev.twitch.tvのアプリ管理画面から取得。")

        # Gemini API設定（最重要）
        sec_gemini = make_section("Gemini API 設定（最重要）", color=self.colors["gemini"])
        make_field(sec_gemini, "Gemini APIキー", self.var_gemini_api_key, show="*")
        make_note(sec_gemini, "APIキーの取得: https://aistudio.google.com/app/apikey  無料枠: 1日1500リクエスト / 1分15リクエスト")
        make_note(sec_gemini, "使用モデル: gemini-2.5-flash-lite（固定）")

        # AI キャラクター設定
        sec_ai = make_section("AI キャラクター設定", color="#00b894")
        make_field(sec_ai, "AIの名前", self.var_ai_name)
        make_note(sec_ai, "配信者がこの名前で呼びかけると直接会話モードになります。例: 太郎、アリス")
        make_field(sec_ai, "配信者の名前", self.var_streamer_name)
        make_note(sec_ai, "AIが配信者を呼ぶときに使用します。例: 配信者名・ニックネームなど")

        tokens_row = tk.Frame(sec_ai, bg=self.colors["panel"])
        tokens_row.pack(fill="x", padx=12, pady=3)
        tk.Label(tokens_row, text="コメント最大長", width=26, anchor="w",
                 bg=self.colors["panel"], fg=self.colors["text"],
                 font=("Yu Gothic UI", 10)).pack(side="left")
        tokens_combo = ttk.Combobox(
            tokens_row, textvariable=self.var_comment_max_tokens,
            values=["150", "300", "500"],
            state="readonly", font=("Yu Gothic UI", 10), width=10
        )
        tokens_combo.pack(side="left")
        make_note(sec_ai, "150: 短め（1文）/ 300: 中文（2〜3文・推奨）/ 500: 長め（3〜5文）")

        # 発言フィルター設定
        sec_filter = make_section("発言フィルター設定")
        make_field(sec_filter, "最小文字数", self.var_speech_min_length)
        make_note(sec_filter, "この文字数以下の発言は無視します。VC向けの短い指示語（「右」「左」等）を除外するため。推奨: 10文字")

        # 会話ステート管理設定
        sec_conv = make_section("会話ステート管理設定")
        make_field(sec_conv, "最大往復回数", self.var_max_turns)
        make_note(sec_conv, "同じ話題を何往復で締めるか。推奨: 3回（3往復したら自動的に話題を締める）")
        make_field(sec_conv, "話題終了後の待機 (秒)", self.var_topic_cooldown)
        make_note(sec_conv, "話題を締めた後、次の話題を振るまで待つ時間。推奨: 60秒")

        # タイミング設定
        sec3 = make_section("タイミング設定")
        make_field(sec3, "コメント間隔 (秒)", self.var_comment_cooldown)
        make_note(sec3, "コメントを投稿する最小間隔（秒）。短すぎると連投になります。推奨: 20秒以上")
        make_field(sec3, "無言検知 (秒)", self.var_silence_comment)
        make_note(sec3, "この秒数以上無言が続いたら話しかけます。推奨: 120秒（2分）以上")
        make_field(sec3, "リトライ回数", self.var_retry_count)
        make_note(sec3, "コメント生成失敗時の再試行回数。レート制限が頻発する場合は 0 を推奨（失敗したら即座にスキップ）")
        make_field(sec3, "リトライ間隔 (秒)", self.var_retry_interval)
        make_note(sec3, "リトライまで待機する秒数。レート制限時は少し待つことで次のリクエストが通りやすくなります。推奨: 5秒以上")

        # チャット監視設定
        sec4 = make_section("他視聴者コメント監視設定")
        chk_row = tk.Frame(sec4, bg=self.colors["panel"])
        chk_row.pack(fill="x", padx=12, pady=3)
        tk.Checkbutton(
            chk_row, text="他の視聴者がコメントしているときはbotを黙らせる",
            variable=self.var_chat_mute_enabled,
            bg=self.colors["panel"], fg=self.colors["text"],
            selectcolor=self.colors["log_bg"],
            activebackground=self.colors["panel"],
            font=("Yu Gothic UI", 10)
        ).pack(side="left")
        make_field(sec4, "活発判定の件数", self.var_chat_threshold)
        make_note(sec4, "直近の時間内にこの件数以上のコメントがあればbotが黙る。推奨: 3")
        make_field(sec4, "活発判定の時間幅 (秒)", self.var_chat_window)
        make_note(sec4, "直近何秒以内のコメントを数えるか。推奨: 60秒")
        make_field(sec4, "静まり判定の秒数", self.var_chat_quiet)
        make_note(sec4, "この秒数間コメントがなければbotが再開する。推奨: 30秒")
        make_field(sec4, "除外アカウント", self.var_excluded_accounts)
        make_note(sec4, "無視するアカウントをカンマ区切りで入力。例: moobot,fossabot")

        # 視聴者コメント反応設定
        chk_reaction_row = tk.Frame(sec4, bg=self.colors["panel"])
        chk_reaction_row.pack(fill="x", padx=12, pady=3)
        tk.Checkbutton(
            chk_reaction_row, text="他の視聴者コメント・ボット通知に反応する",
            variable=self.var_viewer_comment_reaction_enabled,
            bg=self.colors["panel"], fg=self.colors["text"],
            selectcolor=self.colors["log_bg"],
            activebackground=self.colors["panel"],
            font=("Yu Gothic UI", 10)
        ).pack(side="left")
        make_field(sec4, "反応するボットアカウント", self.var_reaction_bot_accounts)
        make_note(sec4, "お知らせ系ボットのみ指定。例: nightbot,streamelements")

        # 視聴者コマンド設定
        sec_cmd = make_section("視聴者コマンド設定", color="#ffd93d")
        chk_cmd_row = tk.Frame(sec_cmd, bg=self.colors["panel"])
        chk_cmd_row.pack(fill="x", padx=12, pady=3)
        tk.Checkbutton(
            chk_cmd_row, text="視聴者コマンドを有効にする",
            variable=self.var_viewer_commands_enabled,
            bg=self.colors["panel"], fg=self.colors["text"],
            selectcolor=self.colors["log_bg"],
            activebackground=self.colors["panel"],
            font=("Yu Gothic UI", 10)
        ).pack(side="left")
        make_field(sec_cmd, "コマンドプレフィックス", self.var_viewer_command_prefix)
        make_note(sec_cmd, "視聴者がAIに質問するコマンド。例: !太郎 と入力すると !太郎 こんにちは で質問できます")
        chk_hello_row = tk.Frame(sec_cmd, bg=self.colors["panel"])
        chk_hello_row.pack(fill="x", padx=12, pady=3)
        tk.Checkbutton(
            chk_hello_row, text="!hello コマンドを有効にする（視聴者が挨拶するとAIが返す）",
            variable=self.var_command_hello_enabled,
            bg=self.colors["panel"], fg=self.colors["text"],
            selectcolor=self.colors["log_bg"],
            activebackground=self.colors["panel"],
            font=("Yu Gothic UI", 10)
        ).pack(side="left")
        chk_status_row = tk.Frame(sec_cmd, bg=self.colors["panel"])
        chk_status_row.pack(fill="x", padx=12, pady=3)
        tk.Checkbutton(
            chk_status_row, text="!status コマンドを有効にする（視聴者が配信状況を聞くとAIが答える）",
            variable=self.var_command_status_enabled,
            bg=self.colors["panel"], fg=self.colors["text"],
            selectcolor=self.colors["log_bg"],
            activebackground=self.colors["panel"],
            font=("Yu Gothic UI", 10)
        ).pack(side="left")

        # 保存ボタン
        btn_row = tk.Frame(scroll_frame, bg=self.colors["bg"])
        btn_row.pack(fill="x", padx=12, pady=16)

        tk.Button(
            btn_row, text="💾  設定を保存",
            bg=self.colors["success"], fg="white",
            font=("Yu Gothic UI", 11, "bold"),
            relief="flat", bd=0, padx=24, pady=8,
            cursor="hand2",
            command=self.save_settings
        ).pack(side="left")

        # 設定を読み込む
        self._load_settings()

    def _load_settings(self):
        """config.pyから設定を読み込む"""
        try:
            import importlib.util
            config_path = os.path.join(os.path.dirname(__file__), "config.py")
            spec = importlib.util.spec_from_file_location("config", config_path)
            cfg = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cfg)

            self.var_bot_nick.set(getattr(cfg, "BOT_NICK", ""))
            self.var_bot_token.set(getattr(cfg, "BOT_TOKEN", ""))
            self.var_streamer_token.set(getattr(cfg, "STREAMER_TOKEN", ""))
            self.var_twitch_client_id.set(getattr(cfg, "TWITCH_CLIENT_ID", ""))
            self.var_twitch_client_secret.set(getattr(cfg, "TWITCH_CLIENT_SECRET", ""))
            self.var_channel.set(getattr(cfg, "CHANNEL_NAME", ""))
            self.var_gemini_api_key.set(getattr(cfg, "GEMINI_API_KEY", ""))
            self.var_gemini_model.set(getattr(cfg, "GEMINI_MODEL", "gemini-1.5-flash"))
            self.var_speech_min_length.set(str(getattr(cfg, "SPEECH_MIN_LENGTH", "10")))
            self.var_comment_cooldown.set(str(getattr(cfg, "COMMENT_COOLDOWN_SECONDS", "20")))
            self.var_silence_comment.set(str(getattr(cfg, "SILENCE_COMMENT_THRESHOLD", "120")))
            self.var_max_turns.set(str(getattr(cfg, "CONVERSATION_MAX_TURNS", "3")))
            self.var_topic_cooldown.set(str(getattr(cfg, "TOPIC_COOLDOWN_SECONDS", "60")))
            self.var_screen_enabled.set(getattr(cfg, "SCREEN_RECOGNITION_ENABLED", True))
            self.var_screen_interval.set(str(getattr(cfg, "SCREEN_CAPTURE_INTERVAL", "300")))
            self.var_monitor_index.set(str(getattr(cfg, "SCREEN_MONITOR_INDEX", "1")))
            self.var_retry_count.set(str(getattr(cfg, "COMMENT_RETRY_COUNT", "2")))
            self.var_retry_interval.set(str(getattr(cfg, "COMMENT_RETRY_INTERVAL", "5")))
            self.var_chat_mute_enabled.set(getattr(cfg, "CHAT_ACTIVITY_MUTE_ENABLED", True))
            self.var_viewer_comment_reaction_enabled.set(getattr(cfg, "VIEWER_COMMENT_REACTION_ENABLED", True))
            self.var_reaction_bot_accounts.set(getattr(cfg, "REACTION_BOT_ACCOUNTS", "nightbot,streamelements"))
            self.var_chat_threshold.set(str(getattr(cfg, "CHAT_ACTIVITY_THRESHOLD", "3")))
            self.var_chat_window.set(str(getattr(cfg, "CHAT_ACTIVITY_WINDOW_SECONDS", "60")))
            self.var_chat_quiet.set(str(getattr(cfg, "CHAT_QUIET_RESUME_SECONDS", "30")))
            self.var_excluded_accounts.set(getattr(cfg, "EXCLUDED_ACCOUNTS", "nightbot,streamelements,moobot,fossabot"))
            self.var_ng_words.set(getattr(cfg, "NG_WORDS", ""))
            self.var_ai_name.set(getattr(cfg, "AI_NAME", "AIコメント太郎"))
            self.var_streamer_name.set(getattr(cfg, "STREAMER_NAME", ""))
            self.var_comment_max_tokens.set(str(getattr(cfg, "COMMENT_MAX_TOKENS", "300")))
            self.var_viewer_commands_enabled.set(getattr(cfg, "VIEWER_COMMANDS_ENABLED", True))
            self.var_viewer_command_prefix.set(getattr(cfg, "VIEWER_COMMAND_PREFIX", "!太郎"))
            self.var_command_hello_enabled.set(getattr(cfg, "COMMAND_HELLO_ENABLED", True))
            self.var_command_status_enabled.set(getattr(cfg, "COMMAND_STATUS_ENABLED", True))
        except Exception as e:
            self._append_log(f"設定の読み込みに失敗しました: {e}", "ERROR")

    def save_settings(self):
        """設定をconfig.pyとsecrets.pyに書き込む"""
        try:
            base_dir = os.path.dirname(__file__)
            config_path = os.path.join(base_dir, "config.py")
            secrets_path = os.path.join(base_dir, "secrets.py")

            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()

            def replace_value(text, key, new_value, is_string=True):
                import re
                if is_string:
                    pattern = rf'^({key}\s*=\s*)["\'].*?["\']'
                    replacement = rf'\g<1>"{new_value}"'
                else:
                    pattern = rf'^({key}\s*=\s*)[\w.]+$'
                    replacement = rf'\g<1>{new_value}'
                return re.sub(pattern, replacement, text, flags=re.MULTILINE)

            content = replace_value(content, "SPEECH_MIN_LENGTH",
                                    self.var_speech_min_length.get(), is_string=False)
            content = replace_value(content, "COMMENT_COOLDOWN_SECONDS",
                                    self.var_comment_cooldown.get(), is_string=False)
            content = replace_value(content, "SILENCE_COMMENT_THRESHOLD",
                                    self.var_silence_comment.get(), is_string=False)
            content = replace_value(content, "CONVERSATION_MAX_TURNS",
                                    self.var_max_turns.get(), is_string=False)
            content = replace_value(content, "TOPIC_COOLDOWN_SECONDS",
                                    self.var_topic_cooldown.get(), is_string=False)
            content = replace_value(content, "SCREEN_RECOGNITION_ENABLED",
                                    str(self.var_screen_enabled.get()), is_string=False)
            content = replace_value(content, "SCREEN_CAPTURE_INTERVAL",
                                    self.var_screen_interval.get(), is_string=False)
            content = replace_value(content, "COMMENT_RETRY_COUNT",
                                    self.var_retry_count.get(), is_string=False)
            content = replace_value(content, "COMMENT_RETRY_INTERVAL",
                                    self.var_retry_interval.get(), is_string=False)
            content = replace_value(content, "CHAT_ACTIVITY_MUTE_ENABLED",
                                    str(self.var_chat_mute_enabled.get()), is_string=False)
            content = replace_value(content, "VIEWER_COMMENT_REACTION_ENABLED",
                                    str(self.var_viewer_comment_reaction_enabled.get()), is_string=False)
            content = replace_value(content, "REACTION_BOT_ACCOUNTS",
                                    self.var_reaction_bot_accounts.get())
            content = replace_value(content, "CHAT_ACTIVITY_THRESHOLD",
                                    self.var_chat_threshold.get(), is_string=False)
            content = replace_value(content, "CHAT_ACTIVITY_WINDOW_SECONDS",
                                    self.var_chat_window.get(), is_string=False)
            content = replace_value(content, "CHAT_QUIET_RESUME_SECONDS",
                                    self.var_chat_quiet.get(), is_string=False)
            content = replace_value(content, "COMMENT_MAX_TOKENS",
                                    self.var_comment_max_tokens.get(), is_string=False)
            content = replace_value(content, "VIEWER_COMMANDS_ENABLED",
                                    str(self.var_viewer_commands_enabled.get()), is_string=False)
            content = replace_value(content, "COMMAND_HELLO_ENABLED",
                                    str(self.var_command_hello_enabled.get()), is_string=False)
            content = replace_value(content, "COMMAND_STATUS_ENABLED",
                                    str(self.var_command_status_enabled.get()), is_string=False)

            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)

            # 個人設定はsecrets.pyに保存
            if os.path.exists(secrets_path):
                with open(secrets_path, "r", encoding="utf-8") as f:
                    secrets_content = f.read()

                # キーが存在しない場合は末尾に自動追記
                def ensure_key(content, key, value, is_string=True):
                    if key not in content:
                        if is_string:
                            content += f'\n{key} = "{value}"\n'
                        else:
                            content += f'\n{key} = {value}\n'
                    return content

                secrets_content = ensure_key(secrets_content, "STREAMER_NAME", self.var_streamer_name.get())
                secrets_content = ensure_key(secrets_content, "REACTION_BOT_ACCOUNTS", self.var_reaction_bot_accounts.get())

                secrets_content = replace_value(secrets_content, "BOT_NICK", self.var_bot_nick.get())
                secrets_content = replace_value(secrets_content, "BOT_TOKEN", self.var_bot_token.get())
                secrets_content = ensure_key(secrets_content, "STREAMER_TOKEN", self.var_streamer_token.get())
                secrets_content = replace_value(secrets_content, "STREAMER_TOKEN", self.var_streamer_token.get())
                secrets_content = ensure_key(secrets_content, "TWITCH_CLIENT_ID", self.var_twitch_client_id.get())
                secrets_content = replace_value(secrets_content, "TWITCH_CLIENT_ID", self.var_twitch_client_id.get())
                secrets_content = ensure_key(secrets_content, "TWITCH_CLIENT_SECRET", self.var_twitch_client_secret.get())
                secrets_content = replace_value(secrets_content, "TWITCH_CLIENT_SECRET", self.var_twitch_client_secret.get())
                secrets_content = replace_value(secrets_content, "CHANNEL_NAME", self.var_channel.get())
                secrets_content = replace_value(secrets_content, "GEMINI_API_KEY", self.var_gemini_api_key.get())
                secrets_content = replace_value(secrets_content, "AI_NAME", self.var_ai_name.get())
                secrets_content = replace_value(secrets_content, "STREAMER_NAME", self.var_streamer_name.get())
                secrets_content = replace_value(secrets_content, "VIEWER_COMMAND_PREFIX", self.var_viewer_command_prefix.get())
                secrets_content = replace_value(secrets_content, "EXCLUDED_ACCOUNTS", self.var_excluded_accounts.get())
                secrets_content = replace_value(secrets_content, "SCREEN_MONITOR_INDEX",
                                                self.var_monitor_index.get(), is_string=False)

                with open(secrets_path, "w", encoding="utf-8") as f:
                    f.write(secrets_content)

            # NGワードはconfig.pyに保存
            content = replace_value(content, "NG_WORDS", self.var_ng_words.get())

            messagebox.showinfo("保存完了", "設定を保存しました。\n変更を反映するにはBotを再起動してください。")
        except Exception as e:
            messagebox.showerror("エラー", f"設定の保存に失敗しました:\n{e}")

    def start_bot(self):
        """Botを別スレッドで起動する"""
        if self.bot_running:
            return

        self.bot_running = True
        self.start_btn.config(state="disabled", bg=self.colors["border"],
                              fg=self.colors["text_dim"])
        self.stop_btn.config(state="normal", bg=self.colors["danger"], fg="white")
        self.status_badge.config(text="● 稼働中", fg=self.colors["success"])

        # ロギングをGUIに転送（全ての既存ハンドラーを削除してからQueueHandlerのみ追加）
        root_logger = logging.getLogger()
        for h in root_logger.handlers[:]:
            root_logger.removeHandler(h)
        queue_handler = QueueHandler(self.log_queue)
        queue_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S"
        ))
        root_logger.addHandler(queue_handler)
        root_logger.setLevel(logging.INFO)

        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()

    def _run_bot(self):
        """Botのメインループを実行する（別スレッド）"""
        try:
            # モジュールを再読み込みして最新の設定を反映
            for mod_name in ["config", "audio_module",
                             "comment_generator", "twitch_module", "screen_module"]:
                if mod_name in sys.modules:
                    del sys.modules[mod_name]

            import config
            from audio_module import AudioModule
            from comment_generator import CommentGenerator, CommentTrigger
            from twitch_module import TwitchModule
            from screen_module import ScreenModule

            # モジュール再インポート後、ルートロガーのハンドラーを再度クリーンアップしてQueueHandlerのみにする
            # （モジュールインポート時に追加ハンドラーが生じる場合の対策）
            _root_logger = logging.getLogger()
            _queue_handlers = [h for h in _root_logger.handlers if isinstance(h, QueueHandler)]
            for h in _root_logger.handlers[:]:
                _root_logger.removeHandler(h)
            if _queue_handlers:
                _root_logger.addHandler(_queue_handlers[-1])
            else:
                _qh = QueueHandler(self.log_queue)
                _qh.setFormatter(logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%H:%M:%S"
                ))
                _root_logger.addHandler(_qh)
            _root_logger.setLevel(logging.INFO)

            logger = logging.getLogger("gui_bot")
            logger.info("=" * 50)
            logger.info("AIコメント太郎 v3.58 を起動します")
            logger.info(f"チャンネル: #{config.CHANNEL_NAME}")
            logger.info(f"音声認識: Google Web Speech API（日本語）")
            logger.info(f"コメント生成: Gemini API ({config.GEMINI_MODEL})")
            logger.info(f"発言フィルター: {config.SPEECH_MIN_LENGTH}文字以下を無視")
            logger.info(f"会話ステート: 最大{config.CONVERSATION_MAX_TURNS}往復 / {config.TOPIC_COOLDOWN_SECONDS}秒クールダウン")
            screen_status = "有効" if config.SCREEN_RECOGNITION_ENABLED else "無効"
            logger.info(f"画面認識: {screen_status}")
            logger.info("=" * 50)

            comment_gen = CommentGenerator(config)
            twitch = TwitchModule(config)
            audio = AudioModule(config)
            audio.set_config(config)  # NGワード前段階フィルター用
            screen = ScreenModule(config)
            # 排他制御用に comment_generator の参照をセット
            screen.comment_generator = comment_gen
            screen.audio_module = audio
            screen.twitch_module = twitch

            self.bot_instance = {
                "audio": audio,
                "twitch": twitch,
                "screen": screen,
                "comment_gen": comment_gen,
            }

            last_comment_time = [0.0]

            # 発言バッファ用変数
            speech_buffer = []
            speech_timer = [None]
            unrecognized_count = [0]

            def can_send():
                # クールダウンチェック
                if (time.time() - last_comment_time[0]) < config.COMMENT_COOLDOWN_SECONDS:
                    return False
                # チャットが活発なときは黙る
                if twitch.is_chat_active():
                    quiet_seconds = getattr(config, 'CHAT_QUIET_RESUME_SECONDS', 30)
                    last_chat = twitch.get_last_chat_time()
                    if last_chat > 0 and (time.time() - last_chat) < quiet_seconds:
                        logger.debug("チャットが活発なためbotは黙黙中")
                        return False
                return True

            # 画面認識結果が更新されたときにコメントを生成するコールバックをセット
            def on_screen_updated(description: str):
                if not can_send():
                    return
                comment = comment_gen.generate(
                    CommentTrigger.SCREEN_EVENT,
                    screen_situation=description
                )
                if comment:
                    logger.info(f"コメント送信: {comment}")
                    self.log_queue.put(f"COMMENT:{comment}")
                    twitch.send_comment(comment)
                    last_comment_time[0] = time.time()
            screen.on_description_updated = on_screen_updated

            def process_speech_buffer():
                """バッファに溜まった発言をまとめて処理する"""
                if not speech_buffer:
                    return

                # Bot停止後は処理しない
                if not self.bot_running:
                    speech_buffer.clear()
                    return

                combined_text = " ".join(speech_buffer)
                speech_buffer.clear()

                # AI名前呼びかけの検出（クールダウンをバイパス）
                ai_name = getattr(config, 'AI_NAME', '太郎')
                is_direct_call = False
                direct_question = combined_text
                for sep in ['、', '，', ' ', '。']:
                    if combined_text.startswith(ai_name + sep):
                        is_direct_call = True
                        direct_question = combined_text[len(ai_name) + len(sep):].strip()
                        break
                if combined_text.startswith(ai_name) and not is_direct_call:
                    is_direct_call = True
                    direct_question = combined_text[len(ai_name):].strip()

                if is_direct_call:
                    logger.info(f"[{ai_name}呼びかけ] {direct_question}")
                    screen_situation = screen.get_latest_description()
                    comment = comment_gen.generate(
                        CommentTrigger.DIRECT_CONVERSATION,
                        speech_text=direct_question or combined_text,
                        screen_situation=screen_situation
                    )
                    if comment:
                        logger.info(f"コメント送信: {comment}")
                        self.log_queue.put(f"COMMENT:{comment}")
                        twitch.send_comment(comment)
                        last_comment_time[0] = time.time()
                        self._update_state_display(comment_gen)
                    return

                if not can_send():
                    if comment_gen._is_search_trigger(combined_text):
                        logger.info("[検索優先] クールダウン中だが検索キーワードを検出、優先処理します")
                        comment_gen.reset_conversation_state()
                        last_comment_time[0] = 0.0
                    else:
                        logger.debug("クールダウン中のためスキップ")
                        return

                logger.info(f"まとまった発言を処理: {combined_text}")

                # 画面状況を取得
                screen_situation = screen.get_latest_description()

                comment = comment_gen.generate(
                    CommentTrigger.SPEECH_RESPONSE,
                    speech_text=combined_text,
                    screen_situation=screen_situation
                )
                if comment:
                    logger.info(f"コメント送信: {comment}")
                    self.log_queue.put(f"COMMENT:{comment}")
                    twitch.send_comment(comment)
                    last_comment_time[0] = time.time()
                    # 会話ステートを更新表示
                    self._update_state_display(comment_gen)

            def on_speech(text):
                # 発言をバッファに追加
                speech_buffer.append(text)
                unrecognized_count[0] = 0  # 成功したらリセット

                # 既存のタイマーをキャンセル
                if speech_timer[0] is not None:
                    speech_timer[0].cancel()

                # 12秒後にバッファを処理するタイマーをセット
                speech_timer[0] = threading.Timer(12.0, process_speech_buffer)
                speech_timer[0].daemon = True
                speech_timer[0].start()

            def on_unrecognized(consecutive: int = 1):
                unrecognized_count[0] = consecutive
                threshold = getattr(config, 'UNRECOGNIZED_THRESHOLD', 6)
                # しきい値回数連続で短時間に聞き取れなかった場合のみ反応
                if consecutive >= threshold:
                    unrecognized_count[0] = 0
                    if not can_send():
                        return

                    comment = comment_gen.generate(
                        CommentTrigger.UNRECOGNIZED_SPEECH,
                        screen_situation=""
                    )
                    if comment:
                        logger.info(f"コメント送信: {comment}")
                        self.log_queue.put(f"COMMENT:{comment}")
                        twitch.send_comment(comment)
                        last_comment_time[0] = time.time()

            def on_silence():
                if not can_send():
                    return
                screen_situation = screen.get_latest_description()
                comment = comment_gen.generate(
                    CommentTrigger.SILENCE_BREAKER,
                    screen_situation=screen_situation
                )
                if comment:
                    logger.info(f"コメント送信: {comment}")
                    self.log_queue.put(f"COMMENT:{comment}")
                    twitch.send_comment(comment)
                    last_comment_time[0] = time.time()
                    self._update_state_display(comment_gen)

            # 視聴者コマンドコールバックを設定
            def on_viewer_command(command_str, username):
                """視聴者コマンドを受け取ったときの処理"""
                screen_situation = screen.get_latest_description()
                comment = comment_gen.generate(
                    CommentTrigger.VIEWER_COMMAND,
                    speech_text=command_str,
                    screen_situation=screen_situation,
                    username=username
                )
                if comment:
                    logger.info(f"[視聴者コマンド] {username} → {comment}")
                    self.log_queue.put(f"COMMENT:{comment}")
                    twitch.send_comment(comment)

            twitch.set_viewer_command_callback(on_viewer_command)

            # 視聴者コメント・ボット通知への反応コールバック
            last_viewer_reaction_time = [0.0]

            def on_viewer_comment(content, username, is_bot=False):
                if not getattr(config, 'VIEWER_COMMENT_REACTION_ENABLED', True):
                    return

                # ボット通知はクールダウンありで反応（頻度を抑える）
                import time as _time
                now = _time.time()
                if is_bot:
                    bot_cooldown = 300  # ボット通知は5分に1回
                    if now - last_viewer_reaction_time[0] < bot_cooldown:
                        return
                    trigger_label = "ボット通知"
                    prompt = f"Twitchのボット通知：「{content}」。これを読んで視聴者として一言コメントしてください。日本語1文のみ。"
                else:
                    # 視聴者コメントを学習
                    profile_mgr = getattr(comment_gen, '_profile_manager', None)
                    if profile_mgr:
                        profile_mgr.add_viewer_comment(username, content)

                    # 視聴者コメントを会話履歴にも追加（俳句の材料にする）
                    if not is_bot and len(content) >= 4:
                        comment_gen._conversation_history.append({
                            'role': 'viewer',
                            'content': f"{username}：{content}"
                        })

                    ai_name = getattr(config, 'AI_NAME', '太郎')
                    is_name_mention = ai_name in content or 'コメント太郎' in content

                    if not is_name_mention:
                        # 通常視聴者コメントは30秒クールダウン
                        cooldown = getattr(config, 'VIEWER_COMMENT_REACTION_COOLDOWN', 30)
                        if now - last_viewer_reaction_time[0] < cooldown:
                            return

                    trigger_label = "視聴者コメント"
                    # 常連かどうかで反応を変える
                    profile_mgr = getattr(comment_gen, '_profile_manager', None)
                    viewer_count = 0
                    if profile_mgr:
                        viewers = profile_mgr._profile.get('known_viewers', {})
                        viewer_count = viewers.get(username, {}).get('count', 0)

                    if viewer_count >= 5:
                        prompt = f"常連の「{username}」が「{content}」とコメントしました。親しみを込めて視聴者として自然に1文で反応してください。日本語のみ。"
                    else:
                        prompt = f"Twitchチャットに「{username}」が「{content}」と書きました。視聴者として自然に1文で反応してください。日本語のみ。"

                logger.info(f"[{trigger_label}反応] {username}: {content}")
                comment = comment_gen._call_gemini(prompt)
                if comment:
                    last_viewer_reaction_time[0] = now
                    logger.info(f"[{trigger_label}] → {comment}")
                    self.log_queue.put(f"COMMENT:{comment}")
                    twitch.send_comment(comment)

            twitch.set_viewer_comment_callback(on_viewer_comment)

            audio.set_speech_callback(on_speech)
            audio.set_silence_callback(on_silence)
            audio.set_unrecognized_callback(on_unrecognized)

            logger.info("Twitch接続を開始します...")
            twitch.start()
            time.sleep(4)

            # 配信情報をTwitch APIから取得
            stream_info = twitch.get_stream_info()
            if stream_info:
                comment_gen.set_stream_info(stream_info)
                if stream_info.get('game_name'):
                    screen._stream_game_name = stream_info['game_name']
                    logger.info(f"📡 配信情報取得成功 - ゲーム: {stream_info['game_name']}")
                if stream_info.get('title'):
                    logger.info(f"📡 配信タイトル: {stream_info['title']}")
            else:
                logger.info("📡 配信情報取得なし（オフラインまたは未配信）")

            logger.info("ゲーム画面認識を開始します...")
            screen.start()

            logger.info("音声認識を開始します（Google Web Speech API）...")
            audio.start()

            logger.info("bot が稼働中です。停止ボタンで停止します。")

            # 俳句・謎かけタイマー設定（15〜25分のランダム間隔）
            import random
            next_event_interval = random.randint(15 * 60, 25 * 60)
            last_event_time = [time.time()]
            logger.info(f"[イベント] 次まで {next_event_interval // 60} 分")

            def _call_gemini_flash(prompt: str) -> str:
                """俳句・謎かけ用にFlashモデルで生成する"""
                try:
                    import google.generativeai as genai
                    genai.configure(api_key=comment_gen.config.GEMINI_API_KEY)
                    model = genai.GenerativeModel(
                        model_name='gemini-2.5-flash',
                        system_instruction=comment_gen.get_system_prompt()
                    )
                    response = model.generate_content(prompt)
                    if response and response.text:
                        return response.text.strip()
                except Exception as e:
                    logger.warning(f"[Flash生成] 失敗、Liteにフォールバック: {e}")
                return comment_gen._call_gemini(prompt) or ""

            def _build_event_context() -> tuple:
                """イベント用の会話履歴とゲーム情報を返す"""
                history = comment_gen._conversation_history[-20:] if comment_gen._conversation_history else []
                stream_info = getattr(comment_gen, '_stream_info', {})
                game_name = stream_info.get('game_name', '')
                game_text = f"配信中のゲーム：{game_name}\n" if game_name else ""
                if history:
                    history_text = "\n".join([
                        f"{'配信者' if m.get('role') == 'streamer' else ('視聴者' if m.get('role') == 'viewer' else '太郎')}: {m.get('content', '')}"
                        for m in history
                    ])
                else:
                    history_text = ""
                return history_text, game_text

            def _do_haiku_event():
                """俳句イベント"""
                history_text, game_text = _build_event_context()
                if history_text:
                    prompt = (
                        f"あなたはTwitch配信の常連視聴者「コメント太郎」です。\n"
                        f"{game_text}"
                        f"今日の配信でこんな会話がありました：\n{history_text}\n\n"
                        f"この配信の雰囲気・出来事・感情を詠んだ俳句を一句作ってください。\n"
                        f"・5・7・5を目安に（字余り・字足らずOK）\n"
                        f"・ありきたりな表現を避け、この配信ならではの言葉を使うこと\n"
                        f"・季語がなくてもOK\n"
                        f"・俳句の本文のみ出力（説明・コメント不要）"
                    )
                else:
                    prompt = (
                        f"あなたはTwitch配信の常連視聴者「コメント太郎」です。\n"
                        f"{game_text}"
                        f"配信を見ている今この瞬間の気持ちを俳句にしてください。\n"
                        f"・5・7・5を目安に（字余り・字足らずOK）\n"
                        f"・ありきたりな表現を避け、独自の言葉を使うこと\n"
                        f"・季語がなくてもOK\n"
                        f"・俳句の本文のみ出力（説明・コメント不要）"
                    )
                haiku = _call_gemini_flash(prompt)
                if haiku and self.bot_running:
                    haiku = haiku.replace('\n', '　')
                    message = f"ここで一句。「{haiku}」"
                    logger.info(f"[俳句] {message}")
                    twitch.send_comment(message)
                    self.log_queue.put(f"COMMENT:{message}")

            def _do_nazokake_event():
                """謎かけイベント"""
                history_text, game_text = _build_event_context()
                if history_text:
                    prompt = (
                        f"あなたはTwitch配信の常連視聴者「コメント太郎」です。\n"
                        f"{game_text}"
                        f"今日の配信でこんな会話がありました：\n{history_text}\n\n"
                        f"この配信の内容をもとに謎かけを一つ作ってください。\n"
                        f"形式：「○○とかけて△△と解く、その心は／□□」\n"
                        f"・「／」で前半と後半を必ず区切ること\n"
                        f"・答え（その心は）は意外性があり、思わず笑えるひねりを効かせること\n"
                        f"・「どちらも〜」という当たり前の答えは絶対禁止\n"
                        f"・配信ならではのワードや状況を活かすこと\n"
                        f"・謎かけ本文のみ出力（説明不要）"
                    )
                else:
                    prompt = (
                        f"あなたはTwitch配信の常連視聴者「コメント太郎」です。\n"
                        f"{game_text}"
                        f"配信の雰囲気をもとに謎かけを一つ作ってください。\n"
                        f"形式：「○○とかけて△△と解く、その心は／□□」\n"
                        f"・「／」で前半と後半を必ず区切ること\n"
                        f"・答え（その心は）は意外性があり、思わず笑えるひねりを効かせること\n"
                        f"・「どちらも〜」という当たり前の答えは絶対禁止\n"
                        f"・謎かけ本文のみ出力（説明不要）"
                    )
                result = _call_gemini_flash(prompt)
                if result and '／' in result and self.bot_running:
                    parts = result.strip().split('／', 1)
                    first = parts[0].strip()
                    second = parts[1].strip() if len(parts) > 1 else ""
                    # 「その心は」が含まれていたら除去（送信側で付ける）
                    first = first.replace('、その心は', '').replace('その心は', '').strip()
                    if first and second:
                        msg1 = f"謎かけいきます。「{first}、その心は」"
                        logger.info(f"[謎かけ前半] {msg1}")
                        twitch.send_comment_priority(msg1)
                        self.log_queue.put(f"COMMENT:{msg1}")
                        time.sleep(1)
                        msg2 = f"「{second}」"
                        logger.info(f"[謎かけ後半] {msg2}")
                        twitch.send_comment_priority(msg2)
                        self.log_queue.put(f"COMMENT:{msg2}")

            # イベントリスト（今後追加しやすい構造）
            event_list = [_do_haiku_event, _do_nazokake_event]

            while self.bot_running:
                time.sleep(1)

                # イベントタイマーチェック
                now = time.time()
                if now - last_event_time[0] >= next_event_interval:
                    try:
                        event_func = random.choice(event_list)
                        event_func()
                    except Exception as e:
                        logger.warning(f"[イベント] 生成失敗: {e}")

                    last_event_time[0] = time.time()
                    next_event_interval = random.randint(15 * 60, 25 * 60)
                    logger.info(f"[イベント] 次まで {next_event_interval // 60} 分")

        except Exception as e:
            logging.getLogger("gui_bot").error(f"Bot実行エラー: {e}")
            import traceback
            logging.getLogger("gui_bot").error(traceback.format_exc())
        finally:
            self._cleanup_bot()

    def _update_state_display(self, comment_gen):
        """会話ステートをGUIに表示する"""
        try:
            state = comment_gen._conversation_state
            turns = comment_gen._current_topic_turns
            max_turns = comment_gen._max_turns

            state_names = {
                "idle": "待機中",
                "topic_raised": "話題を振った",
                "waiting_reply": "返事待ち",
                "deepening": f"深掘り中 ({turns}/{max_turns}往復)",
                "landing": "着地中",
                "cooldown": "クールダウン中",
            }
            state_name = state_names.get(state.value, state.value)
            self.root.after(0, lambda: self.state_label.config(text=state_name))
        except Exception:
            pass

    def _cleanup_bot(self):
        """Botを停止してリソースを解放する"""
        if self.bot_instance:
            try:
                self.bot_instance["audio"].stop()
            except Exception:
                pass
            try:
                self.bot_instance["screen"].stop()
            except Exception:
                pass
            try:
                self.bot_instance["twitch"].stop()
            except Exception:
                pass
            self.bot_instance = None

        self.bot_running = False
        self.root.after(0, self._update_ui_stopped)

    def _update_ui_stopped(self):
        self.start_btn.config(state="normal", bg=self.colors["accent"], fg="white")
        self.stop_btn.config(state="disabled", bg=self.colors["border"],
                             fg=self.colors["text_dim"])
        self.status_badge.config(text="● 停止中", fg=self.colors["danger"])
        self.state_label.config(text="待機中")

    def stop_bot(self):
        """Botを停止する"""
        if not self.bot_running:
            return
        self._append_log("停止中...", "INFO")

        # プロフィールを保存（成長型プロフィール）
        if self.bot_instance and self.bot_instance.get("comment_gen"):
            try:
                self.bot_instance["comment_gen"].reset_history()
                self._append_log("✓ プロフィールを保存しました", "INFO")
            except Exception as e:
                self._append_log(f"プロフィール保存失敗: {e}", "WARNING")

        self.bot_running = False
        self.root.after(2000, lambda: self._append_log("✓ Botを停止しました", "INFO") if not self.bot_running else None)

    def test_screen_capture(self):
        """画面キャプチャのテストを実行する"""
        import threading

        def _run_test():
            try:
                self._append_log("📷 画面テスト開始...", "INFO")

                import sys
                import os
                sys.path.insert(0, os.path.dirname(__file__))
                import config
                from screen_module import ScreenModule

                screen = ScreenModule(config)

                # キャプチャ
                image_bytes = screen._capture_screenshot()
                if not image_bytes:
                    self._append_log("❌ スクリーンショット取得失敗。mss・Pillowが入っているか確認してください。", "ERROR")
                    return

                self._append_log(f"✅ スクリーンショット取得成功 ({len(image_bytes)//1024}KB)", "INFO")
                self._append_log("🔍 Gemini APIで解析中...", "INFO")

                # 解析（生のレスポンスも確認するため直接呼び出し）
                try:
                    model = screen._get_gemini_vision_model()
                    if model is None:
                        self._append_log("❌ Gemini Visionモデルの初期化失敗。APIキーを確認してください。", "ERROR")
                        return
                    import base64
                    import google.generativeai as genai
                    game_title = getattr(config, 'STREAM_GAME_NAME', '') or 'ゲーム配信'
                    prompt = f"これは{game_title}のゲーム配信画面です。画面に何が映っているか日本語で説明してください。"
                    response = model.generate_content([
                        prompt,
                        {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode("utf-8")}
                    ])
                    if response and response.text:
                        self._append_log(f"✅ Gemini生レスポンス: {response.text[:200]}", "INFO")
                    else:
                        self._append_log(f"❌ Geminiレスポンス空。安全フィルターに引っかかっている可能性があります。feedback: {getattr(response, 'prompt_feedback', '不明')}", "WARNING")
                except Exception as e:
                    self._append_log(f"❌ Gemini呼び出しエラー: {e}", "ERROR")

            except Exception as e:
                self._append_log(f"❌ テストエラー: {e}", "ERROR")

        threading.Thread(target=_run_test, daemon=True).start()

    def clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _append_log(self, message: str, level: str = "INFO"):
        self.log_text.config(state="normal")

        # コメント送信は紫色で強調
        if message.startswith("COMMENT:"):
            comment = message[8:]
            self.log_text.insert("end", f"💬 コメント: {comment}\n", "COMMENT")
        elif "[フィルター]" in message:
            self.log_text.insert("end", message + "\n", "FILTER")
        else:
            tag = level if level in ("ERROR", "WARNING", "DEBUG") else "INFO"
            self.log_text.insert("end", message + "\n", tag)

        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _start_log_polling(self):
        """ログキューを定期的にチェックしてGUIに表示する"""
        try:
            while True:
                message = self.log_queue.get_nowait()
                if message.startswith("COMMENT:"):
                    self._append_log(message, "COMMENT")
                elif "[ERROR]" in message:
                    self._append_log(message, "ERROR")
                elif "[WARNING]" in message:
                    self._append_log(message, "WARNING")
                elif "[DEBUG]" in message:
                    self._append_log(message, "DEBUG")
                else:
                    self._append_log(message, "INFO")
        except queue.Empty:
            pass

        # APIリクエスト数を更新
        self._update_api_counter()

        self.root.after(200, self._start_log_polling)

    def _update_api_counter(self):
        """APIリクエスト数カウンターを更新する"""
        try:
            if not self.bot_running or not hasattr(self, 'bot_instance'):
                return
            comment_gen = self.bot_instance.get('comment_gen')
            if comment_gen is None:
                return
            import time
            now = time.time()
            # 直近1分間のリクエスト数をカウント
            recent = [t for t in comment_gen.api_request_times if now - t < 60]
            count = len(recent)
            limit = 15
            # 色を変える：10以上は警告色、全満は危険色
            if count >= limit:
                color = self.colors["danger"]
            elif count >= 10:
                color = "#ffd93d"  # 黄色
            else:
                color = self.colors["text"]
            self.api_count_label.config(
                text=f"{count} / {limit}",
                fg=color
            )
        except Exception:
            pass

    def on_close(self):
        if self.bot_running:
            if messagebox.askyesno("確認", "Botが稼働中です。停止して終了しますか？"):
                self._cleanup_bot()
                self.root.after(1500, self._force_exit)
        else:
            self._force_exit()

    def _force_exit(self):
        """プロセスを完全に終了する"""
        try:
            self.root.destroy()
        except Exception:
            pass
        import os
        os._exit(0)


def main():
    # GUIアプリのディレクトリをPythonパスに追加
    app_dir = os.path.dirname(os.path.abspath(__file__))
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    root = tk.Tk()
    app = BotGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
