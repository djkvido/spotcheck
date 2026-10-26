# Changelog

Notable changes of each release. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- A format converter in its own tab: audio and video files are converted on your computer into MP3, M4A, Opus, OGG, FLAC, ALAC, WAV, AIFF, WMA, AC-3, MP4 (H.264, H.265, AV1), WebM (VP9, AV1), MKV, MOV (H.264, ProRes), AVI, WMV or GIF, or into any other format FFmpeg can write. The sound of a video can be saved on its own, and MKV and MP4 can be rewrapped without re-encoding.
- Conversions can change the quality, resolution, frame rate, sample rate and channels, even out the loudness, remove the sound and convert only a part of a file. They use the graphics card when it can encode the format, run several at once and can be cancelled or retried.
- The original file is never changed or overwritten, tags and cover art are kept, and a failed or cancelled conversion leaves no partial file behind.
- Extended codecs for the converter: a full FFmpeg build that can be downloaded once from the converter or Settings › Dependencies, checked against a fixed SHA-256 hash, and removed again.

### Changed

- Adding a single song is easier to find: the button and the dialog are now called Add playlist or song, and the texts explain that single songs are collected in the Singles list.
- The Terms of Use (version 2) mention the converter and the download of its extended codecs, so they are shown once more for agreement.

## 1.1.1 - 2026-10-22

### Changed

- The installers include platformdirs 4.12.3, and the build uses updated GitHub Actions.

### Security

- The installers include opentelemetry-api 1.45.1, which fixes a known vulnerability in a dependency of FastAPI.

## 1.1.0 - 2026-10-21

### Added

- **Loudness and true peak.** Every check measures integrated loudness, loudness range and true peak (EBU R128). The values are shown in the details of a track and in the CSV export; peaks above 1 dBTP and very quiet files are listed as findings without changing the verdict.
- **Checks without the app.** `spotcheck --check` verifies new and changed files of the current project (`--check all` every file) and prints a report; `spotcheck --report` prints the state as text, JSON or CSV. The exit code is 2 when a file is wrong or needs review, so both commands can run from a scheduler.

### Changed

- Track and file names in any script are matched correctly. Before, names without Latin letters, such as Cyrillic or Japanese titles, could be linked to the wrong file.
- A playlist that cannot be loaded no longer stops the other links of the same run and is retried after 15 minutes, then less often, instead of every 30 seconds.
- Large libraries load and save faster: previews are looked up in memory, a scan only saves when something changed, and the cache of tried sources is limited to 3 GB.
- The interface stays responsive during long jobs, keeps the keyboard focus, and track rows can be opened with Enter.

### Fixed

- Two downloads with the same name can no longer overwrite each other, and a new file only gets its final name once it is complete.
- Restoring a backup keeps the backup until the swap has succeeded, and a track never stays marked as busy after a failure.
- The library and the project list are written safely and fall back to their previous copy when the current one is damaged; a file that is only locked is no longer treated as corrupt.
- Playlists with the same name get their own folders, and files stay linked while the music folder is unavailable, for example on a disconnected drive.
- A hanging download or FFmpeg call is stopped together with all its child processes, and cancelling a job stops it immediately.
- The folder dialog works on macOS.
- Starting SpotCheck again finds the running app even when it had to use another port, instead of starting a second copy on the same library.
- A failed single song was reported with the text of another message.

### Security

- SpotCheck only listens on this computer, and covers and previews are only fetched over HTTPS with a size limit.
- Links in the interface only open web addresses, and exported CSV cells cannot be interpreted as formulas.
- The installers include certifi 2026.10.19 and Pillow 13.0.0, which fix known vulnerabilities. SpotCheck itself does not use Pillow; it is a dependency of a spotDL component.

## 1.0.2 - 2026-10-17

### Changed

- Links pasted without `https://`, with `http://`, as Spotify URIs (`spotify:track:...`) or as YouTube Shorts are completed before they are recognised.
- The size of a Spotify playlist is shown as soon as it is known, instead of after minutes without any message.
- Czech and Slovak video suffixes such as "Oficiální video" are removed from YouTube titles.

## 1.0.1 - 2026-10-16

### Added

- Single songs from Spotify, YouTube, YouTube Music, SoundCloud and Bandcamp are collected in the **Singles** list, in their own folder, and checked like every other track.
- Settings › About › Diagnostics creates a report with the installed components and an optional connection test; the same is available as `spotcheck --diagnostics`. Nothing is sent anywhere.

### Changed

- A playlist that cannot be loaded says why (connection, limit of the service, timeout) in the Log card and in `spotcheck.log`.

## 1.0.0 - 2026-10-15

First public release.
