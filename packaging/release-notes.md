## Download

| System | File |
|---|---|
| Windows 10 and 11 (64-bit) | `SpotCheck-<version>-windows-x64-setup.exe` |
| macOS 11 or newer on Apple Silicon (M1 and later) | `SpotCheck-<version>-macos-arm64.dmg` |

Everything SpotCheck needs is included: Python, FFmpeg with Chromaprint and Deno. Nothing else has to be installed. For video formats the converter offers to download extended codecs once, when you ask for it. The installer and the first start ask you to agree to the [Terms of Use](https://github.com/djkvido/spotcheck/blob/main/TERMS.md).

### Windows

Run the installer. It installs for your user account only and does not need administrator rights. The installers are not yet signed with a code-signing certificate, so Windows SmartScreen may show *Windows protected your PC*; choose **More info › Run anyway**.

### macOS

Open the disk image and drag SpotCheck into Applications. The app is not yet notarized by Apple, so the first start is blocked: open **System Settings › Privacy & Security**, scroll to SpotCheck and choose **Open Anyway**. SpotCheck runs without a Dock icon and opens in your browser; it stops with the power button in its top bar.

## Verify your download

`SHA256SUMS.txt` lists the hash of every file. Each file also carries a signed build provenance attestation, which proves it was built by GitHub Actions from this repository:

```bash
gh attestation verify <file> --repo djkvido/spotcheck
```

## Source code and licenses

SpotCheck is free software under the GNU General Public License v3.0 or later. The complete source code of this release is attached (`spotcheck-<version>-source.tar.gz`), and so is the complete corresponding source code of the included FFmpeg build (`spotcheck-<version>-ffmpeg-sources.tar.gz`). The licenses of all included software are in the `licenses` folder of the installation and in [THIRD_PARTY_NOTICES.md](https://github.com/djkvido/spotcheck/blob/main/THIRD_PARTY_NOTICES.md).

SpotCheck is a hobby project meant for personal use. You are responsible for respecting copyright and the terms of the services involved; see the [Terms of Use](https://github.com/djkvido/spotcheck/blob/main/TERMS.md).
