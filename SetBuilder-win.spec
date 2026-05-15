# -*- mode: python ; coding: utf-8 -*-
# Windows build spec. Run on a Windows machine with:
#   pyinstaller SetBuilder-win.spec

block_cipher = None

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('assets/default.png', 'assets'),
        # Place the Windows ffmpeg binary at assets/ffmpeg.exe before building.
        ('assets/ffmpeg.exe', '.'),
    ],
    hiddenimports=[
        'sounddevice',
        'soundfile',
        'pedalboard',
        'mutagen',
        'PIL',
        'librosa',
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.simpledialog',
        'soundfile_build',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SetBuilder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No terminal window for GUI app
    icon='assets/your_icon.ico',  # Must be .ico format, not .icns
)
