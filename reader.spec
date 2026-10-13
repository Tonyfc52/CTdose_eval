# -*- mode: python ; coding: utf-8 -*-
# PyInstaller Spec 檔案：專為 CT Dose 輻射劑量自動化評估系統設計
# 適用於 Windows 平台打包為單一執行檔 (CTDose.exe)

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['reader.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('metadata.json', '.'),  # 將預設設定檔一併打包進來 (若存在)
    ],
    hiddenimports=[
        'pydicom',
        'pydicom.encoders',
        'pydicom.encoders.native',
        'pandas',
        'matplotlib',
        'matplotlib.backends.backend_tkagg',
        'sqlite3',
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.scrolledtext',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['IPython', 'notebook', 'nbconvert', 'jupyter'],
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
    name='CTDose',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # False: 啟動時不顯示黑色命令列視窗 (純 GUI 模式)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='app.ico',  # 若有圖示可在此指定
)
