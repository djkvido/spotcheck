# Contributing

Thanks for your interest in SpotCheck. This page covers what you need to work on it.

## Setup

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"     # Windows: .venv\Scripts\python
.venv/bin/python -m spotcheck --setup
```

`--setup` installs spotDL into its own folder (`.venv/spotdl`), so its pinned dependencies never hold back the rest, and installs FFmpeg and Deno when they are missing.

Run the app with a workspace outside the repository so your test data stays separate:

```bash
python -m spotcheck --home ../spotcheck-data
```

## Checks

CI runs the same checks on Linux and Windows:

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

The tests do not need network access. `tests/test_audio_ffmpeg.py` and `tests/test_quality.py` exercise the real FFmpeg (silence detection, trimming, fingerprints, truncated files, spectrum cut-off, tags of MP3, M4A and Opus) and are skipped when FFmpeg is not installed. New logic, such as decisions, calculations or parsing, should come with a test.

## Conventions

- Code and commit messages are in English.
- The code carries no comments or docstrings; clear names, small functions and tests document it.
- The backend never produces user-facing text. It sends message codes with parameters (`{"code": "lead_silence", "params": {"seconds": 3.0}}`), and the UI translates them. Every UI string lives in `spotcheck/static/i18n.js`; when you add one, add it to both the English and the Czech catalog. Other languages are welcome.
- No throwaway scripts, debug output or commented-out code in the repository.
- `TERMS.md` and `TERMS.cs.md` are shown in the installer and summarised in the consent dialog. When their substance changes, update both, the date and the version line, and raise `VERSION` in `spotcheck/terms.py` so that everyone is asked to agree again.

## Layout

| Module | Responsibility |
|---|---|
| `config.py` | application home and the folders of the active project |
| `runtime.py` | environment of the installed app: user folders, bundled tools, the separate spotDL folder |
| `projects.py` | project registry, validation of names and music folders |
| `fileops.py` | safe replacement and removal of music files (retries while a file is locked) |
| `settings.py` | default settings, validation and bounds |
| `analysis.py` | length, silence, integrity, fingerprints (chromaprint), spectrum cut-off, trimming |
| `tags.py` | tags and cover art of MP3, M4A, Opus/Ogg and FLAC |
| `origins.py` | playlist links from Spotify, YouTube, SoundCloud and Bandcamp; loading yt-dlp playlists and matching their tracks on Deezer |
| `sources.py` | official previews (Deezer, iTunes) and candidates from YouTube Music, YouTube and SoundCloud |
| `library.py` | playlists and tracks, persistence, matching files on disk |
| `engine.py` | job queue, the per-track pipeline (download, check, verify, fix, trim), automatic maintenance, numbered names, playlist files, exports and tags |
| `deps.py` | dependency checks and installation |
| `terms.py` | version of the Terms of Use and the user's consent |
| `server.py` | HTTP API, Server-Sent Events, static UI |
| `static/` | the web UI, plain JavaScript without a build step |

## Rules for the user's files

- The audio of an existing file is never changed without a backup in the project's `backups/` folder. Filling in missing tags and numbering file names touch only metadata and names; tags that are present are never overwritten.
- By default a better source is only proposed; the user confirms the replacement.
- Every download runs in its own temporary directory so parallel workers cannot mix up files.
- Automatic jobs download, check and verify, and keep numbered names and playlist files in line with the settings; they never replace or trim an existing file. Silence of a new download is trimmed as part of the download when trimming is enabled.
- Removing a playlist or a project never deletes files. A file is deleted only after a replacement with another format, once the previous version is backed up.

## Releases

1. Update `__version__` in `spotcheck/__init__.py`, commit and push.
2. Tag the commit with the same version, for example `git tag v1.0.2`, and push the tag.
3. The [Release workflow](.github/workflows/release.yml) builds FFmpeg from source, the Windows installer and the macOS app, installs and checks both, and creates a draft release with the installers, the source code, `SHA256SUMS.txt` and build provenance attestations.
4. Review the draft on GitHub and publish it.

Dependencies of the installers are locked with hashes in `packaging/requirements-*.lock`. After changing the dependencies in `pyproject.toml`, regenerate the locks with only releases that are at least two weeks old:

```bash
uv pip compile pyproject.toml --generate-hashes --no-header --no-annotate --python-version 3.12 \
  --python-platform x86_64-pc-windows-msvc --only-binary :all: --exclude-newer <date> -o packaging/requirements-windows.lock
```

and the same with `--python-platform aarch64-apple-darwin -o packaging/requirements-macos.lock`. Versions and hashes of Python, Deno and the FFmpeg sources live in `packaging/versions.json`.

## Pull requests

1. Keep changes small and focused, one topic per pull request.
2. Describe what changes and how you verified it (tests, trying it in the UI).
3. Include a screenshot for UI changes.
