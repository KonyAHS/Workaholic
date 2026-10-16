"""Generate Windows metadata and package explicitly selected release files."""
import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import __version__


def version_info():
    if not re.fullmatch(r"\d+\.\d+\.\d+", __version__):
        raise ValueError("version.py must contain a numeric major.minor.patch version")
    numbers = tuple(map(int, __version__.split("."))) + (0,)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f,
                   flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('FileDescription', 'Workaholic'),
    StringStruct('FileVersion', '{__version__}'),
    StringStruct('ProductName', 'Workaholic'),
    StringStruct('ProductVersion', '{__version__}'),
    StringStruct('OriginalFilename', 'Workaholic.exe')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "build/version-info.txt").write_text(text, encoding="utf-8")


def package():
    out = ROOT / "dist/release"
    out.mkdir(parents=True, exist_ok=True)
    exe = out / "Workaholic.exe"
    shutil.copyfile(ROOT / "dist/Workaholic.exe", exe)
    portable = out / f"Workaholic-{__version__}-windows-x64.zip"
    with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe, "Workaholic.exe")
        archive.write(ROOT / "docs/portable-readme.txt", "README.txt")
        archive.write(ROOT / "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.md")
        for license_file in sorted((ROOT / "licenses").glob("*.txt")):
            archive.write(license_file, f"licenses/{license_file.name}")
    source = out / f"Workaholic-{__version__}-source.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--prefix=Workaholic-{__version__}/",
                    "-o", str(source), "HEAD"], cwd=ROOT, check=True)
    assets = [exe, portable, source]
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in assets)
    (out / "SHA256SUMS.txt").write_text(sums, encoding="ascii")
    for asset in assets:
        print(asset)


if __name__ == "__main__":
    {"version-info": version_info, "package": package}[sys.argv[1]]()
