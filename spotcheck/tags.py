from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

TEXT_FIELDS = ("title", "artist", "album")
CHECKED = (*TEXT_FIELDS, "cover")

MP4_KEYS = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "date": "\xa9day", "comment": "\xa9cmt"}
ID3_FRAMES = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "date": "TDRC"}


def _open(path: Path):
    import mutagen

    try:
        return mutagen.File(str(path))
    except Exception:
        return None


def _kind(f) -> str:
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.ogg import OggFileType

    if isinstance(f, MP3):
        return "mp3"
    if isinstance(f, MP4):
        return "mp4"
    if isinstance(f, FLAC):
        return "flac"
    if isinstance(f, OggFileType):
        return "ogg"
    return "other"


def _text(f, kind: str, field: str) -> str:
    tags = f.tags
    if tags is None:
        return ""
    if kind == "mp3":
        frame = tags.get(ID3_FRAMES[field])
        return str(frame.text[0]).strip() if frame and frame.text else ""
    if kind == "mp4":
        values = tags.get(MP4_KEYS[field]) or []
        return str(values[0]).strip() if values else ""
    if kind in ("ogg", "flac"):
        values = tags.get(field) or []
        return str(values[0]).strip() if values else ""
    return ""


def cover(path: Path) -> bytes:
    f = _open(path)
    if f is None or f.tags is None:
        return b""
    kind = _kind(f)
    if kind == "mp3":
        pictures = f.tags.getall("APIC")
        return pictures[0].data if pictures else b""
    if kind == "mp4":
        covers = f.tags.get("covr") or []
        return bytes(covers[0]) if covers else b""
    if kind == "flac" and f.pictures:
        return f.pictures[0].data
    if kind in ("ogg", "flac"):
        from mutagen.flac import Picture

        for value in f.tags.get("metadata_block_picture") or []:
            try:
                return Picture(base64.b64decode(value)).data
            except Exception:
                continue
    return b""


def missing(path: Path) -> list[str] | None:
    f = _open(path)
    if f is None:
        return None
    kind = _kind(f)
    if kind == "other":
        return None
    gaps = [field for field in TEXT_FIELDS if not _text(f, kind, field)]
    if not cover(path):
        gaps.append("cover")
    return gaps


def _picture_block(data: bytes) -> Any:
    from mutagen.flac import Picture

    picture = Picture()
    picture.type = 3
    picture.mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    picture.data = data
    return picture


def write(path: Path, values: dict[str, str], cover_data: bytes | None = None, overwrite: bool = False) -> list[str]:
    f = _open(path)
    if f is None:
        raise ValueError(f"Unsupported or unreadable audio file: {path.name}")
    kind = _kind(f)
    if kind == "other":
        raise ValueError(f"Unsupported audio file: {path.name}")
    if f.tags is None:
        f.add_tags()
    written: list[str] = []
    for field, value in values.items():
        value = str(value or "").strip()
        if not value or (field != "comment" and not overwrite and field in TEXT_FIELDS and _text(f, kind, field)):
            continue
        if kind == "mp3":
            from mutagen import id3

            if field == "comment":
                f.tags.delall("COMM")
                f.tags.add(id3.COMM(encoding=3, lang="eng", desc="", text=[value]))
            else:
                f.tags.delall(ID3_FRAMES[field])
                f.tags.add(getattr(id3, ID3_FRAMES[field])(encoding=3, text=[value]))
        elif kind == "mp4":
            f.tags[MP4_KEYS[field]] = [value]
        else:
            f.tags[field] = [value]
        written.append(field)
    if cover_data and (overwrite or not cover(path)):
        if kind == "mp3":
            from mutagen import id3

            f.tags.delall("APIC")
            mime = _picture_block(cover_data).mime
            f.tags.add(id3.APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover_data))
        elif kind == "mp4":
            from mutagen.mp4 import MP4Cover

            image_format = MP4Cover.FORMAT_PNG if cover_data[:4] == b"\x89PNG" else MP4Cover.FORMAT_JPEG
            f.tags["covr"] = [MP4Cover(cover_data, imageformat=image_format)]
        elif kind == "flac":
            f.clear_pictures()
            f.add_picture(_picture_block(cover_data))
        else:
            block = base64.b64encode(_picture_block(cover_data).write()).decode("ascii")
            f.tags["metadata_block_picture"] = [block]
        written.append("cover")
    if written:
        f.save()
    return written


def copy(src: Path, dst: Path) -> None:
    source, target = _open(src), _open(dst)
    if source is None or target is None or source.tags is None:
        return
    kind = _kind(target)
    if kind != _kind(source) or kind == "other":
        return
    if kind == "mp3":
        source.tags.save(str(dst))
        return
    if target.tags is None:
        target.add_tags()
    target.tags.clear()
    if kind == "mp4":
        for key, value in source.tags.items():
            target.tags[key] = value
    else:
        for key, value in source.tags.items():
            target.tags[key] = value
        if kind == "flac":
            target.clear_pictures()
            for picture in source.pictures:
                target.add_picture(picture)
    target.save()
