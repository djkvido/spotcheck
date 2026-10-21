from __future__ import annotations

from pathlib import Path
from typing import Any

from . import analysis, sources, tags
from .config import paths

UPSCALE_BITRATE = 192
UPSCALE_CUTOFF = 16.5
CLIPPING_PEAK = 1.0
QUIET_LUFS = -24.0


class Checks:
    def preview_fingerprint(self, preview: dict) -> tuple[int, ...]:
        file = paths().previews / preview["file"]
        key = (file.name, file.stat().st_mtime)
        with self._preview_lock:
            cached = self._preview_fps.get(key)
        if cached is None:
            cached = analysis.raw_fingerprint(file)
            with self._preview_lock:
                if len(self._preview_fps) > 400:
                    self._preview_fps.clear()
                self._preview_fps[key] = cached
        return cached

    def do_check(self, tr: dict, path: Path) -> dict:
        errors, decoded = analysis.decode_check(path)
        header_length = analysis.duration(path)
        length = decoded or header_length
        lead, tail = analysis.silence_edges(path, length)
        volume = analysis.head_volume(path)
        gap = analysis.head_gap(path, lead)
        bitrate = analysis.bitrate_kbps(path)
        codec = analysis.codec_name(path)
        measured, cutoff = analysis.cutoff_khz(path, length)
        level = analysis.loudness(path) or {}
        missing_tags = tags.missing(path) or []
        expected = float(tr["duration"])
        diff = length - expected
        extra = diff - lead - tail
        verdict, notes = "OK", []
        rank = {"OK": 0, "WARN": 1, "FAIL": 2}

        def flag(level: str, code: str | None = None, **params: Any) -> None:
            nonlocal verdict
            if rank[level] > rank[verdict]:
                verdict = level
            if code:
                notes.append({"code": code, "params": params})

        if errors:
            flag("FAIL", "damaged", errors=errors)
        if decoded and header_length - decoded > 2:
            flag("FAIL", "incomplete", header=round(header_length, 1), decoded=round(decoded, 1))
        if extra > 4:
            flag("FAIL", "intro_likely", seconds=round(extra, 1))
        elif extra > 2:
            flag("WARN", "intro_possible", seconds=round(extra, 1))
        if extra < -8:
            flag("FAIL", "shorter", seconds=round(-extra, 1))
        elif abs(diff) > 3 and extra <= 2:
            flag("WARN", "length_diff", seconds=round(diff, 1))
        if lead >= 2:
            flag("WARN", "lead_silence", seconds=round(lead, 1))
        if tail >= 2:
            flag("WARN", "tail_silence", seconds=round(tail, 1))
        if volume is not None and volume < -55:
            flag("FAIL", "silent_start", db=round(volume))
        elif volume is not None and volume < -40:
            flag("WARN", "quiet_start", db=round(volume))
        if bitrate is not None and bitrate < self.st["min_bitrate"]:
            flag("WARN", "low_bitrate", kbps=bitrate)
        if measured and cutoff is not None and cutoff < self.st["min_cutoff_khz"]:
            flag("WARN", "low_quality", khz=cutoff)
        elif (
            measured
            and cutoff is not None
            and cutoff <= UPSCALE_CUTOFF
            and (bitrate or 0) >= UPSCALE_BITRATE
            and codec in ("MP3", "AAC")
        ):
            flag("WARN", "upscaled", kbps=bitrate, khz=cutoff)
        if level.get("peak") is not None and level["peak"] >= CLIPPING_PEAK:
            notes.append({"code": "clipping", "params": {"db": round(level["peak"], 1)}})
        if level.get("lufs") is not None and level["lufs"] <= QUIET_LUFS:
            notes.append({"code": "quiet", "params": {"lufs": round(level["lufs"], 1)}})
        if missing_tags:
            notes.append({"code": "tags_missing", "params": {"fields": missing_tags}})
        if gap is not None:
            if extra > 1:
                flag("WARN")
            notes.append({"code": "pause", "params": {"seconds": round(gap, 1)}})
        return {
            "verdict": verdict,
            "sig": self.lib.signature(path),
            "file_len": round(length, 1),
            "spotify_len": expected,
            "diff": round(diff, 1),
            "lead": round(lead, 1),
            "tail": round(tail, 1),
            "extra": round(extra, 1),
            "head_db": None if volume is None else round(volume, 1),
            "gap": None if gap is None else round(gap, 1),
            "bitrate": bitrate,
            "codec": codec,
            "cutoff": cutoff,
            "cutoff_measured": measured,
            "lufs": None if level.get("lufs") is None else round(level["lufs"], 1),
            "true_peak": None if level.get("peak") is None else round(level["peak"], 1),
            "lra": None if level.get("lra") is None else round(level["lra"], 1),
            "tags_missing": missing_tags,
            "decode_errors": errors,
            "notes": notes,
        }

    def do_verify(self, tr: dict, path: Path) -> dict:
        if not analysis.ffmpeg_info()["chromaprint"]:
            return {"verdict": "NO_PREVIEW", "reason": "no_chromaprint"}
        preview = sources.get_preview(tr, self.st)
        if preview is None:
            return {"verdict": "NO_PREVIEW", "reason": "no_preview"}
        match = analysis.best_alignment(path, self.preview_fingerprint(preview))
        if match is None:
            return {"verdict": "NO_PREVIEW", "reason": "preview_too_short", "ref": preview}
        ber = match["ber"]
        if ber <= self.st["accept_ber"]:
            verdict = "MATCH"
        elif ber <= self.st["unsure_ber"]:
            verdict = "UNSURE"
        else:
            verdict = "MISMATCH"
        return {
            "verdict": verdict,
            "ber": round(ber, 3),
            "at": round(match["at"], 1),
            "window": round(match["window"], 1),
            "file_len": round(match["file_len"], 1),
            "curve": analysis.downsample_min(match["curve"]),
            "ref": preview,
        }

    def check_and_verify(self, pl: dict, tr: dict, path: Path) -> None:
        self.touch(pl, tr, "check")
        tr["check"] = self.do_check(tr, path)
        self.touch(pl, tr, "verify")
        tr["verify"] = self.do_verify(tr, path)

    def acceptable(self, tr: dict) -> bool:
        check, verify = tr.get("check") or {}, tr.get("verify") or {}
        if verify.get("verdict") == "MATCH":
            return check.get("verdict") != "FAIL"
        if verify.get("verdict") == "NO_PREVIEW":
            return check.get("verdict") != "FAIL" and abs(check.get("extra", 99)) <= self.st["exact_diff"] + 1
        return False

    @staticmethod
    def needs_fix(tr: dict) -> bool:
        verdict = (tr.get("verify") or {}).get("verdict")
        check = tr.get("check") or {}
        extra = check.get("extra") or 0
        wrong_length = check.get("verdict") == "FAIL" and (extra > 4 or extra < -8)
        low_quality = any(n["code"] == "low_quality" for n in check.get("notes", []))
        return verdict in ("MISMATCH", "UNSURE") or wrong_length or low_quality

    @staticmethod
    def refresh_notes(tr: dict) -> None:
        notes = list((tr.get("check") or {}).get("notes", []))
        verify = tr.get("verify") or {}
        if verify.get("verdict") in ("MISMATCH", "UNSURE"):
            notes.insert(0, {"code": verify["verdict"].lower(), "params": {"ber": verify["ber"]}})
        elif verify.get("verdict") == "NO_PREVIEW":
            notes.append({"code": verify.get("reason", "no_preview"), "params": {}})
        if tr.get("proposal"):
            notes.insert(0, {"code": "proposal", "params": {"ber": tr["proposal"].get("ber")}})
        tr["notes"] = notes

    def find_best_candidate(self, job: dict, tr: dict, exclude: set[str]) -> tuple[dict | None, list[dict]]:
        st = self.st
        preview = sources.get_preview(tr, st)
        candidates = sources.find_candidates(tr, exclude, st)
        if preview is None or not analysis.ffmpeg_info()["chromaprint"]:
            close = [c for c in candidates if abs(c["diff"]) <= st["exact_diff"]]
            return (dict(close[0], ber=None) if close else None), [dict(c, ber=None) for c in candidates]

        reference = self.preview_fingerprint(preview)
        tried: list[dict] = []
        for cand in candidates:
            self._check_cancel(job)
            audio = sources.fetch_candidate(cand, self.ytdlp_cmd())
            if audio is None:
                tried.append(dict(cand, ber=None, error="download_failed"))
                continue
            match = analysis.best_alignment(audio, reference)
            record = dict(
                cand,
                ber=None if match is None else round(match["ber"], 3),
                at=None if match is None else round(match["at"], 1),
            )
            if record["ber"] is not None and record["ber"] <= st["accept_ber"]:
                record["cutoff_measured"], record["cutoff"] = analysis.cutoff_khz(audio, float(cand["duration"] or 0))
            tried.append(record)
            strong = record["ber"] is not None and record["ber"] <= st["strong_ber"]
            if strong and abs(record["diff"]) <= st["exact_diff"]:
                break
        matching = [r for r in tried if r["ber"] is not None and r["ber"] <= st["accept_ber"]]
        if not matching:
            return None, tried
        fits = [r for r in matching if abs(r["diff"]) <= st["exact_diff"]] or matching
        lowest = min(r["ber"] for r in fits)
        close = [r for r in fits if r["ber"] <= lowest + 0.05]
        best = max(close, key=lambda r: (self.spectrum_rank(r), -r["ber"]))
        return best, tried

    @staticmethod
    def spectrum_rank(record: dict) -> float:
        if not record.get("cutoff_measured"):
            return 0.0
        return 30.0 if record.get("cutoff") is None else float(record["cutoff"])

    def is_better(self, cand: dict, tr: dict) -> bool:
        st = self.st
        verify, check = tr.get("verify") or {}, tr.get("check") or {}
        if cand.get("ber") is None:
            return abs(cand["diff"]) <= st["exact_diff"] and abs(check.get("extra", 99)) > st["exact_diff"] + 1
        if verify.get("verdict") in ("MISMATCH", "UNSURE"):
            return verify.get("ber") is None or cand["ber"] < verify["ber"] - 0.05
        if verify.get("verdict") == "NO_PREVIEW":
            return True
        fits = abs(cand["diff"]) <= st["exact_diff"]
        current = check.get("cutoff")
        low = bool(check.get("cutoff_measured")) and current is not None and current < st["min_cutoff_khz"]
        sounds_better = low and self.spectrum_rank(cand) >= current + 1.5
        return fits and ((check.get("extra") or 0) > 4 or sounds_better)
