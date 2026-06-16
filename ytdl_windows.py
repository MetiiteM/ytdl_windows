#!/usr/bin/env python3
"""
YTDL Windows Edition
A clean local desktop downloader for YouTube, Instagram, SoundCloud and TikTok.

Run:
    python ytdl_windows.py

Build EXE:
    pip install -r requirements.txt
    pyinstaller --onefile --noconsole --name YTDL-Windows ytdl_windows.py
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import traceback
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "YTDL Windows"
APP_VERSION = "1.0.0"
CONFIG_DIR = Path.home() / "AppData" / "Roaming" / "YTDL-Windows"
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_PATH = CONFIG_DIR / "history.jsonl"

SUPPORTED_SOURCES = "YouTube, Instagram, SoundCloud, TikTok"
VIDEO_QUALITIES = ["360", "480", "720", "1080", "best"]
AUDIO_QUALITIES = ["128", "192", "320"]
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "fbclid", "gclid", "si", "feature", "ref", "context", "igshid",
}


def lazy_yt_dlp():
    try:
        import yt_dlp  # type: ignore
        return yt_dlp
    except Exception as exc:
        raise RuntimeError(
            "yt-dlp is not installed. Run: pip install -r requirements.txt"
        ) from exc


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def fmt_size(num_bytes: Optional[float]) -> str:
    if not num_bytes:
        return "Unknown"
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def fmt_duration(seconds: Optional[float]) -> str:
    try:
        seconds_i = int(seconds or 0)
    except Exception:
        seconds_i = 0
    if seconds_i <= 0:
        return "Unknown"
    h, rem = divmod(seconds_i, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", str(name or "file"))
    name = re.sub(r"\s+", " ", name).strip(" ._")
    return (name[:max_len].strip(" ._") or "file")


def extract_first_url(text: str) -> str:
    match = re.search(r"https?://[^\s<>()\[\]{}\"']+", text or "")
    return match.group(0).strip() if match else text.strip()


def normalize_source_url(url: str) -> str:
    url = extract_first_url(url)
    try:
        parsed = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        cleaned_query = [(k, v) for k, v in query if k.lower() not in TRACKING_PARAMS]
        return urllib.parse.urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(cleaned_query, doseq=True),
            parsed.fragment,
        ))
    except Exception:
        return url


def source_name(info: Dict[str, Any], url: str = "") -> str:
    value = " ".join(str(x or "") for x in (
        info.get("extractor_key"), info.get("extractor"), info.get("webpage_url"), url
    )).lower()
    if "soundcloud" in value:
        return "SoundCloud"
    if "instagram" in value:
        return "Instagram"
    if "tiktok" in value:
        return "TikTok"
    if "youtube" in value or "youtu.be" in value:
        return "YouTube"
    return "Other"


def best_thumbnail(info: Dict[str, Any]) -> Optional[str]:
    if info.get("thumbnail"):
        return str(info["thumbnail"])
    thumbs = info.get("thumbnails") or []
    if not isinstance(thumbs, list):
        return None
    candidates = [t for t in thumbs if isinstance(t, dict) and t.get("url")]
    candidates.sort(key=lambda t: int(t.get("width") or 0) * int(t.get("height") or 0), reverse=True)
    return str(candidates[0]["url"]) if candidates else None


def estimate_audio_size(duration: Optional[int], kbps: int) -> Optional[int]:
    try:
        seconds = int(duration or 0)
    except Exception:
        seconds = 0
    if seconds <= 0:
        return None
    return int(seconds * kbps * 1000 / 8 * 1.03)


def format_size_value(fmt: Dict[str, Any]) -> Tuple[Optional[int], bool]:
    if fmt.get("filesize"):
        try:
            return int(fmt["filesize"]), False
        except Exception:
            pass
    if fmt.get("filesize_approx"):
        try:
            return int(fmt["filesize_approx"]), True
        except Exception:
            pass
    return None, True


def bitrate_estimate(fmt: Dict[str, Any], duration: Optional[int]) -> Optional[int]:
    try:
        dur = int(duration or 0)
        tbr = float(fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr") or 0)
    except Exception:
        return None
    if dur <= 0 or tbr <= 0:
        return None
    return int(dur * tbr * 1000 / 8)


def best_audio_format(formats: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates = [f for f in formats if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    if not candidates:
        candidates = [f for f in formats if f.get("acodec") not in (None, "none")]
    if not candidates:
        return None
    return sorted(candidates, key=lambda f: (
        1 if f.get("ext") in ("m4a", "mp4") else 0,
        float(f.get("abr") or f.get("tbr") or 0),
        format_size_value(f)[0] or 0,
    ), reverse=True)[0]


def best_video_for_height(formats: List[Dict[str, Any]], height: Optional[int]) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for f in formats:
        if f.get("vcodec") in (None, "none"):
            continue
        try:
            h = int(f.get("height") or 0)
        except Exception:
            h = 0
        if h <= 0:
            continue
        if height is not None and h > height:
            continue
        candidates.append(f)
    if not candidates:
        return None
    return sorted(candidates, key=lambda f: (
        int(f.get("height") or 0),
        1 if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none") else 0,
        1 if f.get("ext") == "mp4" else 0,
        float(f.get("tbr") or 0),
    ), reverse=True)[0]


def estimate_video_size(info: Dict[str, Any], quality: str) -> Optional[int]:
    formats = info.get("formats") or []
    if not isinstance(formats, list):
        return None
    duration = info.get("duration")
    height = None if quality == "best" else int(quality)
    video = best_video_for_height(formats, height)
    audio = best_audio_format(formats)
    if not video:
        return None

    total = 0
    found = False
    video_size, video_approx = format_size_value(video)
    video_bitrate = bitrate_estimate(video, duration)
    if video_size and video_bitrate and video_approx:
        video_size = min(video_size, int(video_bitrate * 1.15))
    elif not video_size:
        video_size = video_bitrate
    if video_size:
        total += video_size
        found = True

    has_audio = video.get("acodec") not in (None, "none")
    if not has_audio and audio:
        audio_size, audio_approx = format_size_value(audio)
        audio_bitrate = bitrate_estimate(audio, duration)
        if audio_size and audio_bitrate and audio_approx:
            audio_size = min(audio_size, int(audio_bitrate * 1.15))
        elif not audio_size:
            audio_size = audio_bitrate
        if audio_size:
            total += audio_size
            found = True

    return int(total * 1.04) if found else None


@dataclass
class DownloadJob:
    url: str
    mode: str
    quality: str
    output_dir: Path
    proxy: str = ""
    cookie_file: str = ""
    browser_cookies: str = ""
    playlist: bool = False
    embed_metadata: bool = True
    embed_thumbnail: bool = True
    subtitles_lang: str = "en"


class YTDLWindowsApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1060x760")
        self.root.minsize(980, 680)

        ensure_dirs()
        self.config = self.load_config()
        self.info: Optional[Dict[str, Any]] = None
        self.cancel_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.ui_queue: queue.Queue = queue.Queue()

        self.url_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="Video")
        self.quality_var = tk.StringVar(value="720")
        self.output_var = tk.StringVar(value=self.config.get("output_dir") or str(Path.home() / "Downloads"))
        self.proxy_var = tk.StringVar(value=self.config.get("proxy", ""))
        self.cookie_file_var = tk.StringVar(value=self.config.get("cookie_file", ""))
        self.browser_cookie_var = tk.StringVar(value=self.config.get("browser_cookies", ""))
        self.playlist_var = tk.BooleanVar(value=bool(self.config.get("playlist", False)))
        self.embed_metadata_var = tk.BooleanVar(value=bool(self.config.get("embed_metadata", True)))
        self.embed_thumb_var = tk.BooleanVar(value=bool(self.config.get("embed_thumbnail", True)))
        self.clean_url_var = tk.BooleanVar(value=True)
        self.subtitles_lang_var = tk.StringVar(value=self.config.get("subtitles_lang", "en"))

        self.build_ui()
        self.root.after(120, self.process_ui_queue)

    def load_config(self) -> Dict[str, Any]:
        try:
            if CONFIG_PATH.exists():
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def save_config(self) -> None:
        ensure_dirs()
        data = {
            "output_dir": self.output_var.get().strip(),
            "proxy": self.proxy_var.get().strip(),
            "cookie_file": self.cookie_file_var.get().strip(),
            "browser_cookies": self.browser_cookie_var.get().strip(),
            "playlist": bool(self.playlist_var.get()),
            "embed_metadata": bool(self.embed_metadata_var.get()),
            "embed_thumbnail": bool(self.embed_thumb_var.get()),
            "subtitles_lang": self.subtitles_lang_var.get().strip() or "en",
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def build_ui(self) -> None:
        style = ttk.Style()
        with suppress_tcl_error():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.download_tab = ttk.Frame(notebook)
        self.settings_tab = ttk.Frame(notebook)
        self.history_tab = ttk.Frame(notebook)
        notebook.add(self.download_tab, text="Downloader")
        notebook.add(self.settings_tab, text="Settings & Proxy")
        notebook.add(self.history_tab, text="History")

        self.build_download_tab()
        self.build_settings_tab()
        self.build_history_tab()

    def build_download_tab(self) -> None:
        top = ttk.Frame(self.download_tab)
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(top, text="YTDL Windows Edition", style="Title.TLabel").pack(anchor="w")
        ttk.Label(top, text=f"Local desktop downloader for {SUPPORTED_SOURCES}", style="Subtitle.TLabel").pack(anchor="w")

        url_frame = ttk.LabelFrame(self.download_tab, text="Source link")
        url_frame.pack(fill="x", pady=8)
        ttk.Entry(url_frame, textvariable=self.url_var, font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ttk.Button(url_frame, text="Analyze", style="Primary.TButton", command=self.analyze_url).pack(side="left", padx=4)
        ttk.Button(url_frame, text="Paste", command=self.paste_url).pack(side="left", padx=4)
        ttk.Button(url_frame, text="Clear", command=lambda: self.url_var.set("")).pack(side="left", padx=8)

        main = ttk.Frame(self.download_tab)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        info_box = ttk.LabelFrame(left, text="Media info")
        info_box.pack(fill="both", expand=True)
        self.info_text = tk.Text(info_box, height=13, wrap="word", font=("Segoe UI", 10))
        self.info_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.info_text.insert("end", "Paste a link and click Analyze.\n")
        self.info_text.configure(state="disabled")

        options_box = ttk.LabelFrame(left, text="Download options")
        options_box.pack(fill="x", pady=8)
        grid = ttk.Frame(options_box)
        grid.pack(fill="x", padx=8, pady=8)
        ttk.Label(grid, text="Mode").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        mode_combo = ttk.Combobox(grid, textvariable=self.mode_var, state="readonly", values=["Video", "Audio MP3", "Cover PNG", "Subtitles", "Info JSON"])
        mode_combo.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        mode_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_quality_options())
        ttk.Label(grid, text="Quality").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.quality_combo = ttk.Combobox(grid, textvariable=self.quality_var, state="readonly", values=VIDEO_QUALITIES)
        self.quality_combo.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(grid, text="Output folder").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(grid, textvariable=self.output_var).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(grid, text="Browse", command=self.browse_output).grid(row=2, column=2, sticky="ew", padx=4, pady=4)
        grid.columnconfigure(1, weight=1)

        checks = ttk.Frame(options_box)
        checks.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Checkbutton(checks, text="Clean tracking parameters", variable=self.clean_url_var).pack(side="left", padx=4)
        ttk.Checkbutton(checks, text="Playlist mode", variable=self.playlist_var).pack(side="left", padx=4)
        ttk.Checkbutton(checks, text="Embed metadata", variable=self.embed_metadata_var).pack(side="left", padx=4)
        ttk.Checkbutton(checks, text="Embed thumbnail for MP3", variable=self.embed_thumb_var).pack(side="left", padx=4)

        action_frame = ttk.Frame(left)
        action_frame.pack(fill="x", pady=8)
        self.download_button = ttk.Button(action_frame, text="Start Download", style="Primary.TButton", command=self.start_download)
        self.download_button.pack(side="left", padx=4)
        self.cancel_button = ttk.Button(action_frame, text="Cancel", command=self.cancel_download, state="disabled")
        self.cancel_button.pack(side="left", padx=4)
        ttk.Button(action_frame, text="Open Output Folder", command=self.open_output_folder).pack(side="left", padx=4)

        progress_box = ttk.LabelFrame(right, text="Progress")
        progress_box.pack(fill="x")
        self.progress = ttk.Progressbar(progress_box, maximum=100)
        self.progress.pack(fill="x", padx=8, pady=(8, 4))
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(progress_box, textvariable=self.status_var).pack(anchor="w", padx=8, pady=(0, 8))

        log_box = ttk.LabelFrame(right, text="Log")
        log_box.pack(fill="both", expand=True, pady=8)
        self.log_text = tk.Text(log_box, wrap="word", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)

    def build_settings_tab(self) -> None:
        ttk.Label(self.settings_tab, text="Settings & Proxy", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        box = ttk.LabelFrame(self.settings_tab, text="Network")
        box.pack(fill="x", pady=8)
        grid = ttk.Frame(box)
        grid.pack(fill="x", padx=8, pady=8)
        ttk.Label(grid, text="Proxy URL").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(grid, textvariable=self.proxy_var).grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(grid, text="Examples: http://127.0.0.1:8080  |  socks5://127.0.0.1:1080").grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(grid, text="Cookie file").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(grid, textvariable=self.cookie_file_var).grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Button(grid, text="Browse", command=self.browse_cookie_file).grid(row=2, column=2, sticky="ew", padx=4)
        ttk.Label(grid, text="Browser cookies").grid(row=3, column=0, sticky="w", padx=4, pady=4)
        ttk.Combobox(grid, textvariable=self.browser_cookie_var, values=["", "chrome", "edge", "firefox", "brave", "opera"], state="readonly").grid(row=3, column=1, sticky="ew", padx=4, pady=4)
        grid.columnconfigure(1, weight=1)

        sub_box = ttk.LabelFrame(self.settings_tab, text="Subtitles")
        sub_box.pack(fill="x", pady=8)
        row = ttk.Frame(sub_box)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Label(row, text="Subtitle language code").pack(side="left", padx=4)
        ttk.Entry(row, textvariable=self.subtitles_lang_var, width=12).pack(side="left", padx=4)
        ttk.Label(row, text="Examples: en, fa, de, all").pack(side="left", padx=4)

        install_box = ttk.LabelFrame(self.settings_tab, text="Required tools")
        install_box.pack(fill="x", pady=8)
        ttk.Label(install_box, text="Install ffmpeg and keep it in PATH for MP3 conversion, video merging, subtitles and metadata embedding.").pack(anchor="w", padx=8, pady=4)
        ttk.Label(install_box, text="Python mode: pip install -r requirements.txt").pack(anchor="w", padx=8, pady=4)
        ttk.Label(install_box, text="EXE mode: build with build_windows_exe.bat after installing dependencies.").pack(anchor="w", padx=8, pady=4)

        ttk.Button(self.settings_tab, text="Save Settings", style="Primary.TButton", command=self.save_settings_clicked).pack(anchor="w", pady=8)

    def build_history_tab(self) -> None:
        header = ttk.Frame(self.history_tab)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="History", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Refresh", command=self.load_history).pack(side="right", padx=4)
        ttk.Button(header, text="Clear History", command=self.clear_history).pack(side="right", padx=4)
        self.history_text = tk.Text(self.history_tab, wrap="word", font=("Segoe UI", 10))
        self.history_text.pack(fill="both", expand=True)
        self.load_history()

    def paste_url(self) -> None:
        try:
            self.url_var.set(self.root.clipboard_get())
        except Exception:
            pass

    def browse_output(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_var.get() or str(Path.home()))
        if folder:
            self.output_var.set(folder)

    def browse_cookie_file(self) -> None:
        file_path = filedialog.askopenfilename(title="Select cookies.txt", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if file_path:
            self.cookie_file_var.set(file_path)

    def save_settings_clicked(self) -> None:
        self.save_config()
        messagebox.showinfo(APP_NAME, "Settings saved.")

    def open_output_folder(self) -> None:
        folder = Path(self.output_var.get() or Path.home() / "Downloads")
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # Windows-only, intentional for this edition

    def refresh_quality_options(self) -> None:
        mode = self.mode_var.get()
        if mode == "Video":
            self.quality_combo.configure(values=VIDEO_QUALITIES)
            if self.quality_var.get() not in VIDEO_QUALITIES:
                self.quality_var.set("720")
        elif mode == "Audio MP3":
            self.quality_combo.configure(values=AUDIO_QUALITIES)
            if self.quality_var.get() not in AUDIO_QUALITIES:
                self.quality_var.set("320")
        else:
            self.quality_combo.configure(values=["best"])
            self.quality_var.set("best")

    def ydl_common_opts(self, quiet: bool = True) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": quiet,
            "no_warnings": quiet,
            "noplaylist": not bool(self.playlist_var.get()),
            "socket_timeout": 30,
            "retries": 5,
            "fragment_retries": 5,
            "continuedl": True,
            "ignoreerrors": False,
        }
        proxy = self.proxy_var.get().strip()
        if proxy:
            opts["proxy"] = proxy
        cookie_file = self.cookie_file_var.get().strip()
        if cookie_file and Path(cookie_file).exists():
            opts["cookiefile"] = cookie_file
        browser = self.browser_cookie_var.get().strip()
        if browser:
            opts["cookiesfrombrowser"] = (browser,)
        return opts

    def analyze_url(self) -> None:
        raw = self.url_var.get().strip()
        if not raw:
            messagebox.showwarning(APP_NAME, "Please paste a link first.")
            return
        url = normalize_source_url(raw) if self.clean_url_var.get() else extract_first_url(raw)
        self.url_var.set(url)
        self.set_busy(True, "Analyzing link...")
        self.log(f"Analyzing: {url}")
        thread = threading.Thread(target=self._analyze_worker, args=(url,), daemon=True)
        thread.start()

    def _analyze_worker(self, url: str) -> None:
        try:
            yt_dlp = lazy_yt_dlp()
            opts = self.ydl_common_opts(quiet=True)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            self.info = info
            self.ui_queue.put(("info", info, url))
        except Exception as exc:
            self.ui_queue.put(("error", f"Analyze failed: {exc}"))
        finally:
            self.ui_queue.put(("busy", False, "Ready"))

    def show_info(self, info: Dict[str, Any], url: str) -> None:
        source = source_name(info, url)
        title = info.get("title") or "Unknown"
        uploader = info.get("uploader") or info.get("channel") or info.get("creator") or info.get("artist") or "Unknown"
        duration = fmt_duration(info.get("duration"))
        thumbnail = best_thumbnail(info) or "Unknown"
        lines = [
            f"Source: {source}",
            f"Title: {title}",
            f"Creator: {uploader}",
            f"Duration: {duration}",
            f"Thumbnail: {thumbnail}",
            "",
            "Estimated sizes:",
        ]
        for q in ["360", "480", "720", "1080", "best"]:
            est = estimate_video_size(info, q)
            lines.append(f"  Video {q}p: {fmt_size(est)}" if q != "best" else f"  Video best: {fmt_size(est)}")
        for q in AUDIO_QUALITIES:
            lines.append(f"  MP3 {q} kbps: {fmt_size(estimate_audio_size(info.get('duration'), int(q)))}")
        self.set_info_text("\n".join(lines))

    def set_info_text(self, text: str) -> None:
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("end", text)
        self.info_text.configure(state="disabled")

    def start_download(self) -> None:
        raw = self.url_var.get().strip()
        if not raw:
            messagebox.showwarning(APP_NAME, "Please paste a link first.")
            return
        url = normalize_source_url(raw) if self.clean_url_var.get() else extract_first_url(raw)
        self.url_var.set(url)
        output_dir = Path(self.output_var.get().strip() or Path.home() / "Downloads")
        output_dir.mkdir(parents=True, exist_ok=True)
        self.save_config()
        self.cancel_event.clear()
        job = DownloadJob(
            url=url,
            mode=self.mode_var.get(),
            quality=self.quality_var.get(),
            output_dir=output_dir,
            proxy=self.proxy_var.get().strip(),
            cookie_file=self.cookie_file_var.get().strip(),
            browser_cookies=self.browser_cookie_var.get().strip(),
            playlist=bool(self.playlist_var.get()),
            embed_metadata=bool(self.embed_metadata_var.get()),
            embed_thumbnail=bool(self.embed_thumb_var.get()),
            subtitles_lang=self.subtitles_lang_var.get().strip() or "en",
        )
        self.set_busy(True, "Starting download...")
        self.progress.configure(value=0)
        self.worker = threading.Thread(target=self._download_worker, args=(job,), daemon=True)
        self.worker.start()

    def cancel_download(self) -> None:
        self.cancel_event.set()
        self.status_var.set("Cancel requested...")
        self.log("Cancel requested.")

    def _download_worker(self, job: DownloadJob) -> None:
        try:
            yt_dlp = lazy_yt_dlp()
            opts = self.ydl_common_opts(quiet=False)
            opts["outtmpl"] = str(job.output_dir / "%(title).160B - %(id)s.%(ext)s")
            opts["progress_hooks"] = [self.progress_hook]
            opts["prefer_ffmpeg"] = True
            opts["windowsfilenames"] = True
            opts["restrictfilenames"] = False

            if job.mode == "Audio MP3":
                opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
                opts["postprocessors"] = [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": job.quality},
                ]
                if job.embed_metadata:
                    opts["postprocessors"].append({"key": "FFmpegMetadata"})
                if job.embed_thumbnail:
                    opts["writethumbnail"] = True
                    opts["postprocessors"].append({"key": "EmbedThumbnail"})
            elif job.mode == "Video":
                opts["merge_output_format"] = "mp4"
                opts["format"] = self.video_format_selector(job.quality)
                opts["postprocessors"] = [{"key": "FFmpegMetadata"}] if job.embed_metadata else []
            elif job.mode == "Cover PNG":
                opts["skip_download"] = True
                opts["writethumbnail"] = True
                opts["convert_thumbnails"] = "png"
                opts["postprocessors"] = [{"key": "FFmpegThumbnailsConvertor", "format": "png", "when": "before_dl"}]
            elif job.mode == "Subtitles":
                opts["skip_download"] = True
                opts["writesubtitles"] = True
                opts["writeautomaticsub"] = True
                opts["subtitleslangs"] = [job.subtitles_lang] if job.subtitles_lang != "all" else ["all"]
                opts["subtitlesformat"] = "srt/best"
            elif job.mode == "Info JSON":
                opts["skip_download"] = True
                opts["writeinfojson"] = True
            else:
                raise RuntimeError(f"Unknown mode: {job.mode}")

            self.ui_queue.put(("log", f"Download started: {job.mode} / {job.quality}"))
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(job.url, download=True)
            self.add_history(job, info)
            self.ui_queue.put(("done", f"Done: {info.get('title') or job.url}"))
        except Exception as exc:
            if self.cancel_event.is_set():
                self.ui_queue.put(("error", "Download cancelled."))
            else:
                self.ui_queue.put(("error", f"Download failed: {exc}\n{traceback.format_exc(limit=2)}"))
        finally:
            self.ui_queue.put(("busy", False, "Ready"))

    def video_format_selector(self, quality: str) -> str:
        if quality == "best":
            return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
        try:
            h = int(quality)
        except Exception:
            h = 720
        return (
            f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={h}]+bestaudio/"
            f"best[height<={h}][ext=mp4]/best[height<={h}]/best"
        )

    def progress_hook(self, data: Dict[str, Any]) -> None:
        if self.cancel_event.is_set():
            raise RuntimeError("Cancelled by user")
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes") or 0
            percent = float(downloaded) / float(total) * 100 if total else 0
            speed = data.get("speed")
            eta = data.get("eta")
            msg = f"Downloading: {percent:.1f}% | {fmt_size(downloaded)} / {fmt_size(total)}"
            if speed:
                msg += f" | {fmt_size(speed)}/s"
            if eta is not None:
                msg += f" | ETA {fmt_duration(eta)}"
            self.ui_queue.put(("progress", percent, msg))
        elif status == "finished":
            self.ui_queue.put(("progress", 100, "Processing media..."))

    def set_busy(self, busy: bool, status: str) -> None:
        self.download_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.status_var.set(status)

    def process_ui_queue(self) -> None:
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "info":
                    self.show_info(item[1], item[2])
                    self.log("Analyze completed.")
                elif kind == "error":
                    self.log(item[1])
                    self.status_var.set("Error")
                    messagebox.showerror(APP_NAME, item[1][:1200])
                elif kind == "done":
                    self.log(item[1])
                    self.status_var.set("Done")
                    self.load_history()
                    messagebox.showinfo(APP_NAME, item[1][:500])
                elif kind == "progress":
                    self.progress.configure(value=max(0, min(100, float(item[1]))))
                    self.status_var.set(item[2])
                elif kind == "busy":
                    self.set_busy(bool(item[1]), str(item[2]))
                elif kind == "log":
                    self.log(item[1])
        except queue.Empty:
            pass
        self.root.after(120, self.process_ui_queue)

    def log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] {text}\n")
        self.log_text.see("end")

    def add_history(self, job: DownloadJob, info: Dict[str, Any]) -> None:
        ensure_dirs()
        record = {
            "time": int(time.time()),
            "url": job.url,
            "mode": job.mode,
            "quality": job.quality,
            "title": info.get("title"),
            "source": source_name(info, job.url),
            "output_dir": str(job.output_dir),
        }
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_history(self) -> List[Dict[str, Any]]:
        if not HISTORY_PATH.exists():
            return []
        rows = []
        for line in HISTORY_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        return rows[-100:][::-1]

    def load_history(self) -> None:
        rows = self.read_history()
        self.history_text.configure(state="normal")
        self.history_text.delete("1.0", "end")
        if not rows:
            self.history_text.insert("end", "No history yet.\n")
        for i, row in enumerate(rows[:50], 1):
            t = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(row.get("time") or 0)))
            self.history_text.insert("end", f"{i}. {row.get('mode')} / {row.get('quality')} · {row.get('source')}\n")
            self.history_text.insert("end", f"   {row.get('title') or 'Unknown title'}\n")
            self.history_text.insert("end", f"   {t} · {row.get('url')}\n\n")
        self.history_text.configure(state="disabled")

    def clear_history(self) -> None:
        if messagebox.askyesno(APP_NAME, "Clear local download history?"):
            with open(HISTORY_PATH, "w", encoding="utf-8") as fh:
                fh.write("")
            self.load_history()


class suppress_tcl_error:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return exc_type is tk.TclError


def main() -> None:
    ensure_dirs()
    root = tk.Tk()
    app = YTDLWindowsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
