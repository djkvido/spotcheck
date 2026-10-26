# SpotCheck Terms of Use

Version 2, 26 October 2026. Czech translation: [TERMS.cs.md](TERMS.cs.md).

SpotCheck asks you to accept these terms when you install it and when you start it for the first time. If you do not agree with them, do not install or use SpotCheck.

## 1. What SpotCheck is

SpotCheck is a hobby open-source project by djkvido, published at https://github.com/djkvido/spotcheck. It helps you keep a local music library in order: it reads the track lists of playlists you give it, finds the tracks on publicly available sources, checks every file against the official preview and measures its sound quality. Its converter converts audio and video files on your computer between formats.

SpotCheck is free software under the GNU General Public License, version 3 or later (GPL). The GPL governs copying, modifying and distributing SpotCheck. These terms describe what you are responsible for when you use it. They do not limit any right the GPL gives you.

## 2. Built from open-source software

SpotCheck is assembled from publicly available open-source software, which it uses under the licenses of its authors: Python, spotDL, yt-dlp, FFmpeg with LAME, Opus and Chromaprint, Deno, FastAPI, Mutagen and others. The complete list with versions and licenses is in `THIRD_PARTY_NOTICES.txt` in the installation folder and in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); the license texts are in the `licenses` folder. The authors of these programs are not affiliated with SpotCheck and do not endorse it.

The installers are built by GitHub Actions directly from the public source code. The source code of every release, including the complete corresponding source code of the bundled FFmpeg build, is attached to the release.

## 3. Your responsibility

SpotCheck is a tool. What it does is decided by you. It does not download audio from Spotify, does not circumvent DRM or any other copy protection, and does not host or share any music.

By using SpotCheck you confirm that:

- you use it for your own personal, non-commercial use;
- you only process content that you are allowed to download, copy and keep under the law of your country;
- you respect the terms of the services you use through it, such as Spotify, YouTube, SoundCloud, Bandcamp, Deezer and Apple;
- you do not use it to share, distribute or sell copyrighted content.

You alone are responsible for what you download with SpotCheck and for what you do with the files.

## 4. Connections and downloads

SpotCheck runs on your computer and only accepts connections from it. It has no account, no analytics and no telemetry, and it sends nothing about you to its authors. It connects to:

- the services that host your playlists and tracks (Spotify, YouTube, YouTube Music, SoundCloud, Bandcamp) to read track lists and download audio;
- Deezer and the iTunes Search API for official previews and metadata, and the lyrics sites spotDL uses to add lyrics (Genius, AZLyrics, Musixmatch);
- PyPI (pypi.org) to show available updates of its components on the Dependencies page, and to download an update of yt-dlp when you ask for it;
- GitHub (github.com) and the distribution sites of FFmpeg and Deno, only when you run SpotCheck from source and ask it to install FFmpeg or Deno;
- GitHub (github.com) on Windows or ffmpeg.martin-riedl.de on macOS, only when you ask the converter to download its extended codecs.

The app never updates or installs a component on its own. It tells you what it is going to download and from where, and starts only after you confirm it. When you run SpotCheck from source, its install and start scripts download the Python packages it needs from PyPI and, if they are missing, Python, FFmpeg and Deno from their official distribution channels. Downloaded components are third-party software under their own licenses.

## 5. Your files

SpotCheck never changes the audio of an existing file without keeping a backup, and only replaces a file when you confirm it. Still, keep your own backup of music you care about.

## 6. No warranty

SpotCheck is provided "as is", without warranty of any kind, as described in sections 15 and 16 of the GPL. To the extent permitted by law, its authors are not liable for any damage, loss of data, claims of third parties or other consequences of using it.

## 7. Trademarks

SpotCheck is not affiliated with, endorsed or sponsored by Spotify, YouTube, Google, SoundCloud, Bandcamp, Deezer or Apple. Their names are trademarks of their respective owners and are used only to describe what SpotCheck works with.

## 8. Changes

When these terms change, SpotCheck asks you to accept the new version before you continue. The current version is always at https://github.com/djkvido/spotcheck/blob/main/TERMS.md. If the English and the Czech version differ, the English version applies.
