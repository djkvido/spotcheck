# Third-party software

SpotCheck is licensed under the [GNU General Public License v3.0 or later](LICENSE). It relies on the software listed below. The Windows and macOS installers include all of it, together with every license text (`licenses/` in the installation) and a `THIRD_PARTY_NOTICES.txt` with the exact versions.

## Programs included in the installers

| Component | License | Source |
|---|---|---|
| Python (python-build-standalone) | PSF-2.0 | <https://github.com/astral-sh/python-build-standalone> |
| FFmpeg | LGPL-2.1-or-later | <https://ffmpeg.org> |
| LAME | LGPL-2.0-or-later | <https://lame.sourceforge.io> |
| Opus | BSD-3-Clause | <https://opus-codec.org> |
| Chromaprint | LGPL-2.1-or-later | <https://acoustid.org/chromaprint> |
| Deno | MIT | <https://deno.com> |

FFmpeg is built without GPL or non-free components by [`packaging/build_ffmpeg.sh`](packaging/build_ffmpeg.sh) from the official source releases pinned in [`packaging/versions.json`](packaging/versions.json). Every release on GitHub carries the complete corresponding source code of that build (`spotcheck-<version>-ffmpeg-sources.tar.gz`).

## Python packages

Installed from the hashed lock files in [`packaging/`](packaging). spotDL is run as a separate program.

| Package | License |
|---|---|
| annotated-doc | MIT |
| annotated-types | MIT |
| anyio | MIT |
| beautifulsoup4 | MIT |
| brotli | MIT |
| certifi | MPL-2.0 |
| cffi | MIT-0 |
| charset-normalizer | MIT |
| click | BSD-3-Clause |
| colorama | BSD-3-Clause |
| curl_cffi | MIT |
| dacite | MIT |
| datastar-py | MIT |
| Deprecated | MIT |
| dnspython | ISC |
| fastapi | MIT |
| h11 | MIT |
| idna | BSD-3-Clause |
| jaconv | MIT |
| Jinja2 | BSD-3-Clause |
| markdown-it-py | MIT |
| MarkupSafe | BSD-3-Clause |
| mdurl | MIT |
| mutagen | GPL-2.0-or-later |
| opentelemetry-api | Apache-2.0 |
| pillow | MIT-CMU |
| platformdirs | MIT |
| pycparser | BSD-3-Clause |
| pycryptodomex | BSD-2-Clause and public domain |
| pydantic | MIT |
| pydantic_core | MIT |
| Pygments | BSD-2-Clause |
| pykakasi | GPL-3.0-or-later |
| pymongo | Apache-2.0 |
| PyOTP | MIT |
| python-dateutil | Apache-2.0 AND BSD-3-Clause |
| python-multipart | Apache-2.0 |
| python-slugify | MIT |
| RapidFuzz | MIT |
| readerwriterlock | MIT |
| redis | MIT |
| requests | Apache-2.0 |
| rich | MIT |
| six | MIT |
| soundcloud-v2 | MIT |
| soupsieve | MIT |
| spotapi | GPL-3.0 |
| spotdl | MIT |
| spotipy | MIT |
| spotipyFree | MIT |
| starlette | BSD-3-Clause |
| syncedlyrics | MIT |
| text-unidecode | Artistic-1.0-Perl OR GPL-2.0-or-later |
| typing-inspection | MIT |
| typing_extensions | PSF-2.0 |
| Unidecode | GPL-2.0-or-later |
| urllib3 | MIT |
| uvicorn | BSD-3-Clause |
| validators | MIT |
| websockets | BSD-3-Clause |
| wrapt | BSD-2-Clause |
| yt-dlp | Unlicense |
| yt-dlp-ejs | Unlicense AND MIT AND ISC |
| ytmusicapi | MIT |

## Online services

SpotCheck reads public data from Spotify, YouTube, YouTube Music, SoundCloud, Bandcamp, Deezer and the iTunes Search API. It is not affiliated with, endorsed or sponsored by any of them, and their names are trademarks of their respective owners. Using these services is subject to their terms.
