# -*- mode: python ; coding: utf-8 -*-

import os as _os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

_as_datas, _as_bins, _as_hidden = collect_all('audio_separator')
_torch_datas, _torch_bins, _torch_hidden = collect_all('torch')

_datas = _as_datas + _torch_datas

a = Analysis(
    ['src/denoiser_worker.py'],
    pathex=['src'],
    binaries=_as_bins + _torch_bins,
    datas=_datas,
    hiddenimports=[
        'audio_separator',
        'audio_separator.separator',
        'audio_separator.separator.separator',
        'audio_separator.separator.common_separator',
        'audio_separator.separator.architectures.vr_separator',
        'audio_separator.separator.architectures.mdxc_separator',
        'tqdm',
        'torch', 'torch.nn', 'torch.nn.functional', 'torch.backends.mps',
    ] + _as_hidden + _torch_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter', 'PIL', 'sounddevice',
        'pedalboard', 'mutagen', 'tkinterdnd2',
        'torch.distributed', 'torch.testing', 'torch.utils.tensorboard',
        'torch.ao', 'torch.onnx', 'torchaudio', 'torchvision',
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
    name='SetBuilderDenoiser',
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
    name='SetBuilderDenoiser',
)

app = BUNDLE(
    coll,
    name='SetBuilderDenoiser.app',
    icon='assets/your_icon.icns',
    bundle_identifier='com.yourname.setbuilder.denoiser',
)
