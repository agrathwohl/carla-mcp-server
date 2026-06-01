"""Phase 2 track pre-analysis — runs inside the companion venv.

Invoked as a subprocess by `EarshotTools.earshot_analyze_track` (in
earshot/ingest.py). Reads a track URL or local path, performs the full
Phase 2 analysis stack, and writes a `context.json` artifact at the path
supplied by --output.

The analyses are chosen to use the best-available algorithm for each
measurement on the companion venv's installed libraries:

  - **tempo + beats**: essentia.standard.RhythmExtractor2013 (multifeature
    method; more accurate than librosa.beat.beat_track on rubato / free-improv)
  - **key**: essentia.standard.KeyExtractor (template-based but with
    multiple profiles; more robust than hand-rolled Krumhansl-Schmuckler)
  - **LUFS + LRA**: essentia.standard.LoudnessEBUR128 (the EBU R128 path
    that the LV2 plugin couldn't expose via control ports)
  - **onset density**: essentia.standard.OnsetRate
  - **spectral centroid**: essentia.standard.SpectralCentroidTime
  - **dynamic envelope**: 1-second-window RMS via essentia
  - **lyrics**: faster-whisper base.en with VAD filtering

The script is deliberately stateless: no logging in / no LLM calls / no
network beyond the optional yt-dlp download. All heavy lifting is local.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from chroma_features import chord_change_rate, harmonic_tension

# Suppress TF / oneDNN chatter that bleeds into stderr.
import os
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")


def download_track(url: str, dest_dir: Path) -> Path:
    """yt-dlp into dest_dir; return the downloaded audio file path.

    If the supplied URL is already a local file path, returns it unchanged.
    """
    if Path(url).exists():
        return Path(url)
    out_template = str(dest_dir / "track.%(ext)s")
    cmd = [
        "yt-dlp",
        "--quiet", "--no-warnings",
        "-x", "--audio-format", "best",
        "-o", out_template,
        url,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    files = list(dest_dir.glob("track.*"))
    if not files:
        raise RuntimeError(f"yt-dlp produced no output for {url}")
    return files[0]


def krumhansl_fallback_key(audio_mono: np.ndarray, sr: int) -> dict:
    """Cheap key estimate as a backup. Only used if essentia.KeyExtractor
    raises; the primary path is essentia."""
    import librosa
    chroma = librosa.feature.chroma_cqt(y=audio_mono, sr=sr).mean(axis=1)
    chroma = chroma / chroma.sum() if chroma.sum() > 0 else chroma
    pcs = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    major = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
    best = (pcs[0], "major", -2.0)
    for i in range(12):
        c_maj = float(np.corrcoef(chroma, np.roll(major, i))[0, 1])
        c_min = float(np.corrcoef(chroma, np.roll(minor, i))[0, 1])
        if c_maj > best[2]:
            best = (pcs[i], "major", c_maj)
        if c_min > best[2]:
            best = (pcs[i], "minor", c_min)
    return {"tonic": best[0], "mode": best[1], "confidence": round(best[2], 4), "method": "krumhansl_fallback"}


def analyze(audio_path: Path, output_path: Path, track_id: str, artist_id: str) -> dict:
    import essentia
    import essentia.standard as es
    import librosa  # still useful for spectral centroid and convenience

    # essentia.MonoLoader: integer sample rate; resample to 44100 for analysis.
    sr = 44100
    loader = es.MonoLoader(filename=str(audio_path), sampleRate=sr)
    y = loader()
    duration_s = float(len(y) / sr)

    # ---- Tempo + beats (essentia RhythmExtractor2013, multifeature) ----
    rex = es.RhythmExtractor2013(method="multifeature")
    bpm, beats, beats_conf, _, beats_intervals = rex(y)
    bpm = float(bpm)
    beats_conf = float(beats_conf)
    beats = [round(float(b), 3) for b in beats]
    ibi_arr = np.diff(beats) if len(beats) > 1 else np.array([])
    ibi_mean = float(ibi_arr.mean()) if len(ibi_arr) else 0.0
    ibi_cv = float(ibi_arr.std() / ibi_mean) if ibi_mean > 0 else 1.0
    tempo_steadiness = 1.0 / (1.0 + ibi_cv)

    # ---- Key (essentia KeyExtractor) ----
    try:
        ke = es.KeyExtractor()
        key_tonic, key_scale, key_strength = ke(y)
        key = {
            "tonic": key_tonic,
            "mode": key_scale,
            "confidence": round(float(key_strength), 4),
            "method": "essentia_KeyExtractor",
        }
    except Exception as e:
        key = krumhansl_fallback_key(y, sr)
        key["fallback_reason"] = str(e)

    # ---- LUFS + LRA (essentia LoudnessEBUR128, stereo expected; mono works too) ----
    # LoudnessEBUR128 wants a 2D stereo array of shape (N, 2) — not a flat
    # interleaved buffer. essentia maps this internally to VECTOR_STEREOSAMPLE.
    # For a mono source we duplicate the channel; the EBU R128 spec averages
    # the two anyway so this is exact for mono material.
    stereo = np.stack([y, y], axis=1).astype(np.float32)
    try:
        lufs = es.LoudnessEBUR128(sampleRate=sr, startAtZero=False)
        momentary, shortterm, integrated, lra = lufs(stereo)
        loudness = {
            "integrated_lufs": round(float(integrated), 2),
            "lra_lu": round(float(lra), 2),
            "momentary_lufs_max": round(float(np.max(momentary)), 2) if len(momentary) else None,
            "momentary_lufs_min": round(float(np.min(momentary[momentary > -120])), 2) if len(momentary) and np.any(momentary > -120) else None,
            "shortterm_lufs_max": round(float(np.max(shortterm)), 2) if len(shortterm) else None,
        }
    except Exception as e:
        loudness = {"error": f"LoudnessEBUR128 failed: {e}"}

    # ---- Onset density (essentia OnsetRate) ----
    try:
        onset_extractor = es.OnsetRate()
        onsets, onset_rate = onset_extractor(y)
        onsets = [round(float(o), 3) for o in onsets]
    except Exception as e:
        onsets = []
        onset_rate = 0.0

    # ---- Spectral centroid (essentia time-domain wrapper) ----
    try:
        sc = es.SpectralCentroidTime(sampleRate=sr)
        # Compute over windowed frames so we get a mean + std, not a single value
        frame_size = 4096
        hop = 2048
        cents = []
        for start in range(0, len(y) - frame_size, hop):
            cents.append(float(sc(y[start:start + frame_size])))
        cents_arr = np.array(cents) if cents else np.array([0.0])
    except Exception:
        # librosa fallback
        cents_arr = librosa.feature.spectral_centroid(y=y, sr=sr).flatten()

    # ---- Dynamic envelope (1-second window RMS) ----
    hop = sr
    rms = librosa.feature.rms(y=y, frame_length=2 * hop, hop_length=hop).flatten()
    rms_db = 20 * np.log10(np.maximum(rms, 1e-8))
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    dynamic_range_db = float(rms_db.max() - rms_db.min())

    # ---- Section boundaries (agglomerative chroma clustering, k=8) ----
    # README §"Phase 2" calls for section boundaries. librosa.segment.agglomerative
    # runs hierarchical clustering on the chroma matrix and returns boundary frame
    # indices. Cheaper than true laplacian spectral clustering and accurate enough
    # for the drift comparator's purposes (matching against current-position).
    try:
        chroma_for_seg = librosa.feature.chroma_cqt(y=y, sr=sr)
        bound_frames = librosa.segment.agglomerative(chroma_for_seg, k=8)
        bound_times = librosa.frames_to_time(bound_frames, sr=sr).tolist()
        bounds = sorted({0.0, *(round(t, 3) for t in bound_times), round(duration_s, 3)})
        section_map = []
        for i in range(len(bounds) - 1):
            section_map.append({
                "index": i,
                "start_s": bounds[i],
                "end_s": bounds[i + 1],
                "duration_s": round(bounds[i + 1] - bounds[i], 3),
            })
        section_error = None
    except Exception as e:
        section_map = []
        section_error = str(e)

    # ---- Harmonic features (chord-change-rate + tension over time) ----
    # Reuses a chroma matrix (same call used for segmentation). Per-frame
    # tension (chroma entropy) and inter-frame change-rate (chroma movement);
    # averaged globally and per section. Guarded: on failure the fields are
    # absent -> Phase2Baseline returns None -> comparators skip (graceful).
    try:
        chroma_h = librosa.feature.chroma_cqt(y=y, sr=sr)            # (12, F)
        frame_times = librosa.frames_to_time(np.arange(chroma_h.shape[1]), sr=sr)
        tensions = [harmonic_tension(chroma_h[:, i]) for i in range(chroma_h.shape[1])]
        changes = [chord_change_rate(chroma_h[:, i - 1], chroma_h[:, i])
                   for i in range(1, chroma_h.shape[1])]
        chord_change_rate_mean = round(float(np.mean(changes)), 4) if changes else 0.0
        harmonic_tension_mean = round(float(np.mean(tensions)), 4) if tensions else 0.0
        section_harmonic = {}
        for sec in section_map:
            lo, hi = sec["start_s"], sec["end_s"]
            t_idx = [i for i, t in enumerate(frame_times) if lo <= t < hi]
            if not t_idx:
                continue
            sec_tension = float(np.mean([tensions[i] for i in t_idx]))
            # change INTO frame i is changes[i-1]; only frames with i>=1 have one
            ch = [changes[i - 1] for i in t_idx if i >= 1]
            sec_change = float(np.mean(ch)) if ch else 0.0
            section_harmonic[str(sec["index"])] = {
                "chord_change_rate": round(sec_change, 4),
                "harmonic_tension": round(sec_tension, 4),
            }
        harmonic_error = None
    except Exception as e:
        chord_change_rate_mean = 0.0
        harmonic_tension_mean = 0.0
        section_harmonic = {}
        harmonic_error = str(e)

    # ---- Whisper lyrics ----
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base.en", compute_type="int8")
        segments, info = model.transcribe(
            str(audio_path),
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 1000},
            word_timestamps=True,
        )
        transcript = []
        transcript_words = []
        for seg in segments:
            transcript.append({
                "start_s": round(seg.start, 3),
                "end_s": round(seg.end, 3),
                "text": seg.text.strip(),
                "avg_logprob": round(seg.avg_logprob, 4),
                "no_speech_prob": round(seg.no_speech_prob, 4),
            })
            # Word-level timing enables anti-spoiler-safe lyric surfacing: the
            # baseline can return only words already sung at a given time,
            # instead of a whole coarse segment that runs minutes ahead.
            for w in (seg.words or []):
                word_text = w.word.strip()
                if not word_text:
                    continue
                transcript_words.append({
                    "start_s": round(w.start, 3),
                    "end_s": round(w.end, 3),
                    "word": word_text,
                })
    except Exception as e:
        transcript = []
        transcript_words = []
        transcript_error = str(e)
    else:
        transcript_error = None

    # ---- Compose context ----
    context = {
        "track_id": track_id,
        "artist_id": artist_id,
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_path": str(audio_path),
        "duration_s": round(duration_s, 3),
        "sample_rate": sr,
        "baseline": {
            "tempo_bpm": round(bpm, 2),
            "tempo_confidence": round(beats_conf, 4),
            "tempo_steadiness_0to1": round(tempo_steadiness, 3),
            "inter_beat_interval_mean_s": round(ibi_mean, 4),
            "inter_beat_interval_cv": round(ibi_cv, 4),
            "beat_count": len(beats),
            "key": key,
            "loudness": loudness,
            "onset_count": len(onsets),
            "onset_rate_hz": round(float(onset_rate), 4),
            "spectral_centroid_mean_hz": round(float(cents_arr.mean()), 1),
            "spectral_centroid_std_hz": round(float(cents_arr.std()), 1),
            "dynamic_range_db": round(dynamic_range_db, 2),
            "chord_change_rate_mean": chord_change_rate_mean,
            "harmonic_tension_mean": harmonic_tension_mean,
        },
        "beat_times_s_sample": beats[:50],
        "rms_envelope_db": [
            {"t_s": round(float(t), 2), "rms_db": round(float(d), 2)}
            for t, d in zip(rms_times, rms_db)
        ],
        "section_map": section_map,
        "section_error": section_error,
        "section_harmonic": section_harmonic,
        "harmonic_error": harmonic_error,
        "transcript": transcript,
        "transcript_segment_count": len(transcript),
        "transcript_word_count": len(transcript_words),
        "transcript_words": transcript_words,
        "transcript_error": transcript_error,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(context, indent=2, ensure_ascii=False))
    return {"status": "complete", "output_path": str(output_path), "duration_s": duration_s, "bpm": bpm, "key": key, "integrated_lufs": loudness.get("integrated_lufs")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track-url", required=True, help="URL or local file path")
    ap.add_argument("--track-id", required=True)
    ap.add_argument("--artist-id", required=True)
    ap.add_argument("--output", required=True, help="Path to write context.json")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="earshot_phase2_") as td:
        td_path = Path(td)
        try:
            audio_path = download_track(args.track_url, td_path)
        except Exception as e:
            print(json.dumps({"status": "error", "stage": "download", "error": str(e)}), file=sys.stdout)
            return 2
        try:
            result = analyze(audio_path, Path(args.output), args.track_id, args.artist_id)
        except Exception as e:
            import traceback
            print(json.dumps({"status": "error", "stage": "analyze", "error": str(e), "trace": traceback.format_exc()}), file=sys.stdout)
            return 3
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
