# PyInstaller spec para TitlePilot Pro
# Gera um exe standalone com tudo incluído

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None
SRC = r"C:\Users\Guilherme\Desktop\VideoAutomation\titlepilot_pro"

# Coleta dados necessários
datas = [
    (os.path.join(SRC, "templates"), "templates"),
    (os.path.join(SRC, "static"), "static"),
    (os.path.join(SRC, "core"), "core"),
]

# Coleta dados dos pacotes Python
for pkg in ["groq", "flask", "werkzeug", "jinja2", "google"]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
    except Exception:
        pass

hiddenimports = [
    "groq",
    "flask",
    "flask_cors",
    "werkzeug",
    "werkzeug.serving",
    "jinja2",
    "requests",
    "google.generativeai",
    "google.ai.generativelanguage",
    "google.auth",
    "grpc",
    "proto",
    "core.api_keys",
    "core.youtube_api",
    "googleapiclient",
    "googleapiclient.discovery",
    "urllib3",
    "charset_normalizer",
    "certifi",
    "idna",
    "httpx",
    "httpcore",
    "anyio",
    "sniffio",
]

a = Analysis(
    [os.path.join(SRC, "launcher.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL", "cv2"],
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
    name="TitlePilot Pro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # Mostra console com status do servidor
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
