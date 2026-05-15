# SetBuilder — DJ Set Builder Pro

A desktop application for building, arranging, and exporting DJ sets. Load audio tracks, adjust their order, tune per-track DSP (EQ, compression, limiting), normalize loudness, and export a finished, sequentially numbered set ready for playback.

---

## Features

- **Multi-format import** — MP3, FLAC, WAV, WMA
- **Per-track DSP chain** — 3-band EQ → Compressor → Limiter (powered by [pedalboard](https://github.com/spotify/pedalboard))
- **LUFS normalization** — target loudness across the entire set
- **BPM-aware sorting** — auto-sort tracks by tempo or reorder manually
- **Animated vinyl visualizer** — shows album art with a spinning record while playing
- **Project persistence** — auto-saves every second to a JSON metadata file
- **Export/Render** — outputs sequentially numbered files with baked-in DSP and optional MP3 compression

---

## Prerequisites

- Python 3.10+
- `ffmpeg` binary (see [Platform notes](#platform-notes) below)
- The Python packages listed in `requirements.txt`

Install Python dependencies:

```bash
pip install -r requirements.txt
```

---

## Running from source

```bash
python run.py
```

---

## Building a standalone binary

### macOS

Requires the macOS `ffmpeg` binary at `assets/ffmpeg` (already present in this repo).

```bash
pip install pyinstaller
pyinstaller SetBuilder.spec
```

The output is `dist/SetBuilder.app`. You can compress it for distribution:

```bash
cd dist && zip -r SetBuilder.zip SetBuilder.app
```

### Windows

> **This must be run on a Windows machine.** PyInstaller builds platform-specific executables — you cannot cross-compile a `.exe` from macOS.

#### Step 1 — Get a Windows ffmpeg binary

Download a static Windows build of ffmpeg from <https://ffmpeg.org/download.html> (look for "Windows builds"). Extract `ffmpeg.exe` and place it at:

```
assets/ffmpeg.exe
```

#### Step 2 — Convert the icon

The macOS spec uses `assets/your_icon.icns`. Windows requires `.ico` format. Convert your icon using any online converter or ImageMagick:

```bash
magick assets/your_icon.icns -resize 256x256 assets/your_icon.ico
```

Or use an online tool and save the result as `assets/your_icon.ico`.

#### Step 3 — Install dependencies and build

Open a terminal (PowerShell or cmd) on Windows and run:

```bash
pip install -r requirements.txt
pyinstaller SetBuilder-win.spec
```

The output is `dist/SetBuilder.exe` — a single self-contained executable.

#### Step 4 — (Optional) Package for distribution

```powershell
Compress-Archive -Path dist\SetBuilder.exe -DestinationPath dist\SetBuilder-Windows.zip
```

---

## Platform notes

| Platform | ffmpeg path | Icon format | Spec file |
|----------|-------------|-------------|-----------|
| macOS | `assets/ffmpeg` | `.icns` | `SetBuilder.spec` |
| Windows | `assets/ffmpeg.exe` | `.ico` | `SetBuilder-win.spec` |

The Python source is fully cross-platform — only the bundled binary and icon differ.

---

## Project structure

```
SetBuilder/
├── run.py                # Entry point — run this to launch the app
│
├── src/
│   ├── main.py           # Main UI class (DJAppUI)
│   ├── AudioCore.py      # Audio engine and DSP processing
│   ├── ProjectManager.py # Project state, metadata, persistence
│   ├── project_actions.py# UI-triggered actions (add, load, save, export)
│   ├── export_dialog.py  # Export/render dialog
│   ├── ui_components.py  # Reusable widgets (Knob, Timeline, buttons)
│   ├── vinyl_animator.py # Spinning vinyl visualization
│   ├── vinyl_renderer.py # Vinyl image generation
│   └── constants.py      # Color palette and sizing constants
│
├── assets/
│   ├── default.png       # Fallback album art
│   ├── ffmpeg            # macOS ffmpeg binary
│   ├── ffmpeg.exe        # Windows ffmpeg binary (add before building on Windows)
│   ├── your_icon.icns    # macOS app icon
│   └── your_icon.ico     # Windows app icon (add before building on Windows)
│
├── SetBuilder.spec       # PyInstaller config — macOS
├── SetBuilder-win.spec   # PyInstaller config — Windows
├── requirements.txt      # Python dependencies
└── download.sh           # Batch download audio via yt-dlp
```
