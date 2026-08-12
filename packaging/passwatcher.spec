from pathlib import Path
import sys

from PyInstaller.utils.hooks import copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


PROJECT_ROOT = Path(SPECPATH).parent
SOURCE_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from passwatcher import __version__


def version_tuple(value):
    parts = tuple(int(part) for part in value.split("."))
    if len(parts) > 4:
        raise ValueError("Passwatcher version must contain at most four numeric parts")
    return parts + (0,) * (4 - len(parts))


numeric_version = version_tuple(__version__)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=numeric_version,
        prodvers=numeric_version,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Passwatcher"),
                        StringStruct("FileDescription", "Passwatcher CLI"),
                        StringStruct("FileVersion", __version__),
                        StringStruct("InternalName", "pw"),
                        StringStruct("OriginalFilename", "pw.exe"),
                        StringStruct("ProductName", "Passwatcher"),
                        StringStruct("ProductVersion", __version__),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

datas = [
    (
        str(SOURCE_ROOT / "passwatcher" / "assets" / "passwatcher-server.pyz"),
        "passwatcher/assets",
    ),
]
datas += copy_metadata("typer")
datas += copy_metadata("rich")

a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "passwatcher_entry.py")],
    pathex=[str(SOURCE_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pw",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="passwatcher",
)
