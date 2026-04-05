# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['..\\omni_assistant.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'PyQt6', 'PyQt6.Qt6', 'PyQt6.sip', 'playwright', 'pygame', 'scipy', 'pandas', 'matplotlib', 'traitlets', 'jinja2', 'markupsafe', 'paddlex', 'tensorboard', 'deepface', 'icrawler', 'pdf2image', 'pystray', 'reportlab', 'retina-face'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='python',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['c:\\Users\\Abubakr\\Desktop\\MainCode\\python_icon.ico'],
)
