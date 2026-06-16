# YTDL Windows Edition

A local Windows desktop downloader for YouTube, Instagram, SoundCloud and TikTok.

## Features

- Analyze links before download
- Download video in 360p, 480p, 720p, 1080p or best quality
- Download MP3 audio in 128, 192 or 320 kbps
- Download best available cover/thumbnail as PNG
- Download subtitles / auto-subtitles when available
- Export info JSON
- Clean tracking parameters from shared links
- Local history
- Proxy support: HTTP, HTTPS, SOCKS4, SOCKS5
- Cookie support: cookies.txt or browser cookies
- Playlist mode switch
- Cancel running download
- Clean Windows-safe filenames

## Requirements

1. Windows 10/11
2. Python 3.10+
3. FFmpeg installed and available in PATH

Install Python dependencies:

```bat
pip install -r requirements.txt
```

Run:

```bat
python ytdl_windows.py
```

Or use:

```bat
run_windows.bat
```

## Build EXE

```bat
build_windows_exe.bat
```

The EXE will be created here:

```text
dist\YTDL-Windows.exe
```

## Proxy examples

```text
http://127.0.0.1:8080
https://user:pass@host:port
socks5://127.0.0.1:1080
```

For SOCKS proxies, install yt-dlp with SOCKS support if needed:

```bat
pip install "yt-dlp[default]"
```

## Cookies

You can use either:

- `cookies.txt` exported from your browser
- Browser cookies directly: chrome, edge, firefox, brave, opera

For restricted YouTube/TikTok/Instagram links, cookies may be required.

## FFmpeg

FFmpeg is required for:

- MP3 conversion
- video/audio merging
- thumbnail embedding
- subtitle conversion

Install FFmpeg and add it to PATH, or place `ffmpeg.exe` next to the EXE.
