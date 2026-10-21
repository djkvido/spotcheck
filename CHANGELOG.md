# Changelog

Notable changes of each release. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

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
