# -*- mode: python ; coding: utf-8 -*-

# This is a PyInstaller spec file. It's a Python script that tells PyInstaller
# how to build your application.

import os as _os
block_cipher = None

_datas = [('assets/default.png', 'assets')]
if _os.path.exists('assets/ffmpeg'):
    _datas.append(('assets/ffmpeg', '.'))

# Analysis: This is where PyInstaller finds all your code and dependencies.
a = Analysis(['src/main.py'],
             pathex=['src'],
             binaries=[],
             # Add all your data files here.
             # The first part is the source file in your project.
             # The second part is the destination folder inside the .app bundle.
             datas=_datas,
             # Sometimes PyInstaller misses imports for complex libraries.
             # We can tell it about them here.
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
                 'tkinterdnd2',
                 'soundfile_build' # Often needed for soundfile
             ],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)

pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)

# EXE: This bundles the processed files into a single executable.
exe = EXE(pyz,
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
          console=True, # Set to False for a GUI app
          icon='assets/your_icon.icns') # Specify your icon file here

# APP: This creates the final .app bundle for macOS.
app = BUNDLE(exe,
          name='SetBuilder.app',
          icon='assets/your_icon.icns', # Also specify icon here for the bundle
          bundle_identifier='com.yourname.setbuilder') # Optional: a unique ID

