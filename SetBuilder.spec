# -*- mode: python ; coding: utf-8 -*-

import os as _os

block_cipher = None

_datas = [('assets/default.png', 'assets')]
if _os.path.exists('assets/ffmpeg'):
    _datas.append(('assets/ffmpeg', '.'))
if _os.path.exists('clear_app.sh'):
    _datas.append(('clear_app.sh', '.'))

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'sounddevice', 'soundfile', 'pedalboard', 'mutagen',
        'PIL', 'librosa', 'tkinter', 'tkinter.filedialog',
        'tkinter.messagebox', 'tkinter.simpledialog', 'tkinterdnd2',
        'soundfile_build', 'pyloudnorm',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'torch', 'torchaudio', 'torchvision',
        'audio_separator',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SetBuilder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='assets/your_icon.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SetBuilder',
)

app = BUNDLE(
    coll,
    name='SetBuilder.app',
    icon='assets/your_icon.icns',
    bundle_identifier='com.yourname.setbuilder',
)
