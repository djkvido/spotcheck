from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
VERSIONS = json.loads((PACKAGING / "versions.json").read_text(encoding="utf-8"))
REPOSITORY = "https://github.com/djkvido/spotcheck"
LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.I)
LICENSE_OVERRIDES = {
    "colorama": "BSD-3-Clause",
    "jinja2": "BSD-3-Clause",
    "pycryptodomex": "BSD-2-Clause and public domain",
    "python-dateutil": "Apache-2.0 AND BSD-3-Clause",
    "spotapi": "GPL-3.0",
    "spotipyfree": "MIT",
    "text-unidecode": "Artistic-1.0-Perl OR GPL-2.0-or-later",
    "unidecode": "GPL-2.0-or-later",
}
MACHO_MAGIC = {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}


def app_version() -> str:
    text = (ROOT / "spotcheck" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def run(cmd: list, **kwargs) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def download(url: str, sha256: str, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{sha256[:12]}-{Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name}"
    if not target.exists():
        partial = target.with_name(target.name + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": "SpotCheck-build"})
        with urllib.request.urlopen(request, timeout=120) as response, open(partial, "wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != sha256:
        target.unlink()
        raise SystemExit(f"Checksum mismatch for {url}: {digest}")
    return target


def python_executable(runtime: Path, target: str) -> Path:
    return runtime / "python.exe" if target == "windows" else runtime / "bin" / "python3"


def build_runtime(target: str, stage: Path, cache: Path) -> Path:
    spec = VERSIONS["python"][target]
    archive = download(spec["url"], spec["sha256"], cache)
    with tarfile.open(archive) as tar:
        tar.extractall(stage, filter="data")
    runtime = stage / "runtime"
    (stage / "python").rename(runtime)
    python = python_executable(runtime, target)
    pip = [python, "-m", "pip", "install", "--no-deps", "--disable-pip-version-check", "--no-warn-script-location"]
    run([*pip, "--require-hashes", "--only-binary", ":all:", "-r", PACKAGING / f"requirements-{target}.lock"])
    spotdl = VERSIONS["spotdl"]
    requirement = stage / "spotdl.txt"
    requirement.write_text(f"spotdl=={spotdl['version']} --hash=sha256:{spotdl['sha256']}\n", encoding="utf-8")
    run([*pip, "--require-hashes", "--only-binary", ":all:", "--target", runtime / "spotdl", "-r", requirement])
    requirement.unlink()
    wheels = stage / "wheel"
    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", wheels, ROOT])
    run([*pip, "--no-index", next(wheels.glob("spotcheck-*.whl"))])
    shutil.rmtree(wheels)
    return python


def add_tools(target: str, stage: Path, ffmpeg: Path, cache: Path) -> None:
    tools = stage / "tools"
    tools.mkdir()
    suffix = ".exe" if target == "windows" else ""
    for name in ("ffmpeg", "ffprobe"):
        shutil.copy2(ffmpeg / f"{name}{suffix}", tools / f"{name}{suffix}")
    deno = VERSIONS["deno"][target]
    with zipfile.ZipFile(download(deno["url"], deno["sha256"], cache)) as archive:
        archive.extract(f"deno{suffix}", tools)
    for tool in tools.iterdir():
        tool.chmod(0o755)


def package_licenses(site_packages: list[Path]) -> list[tuple[str, str, str, Path]]:
    found = []
    for folder in site_packages:
        for info in sorted(folder.glob("*.dist-info")):
            meta = email.parser.HeaderParser().parsestr((info / "METADATA").read_text(encoding="utf-8"))
            licensed = [c for c in meta.get_all("Classifier") or [] if c.startswith("License")]
            classifiers = [c.split("::")[-1].strip() for c in licensed]
            license_text = (meta.get("License") or "").strip().splitlines()
            name = meta.get("License-Expression") or (license_text[0] if license_text else "") or ", ".join(classifiers)
            name = LICENSE_OVERRIDES.get(meta["Name"].lower(), name.replace("MIT License", "MIT"))
            found.append((meta["Name"], meta["Version"], name or "see license files", info))
    return found


def add_licenses(target: str, stage: Path, ffmpeg: Path, cache: Path) -> None:
    licenses = stage / "licenses"
    licenses.mkdir()
    shutil.copy2(ROOT / "LICENSE", licenses / "SpotCheck-GPL-3.0.txt")
    shutil.copytree(ffmpeg / "licenses", licenses / "ffmpeg")
    deno = VERSIONS["deno"]["license"]
    shutil.copy2(download(deno["url"], deno["sha256"], cache), licenses / "deno-LICENSE.md")
    runtime = stage / "runtime"
    python_license = next(runtime.rglob("LICENSE.txt"))
    shutil.copy2(python_license, licenses / "python-LICENSE.txt")
    site = [next(runtime.rglob("site-packages")), runtime / "spotdl"]
    packages = package_licenses(site)
    for name, version, _, info in packages:
        files = [p for p in info.rglob("*") if p.is_file() and LICENSE_FILE.match(p.name)]
        if files:
            destination = licenses / "python" / f"{name}-{version}"
            destination.mkdir(parents=True, exist_ok=True)
            for file in files:
                shutil.copy2(file, destination / file.name)
    sources = VERSIONS["ffmpeg_sources"]
    lines = [
        "SpotCheck",
        "=========",
        "",
        "SpotCheck is free software, licensed under the GNU General Public License version 3 or later",
        f"(licenses/SpotCheck-GPL-3.0.txt). Source code: {REPOSITORY}",
        "",
        "This package includes the third-party software listed below. Their license texts are in the",
        "licenses folder. The complete corresponding source code of the FFmpeg build (FFmpeg, LAME,",
        "Opus, Chromaprint) together with the script that builds it is attached to the release this",
        f"package comes from: {REPOSITORY}/releases",
        "",
        "Programs",
        "--------",
        "",
        f"Python {VERSIONS['python']['version']} (python-build-standalone {VERSIONS['python']['release']}), "
        "Python Software Foundation License: licenses/python-LICENSE.txt",
        f"Deno {VERSIONS['deno']['version']}, MIT: licenses/deno-LICENSE.md",
    ]
    lines += [f"{name} {spec['version']}, {spec['license']}: {spec['url']}" for name, spec in sources.items()]
    lines += ["", "Python packages", "---------------", ""]
    lines += [f"{name} {version}, {license_name}" for name, version, license_name, _ in packages]
    (stage / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plain_text(markdown: str) -> str:
    def link(match: re.Match) -> str:
        text, url = match.groups()
        if not url.startswith("http"):
            url = f"{REPOSITORY}/blob/main/{url}"
        return text if url == text else f"{text} ({url})"

    lines = []
    for line in markdown.splitlines():
        heading = re.match(r"^(#{1,2}) (.+)$", line)
        if heading:
            title = heading.group(2)
            lines += [title, ("=" if len(heading.group(1)) == 1 else "-") * len(title)]
            continue
        lines.append(re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, line).replace("`", ""))
    return "\n".join(lines) + "\n"


def add_terms(stage: Path) -> None:
    for source, target in (("TERMS.md", "TERMS.txt"), ("TERMS.cs.md", "TERMS.cs.txt")):
        text = plain_text((ROOT / source).read_text(encoding="utf-8"))
        (stage / target).write_text(text, encoding="utf-8-sig")


def smoke_test(python: Path, stage: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(stage / "runtime" / "spotdl")}
    run([python, "-m", "spotdl", "--version"], env=env)
    with tempfile.TemporaryDirectory() as home:
        run([python, "-I", "-m", "spotcheck", "--packaged", "--home", home, "--setup"])


def package_windows(stage: Path, out: Path, version: str, cache: Path) -> Path:
    runtime = stage / "runtime"
    launcher = runtime / "SpotCheck.exe"
    shutil.copy2(runtime / "pythonw.exe", launcher)
    rcedit = VERSIONS["rcedit"]
    run(
        [
            download(rcedit["url"], rcedit["sha256"], cache),
            launcher,
            "--set-icon",
            ROOT / "spotcheck" / "static" / "spotcheck.ico",
            "--set-version-string",
            "ProductName",
            "SpotCheck",
            "--set-version-string",
            "FileDescription",
            "SpotCheck",
            "--set-version-string",
            "CompanyName",
            "djkvido",
            "--set-version-string",
            "LegalCopyright",
            "Copyright (C) 2026 djkvido. GPL-3.0-or-later.",
            "--set-file-version",
            version,
            "--set-product-version",
            version,
        ]
    )
    iscc = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    run(
        [
            iscc,
            f"/DAppVersion={version}",
            f"/DSourceDir={stage}",
            f"/DOutputDir={out}",
            f"/DRepoDir={ROOT}",
            PACKAGING / "windows" / "SpotCheck.iss",
        ]
    )
    return out / f"SpotCheck-{version}-windows-x64-setup.exe"


def is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with open(path, "rb") as f:
        return f.read(4) in MACHO_MAGIC


def package_macos(stage: Path, out: Path, version: str) -> Path:
    app = stage / "SpotCheck.app"
    contents = app / "Contents"
    resources = contents / "Resources"
    (contents / "MacOS").mkdir(parents=True)
    resources.mkdir()
    for name in ("runtime", "tools", "licenses", "THIRD_PARTY_NOTICES.txt", "TERMS.txt", "TERMS.cs.txt"):
        shutil.move(str(stage / name), str(resources / name))
    launcher = contents / "MacOS" / "SpotCheck"
    shutil.copy2(PACKAGING / "macos" / "launcher.sh", launcher)
    launcher.chmod(0o755)
    plist = (PACKAGING / "macos" / "Info.plist").read_text(encoding="utf-8").replace("@VERSION@", version)
    (contents / "Info.plist").write_text(plist, encoding="utf-8")
    iconset = stage / "SpotCheck.iconset"
    iconset.mkdir()
    source = PACKAGING / "icon" / "spotcheck.png"
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            run(["sips", "-z", size * scale, size * scale, source, "--out", iconset / name], stdout=subprocess.DEVNULL)
    run(["iconutil", "-c", "icns", iconset, "-o", resources / "SpotCheck.icns"])
    shutil.rmtree(iconset)
    for path in sorted(app.rglob("*")):
        if is_macho(path):
            run(["codesign", "--force", "--sign", "-", path], stdout=subprocess.DEVNULL)
    run(["codesign", "--force", "--sign", "-", app])
    run(["codesign", "--verify", "--deep", "--strict", app])
    image = stage / "image"
    image.mkdir()
    shutil.move(str(app), str(image / "SpotCheck.app"))
    (image / "Applications").symlink_to("/Applications")
    dmg = out / f"SpotCheck-{version}-macos-arm64.dmg"
    run(["hdiutil", "create", "-volname", "SpotCheck", "-srcfolder", image, "-ov", "-format", "UDZO", dmg])
    return dmg


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the SpotCheck installer for Windows or macOS.")
    parser.add_argument("target", choices=["windows", "macos"])
    parser.add_argument("--ffmpeg", required=True, type=Path, help="folder with the FFmpeg build")
    parser.add_argument("--out", default=ROOT / "dist", type=Path)
    parser.add_argument("--cache", default=ROOT / "build" / "cache", type=Path)
    parser.add_argument("--stage-only", action="store_true", help="build and test the bundle without packaging it")
    args = parser.parse_args()

    version = app_version()
    stage = ROOT / "build" / args.target
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    args.out.mkdir(parents=True, exist_ok=True)
    python = build_runtime(args.target, stage, args.cache)
    add_tools(args.target, stage, args.ffmpeg.resolve(), args.cache)
    add_licenses(args.target, stage, args.ffmpeg.resolve(), args.cache)
    add_terms(stage)
    smoke_test(python, stage)
    if args.stage_only:
        return
    if args.target == "windows":
        artifact = package_windows(stage, args.out.resolve(), version, args.cache)
    else:
        artifact = package_macos(stage, args.out.resolve(), version)
    print(f"Built {artifact}")


if __name__ == "__main__":
    main()
