# Security policy

## Supported versions

Security fixes are made for the latest release only. Please update before reporting a problem.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Do not open a public issue for a security problem.

Include what you found, how to reproduce it and which version and operating system you used. You will get a reply within seven days. Once a fix is released, the report is published as a security advisory, with credit to you unless you prefer otherwise.

## How SpotCheck protects you

- The web interface listens on `127.0.0.1` only. Requests with any other `Host` header are rejected, which blocks DNS rebinding, and every request that changes something must carry a custom header that other websites cannot send.
- The interface is served with a strict Content Security Policy, without inline scripts, and refuses to be embedded in other pages.
- External programs are started with argument lists, never through a shell, and links are always passed after `--`, so they cannot be read as options.
- Audio is never changed without a backup. A replaced or trimmed file can always be restored.
- SpotCheck collects no data and sends nothing anywhere except the requests needed to search and download music and official previews.

## How the releases are built

- Installers are built by [GitHub Actions](.github/workflows/release.yml) from the tagged source code. Every action is pinned to a full commit hash.
- Python packages are installed from lock files with SHA-256 hashes ([`packaging/`](packaging)). Only versions published at least two weeks before the lock was created are used, except for an update that fixes a known vulnerability, and the locks are audited for known vulnerabilities on every change and every week.
- Python, Deno and the FFmpeg sources are downloaded from their official releases and checked against the SHA-256 hashes in [`packaging/versions.json`](packaging/versions.json).
- Every release lists the SHA-256 hash of each file in `SHA256SUMS.txt` and carries a signed build provenance attestation. To verify a download with the [GitHub CLI](https://cli.github.com):

  ```bash
  gh attestation verify SpotCheck-<version>-windows-x64-setup.exe --repo djkvido/spotcheck
  ```
