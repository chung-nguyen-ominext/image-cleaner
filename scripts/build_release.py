"""Build, verify and package ImageCleaner for the OS you are running on.

PyInstaller cannot cross-compile: run this script natively on Linux, Windows
and macOS (or let .github/workflows/build.yml run it on each runner).

Usage:
    python scripts/build_release.py                 # build + verify + package
    python scripts/build_release.py --skip-build
    python scripts/build_release.py --skip-verify
    python scripts/build_release.py --skip-installer   # Windows: portable zip only

Outputs (dist/):
    Linux   ImageCleaner-<version>-linux-x64.tar.gz + .deb
    Windows ImageCleaner-Setup-<version>-x64.exe + ImageCleaner-<version>-windows-x64.zip
    macOS   ImageCleaner-<version>-macos-arm64.dmg (or macos-x64)
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUNDLE = DIST / "ImageCleaner-app"
APP_BUNDLE = DIST / "ImageCleaner.app"
FIXTURE = ROOT / "tests" / "fixtures" / "btn_filled.png"
SPEC = "imagecleaner.spec"
INNO_SCRIPT = ROOT / "packaging" / "windows" / "ImageCleaner.iss"


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def platform_tag() -> str:
    system = platform.system().lower()
    os_name = {"darwin": "macos", "windows": "windows", "linux": "linux"}.get(system)
    if os_name is None:
        raise SystemExit(f"unsupported build platform: {system}")
    machine = platform.machine().lower()
    arch = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, machine)
    return f"{os_name}-{arch}"


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print(">", " ".join(str(part) for part in command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def build() -> None:
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC])


def verify() -> None:
    exe_name = "imagecleaner-selftest.exe" if os.name == "nt" else "imagecleaner-selftest"
    exe = BUNDLE / exe_name
    if not exe.is_file():
        raise SystemExit(f"bundle is missing {exe}; run without --skip-build first")
    # Run from inside the bundle so the check proves the shipped models work
    # without the source tree or a system Python installation.
    run([str(exe), str(FIXTURE)], cwd=BUNDLE)


def verify_macos_app() -> None:
    """Also prove the .app layout (Frameworks/Resources) works, not just onedir."""
    if sys.platform != "darwin":
        return
    exe = APP_BUNDLE / "Contents" / "MacOS" / "imagecleaner-selftest"
    if not exe.is_file():
        raise SystemExit(f"app bundle is missing {exe}")
    # A background-only .app never becomes the active app on macOS and gets no
    # keyboard input, so every text field looks broken. Guard against the
    # regression that BUNDLE picked console=True up from the self-test EXE.
    plist = APP_BUNDLE / "Contents" / "Info.plist"
    info = plistlib.loads(plist.read_bytes())
    if info.get("LSBackgroundOnly"):
        raise SystemExit(f"{plist} sets LSBackgroundOnly; the app would drop all keyboard input")
    run([str(exe), str(FIXTURE)], cwd=exe.parent)


def find_iscc() -> Path | None:
    found = shutil.which("iscc")
    if found:
        return Path(found)
    roots = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    ]
    for root in roots:
        for candidate in sorted(root.glob("Inno Setup */ISCC.exe"), reverse=True):
            if candidate.is_file():
                return candidate
    return None


def macos_dmg(version: str, tag: str) -> Path:
    if not APP_BUNDLE.is_dir():
        raise SystemExit(f"missing app bundle: {APP_BUNDLE}")
    dmg = DIST / f"ImageCleaner-{version}-{tag}.dmg"
    run([
        "hdiutil", "create",
        "-volname", "ImageCleaner",
        "-srcfolder", str(APP_BUNDLE),
        "-ov", "-format", "UDZO",
        str(dmg),
    ])
    return dmg


def windows_package(version: str, tag: str, *, installer: bool) -> list[Path]:
    base = DIST / f"ImageCleaner-{version}-{tag}"
    portable = Path(
        shutil.make_archive(str(base), "zip", root_dir=DIST, base_dir=BUNDLE.name)
    )
    outputs = [portable]
    if not installer:
        return outputs

    iscc = find_iscc()
    if iscc is None:
        raise SystemExit(
            "Inno Setup (ISCC.exe) not found. Install it from "
            "https://jrsoftware.org/isinfo.php or pass --skip-installer."
        )
    run([str(iscc), f"/DAppVersion={version}", str(INNO_SCRIPT)])
    setup = DIST / f"ImageCleaner-Setup-{version}-x64.exe"
    if not setup.is_file():
        raise SystemExit(f"Inno Setup did not produce {setup}")
    outputs.append(setup)
    return outputs


def linux_deb(version: str, tag: str) -> Path | None:
    if shutil.which("dpkg-deb") is None:
        print("WARNING: dpkg-deb not found; skipping the .deb package")
        return None

    root = ROOT / "build" / "deb"
    shutil.rmtree(root, ignore_errors=True)
    app_dir = root / "opt" / "imagecleaner"
    shutil.copytree(BUNDLE, app_dir)

    bin_dir = root / "usr" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "imagecleaner").symlink_to("/opt/imagecleaner/ImageCleaner")

    icon_dir = root / "usr" / "share" / "icons" / "hicolor" / "512x512" / "apps"
    icon_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "packaging" / "icon.png", icon_dir / "imagecleaner.png")

    desktop_dir = root / "usr" / "share" / "applications"
    desktop_dir.mkdir(parents=True)
    (desktop_dir / "imagecleaner.desktop").write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=ImageCleaner\n"
        "Comment=Offline image cleaner\n"
        "Exec=/opt/imagecleaner/ImageCleaner\n"
        "Icon=imagecleaner\n"
        "Terminal=false\n"
        "Categories=Graphics;Utility;\n",
        encoding="utf-8",
    )

    arch = "amd64" if tag.endswith("x64") else "arm64"
    installed_kb = sum(p.stat().st_size for p in app_dir.rglob("*") if p.is_file()) // 1024
    control_dir = root / "DEBIAN"
    control_dir.mkdir()
    (control_dir / "control").write_text(
        f"Package: imagecleaner\n"
        f"Version: {version}\n"
        f"Section: graphics\n"
        f"Priority: optional\n"
        f"Architecture: {arch}\n"
        f"Installed-Size: {installed_kb}\n"
        f"Maintainer: ImageCleaner <noreply@imagecleaner.local>\n"
        f"Depends: libc6, libgl1, libegl1, libxkbcommon-x11-0, libdbus-1-3, libxcb-cursor0\n"
        f"Description: Offline image cleaner\n"
        f" Find and remove user-defined text/UI elements from screenshots\n"
        f" using offline OCR and inpainting.\n",
        encoding="utf-8",
    )

    deb = DIST / f"ImageCleaner-{version}-{tag}.deb"
    run(["dpkg-deb", "--build", "--root-owner-group", str(root), str(deb)])
    return deb


def linux_package(version: str, tag: str) -> list[Path]:
    tarball = DIST / f"ImageCleaner-{version}-{tag}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(BUNDLE, arcname=BUNDLE.name)
    outputs = [tarball]
    deb = linux_deb(version, tag)
    if deb is not None:
        outputs.append(deb)
    return outputs


def package(version: str, tag: str, *, installer: bool) -> list[Path]:
    if sys.platform == "darwin":
        return [macos_dmg(version, tag)]
    if os.name == "nt":
        return windows_package(version, tag, installer=installer)
    return linux_package(version, tag)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and package ImageCleaner")
    parser.add_argument("--skip-build", action="store_true", help="reuse dist/ImageCleaner-app")
    parser.add_argument("--skip-verify", action="store_true", help="do not run the bundled selftest")
    parser.add_argument(
        "--skip-installer",
        action="store_true",
        help="Windows only: create the portable zip without the Inno Setup installer",
    )
    args = parser.parse_args(argv)

    if not args.skip_build:
        build()
    if not BUNDLE.is_dir():
        raise SystemExit(f"missing bundle: {BUNDLE}")
    if not args.skip_verify:
        verify()
        verify_macos_app()

    version = project_version()
    outputs = package(version, platform_tag(), installer=not args.skip_installer)
    print("")
    for path in outputs:
        print(f"OK: {path} ({path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
