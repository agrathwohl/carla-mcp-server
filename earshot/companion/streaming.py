"""Phase C streaming companion — librosa online analysis -> ambient stream.

Long-running subprocess invoked by `EarshotTools.earshot_start_session` when
the caller supplies `companion_audio_file`. Reads the audio file once at
startup, then iterates chunk-by-chunk, runs tempo / key / LUFS / spectral
analysis per chunk, and appends one ambient-stream JSONL line per
measurement.

Why a subprocess (and not in-process):
  - librosa + pyloudnorm + numpy live in the companion venv at
    ~/.carla-mcp/earshot/companion/.venv. The main server runs in its own
    venv where pulling those in would bloat startup and risk dependency
    conflicts (numpy ABI, in particular).
  - The companion can be SIGTERM'd cleanly at end_session without taking
    the server down. asyncio in-process inference would couple lifecycles.

Wall-clock alignment (subtle):
  Every emitted entry's `ts_ms` is the *logical* wall-clock at which the
  chunk's content corresponds in the session's playback timeline:

      ts_ms = playback_start_ms + chunk_start_s * 1000

  NOT the wall-clock at which the companion finished computing the chunk.
  This matches the LV2Poller's semantic (ts_ms = when in the audio stream
  this measurement applies). It lets the companion run *faster than real-
  time* and write entries describing future track positions, which the
  delay tower's buffer + anti-spoiler discipline rely on.

Lookahead pacing:
  The companion races ahead until it's `--lookahead-seconds` ahead of the
  inferred playback position, then sleeps to maintain that lead. Without
  this it would scream through the entire track in seconds and exhaust
  itself before playback even reaches halfway.

Ambient stream schema (one JSONL line per measurement):
  {"ts_ms": <int>, "source": "librosa", "type": "<metric>", "value": <any>}

Metrics emitted per chunk (best-effort; each is independently try/excepted
so one librosa failure doesn't kill the whole pipeline):
  - tempo_bpm           — librosa.beat.beat_track
  - onset_density       — librosa.onset.onset_detect / chunk_seconds
  - key                 — chroma_cqt → Krumhansl-Schmuckler correlation
  - lufs_integrated     — pyloudnorm Meter
  - spectral_centroid   — librosa.feature.spectral_centroid mean

Each metric's `type` is what the semantics map in earshot_start_session
expects on the comparator side, e.g.:
  semantics = {"tempo_bpm": "tempo", "key": "key", ...}
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

# Suppress TF chatter (some librosa internals trigger TF init via essentia
# shared libs even if we don't use it).
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import numpy as np


# Atomicity ceiling — must match AmbientStreamWriter.MAX_LINE_BYTES (4000).
# Lines above this lose multi-writer line-atomicity on Linux PIPE_BUF.
MAX_LINE_BYTES = 4000


# Krumhansl-Schmuckler profiles for key estimation. Same coefficients used
# in phase2.py's fallback path — duplicating here avoids importing the
# Phase 2 module and keeps the companion's import surface small.
PCS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
KS_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
KS_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)


_terminate_requested = False


def _request_terminate(_signum, _frame):
    """SIGTERM handler — flips a flag the main loop polls.

    We deliberately do NOT raise from the handler; the librosa calls and
    file writes finish cleanly, then the loop exits on the next iteration.
    """
    global _terminate_requested
    _terminate_requested = True


def estimate_key_chroma(chroma_mean: np.ndarray) -> dict:
    """Krumhansl-Schmuckler key estimation from a 12-bin chroma vector.

    Returns {"tonic": str, "mode": "major"|"minor", "confidence": float}.
    Confidence is the correlation coefficient of the best match (range
    roughly -1..1; values > 0.5 are typically reliable).
    """
    if chroma_mean.sum() <= 0:
        return {"tonic": "?", "mode": "?", "confidence": 0.0}
    normed = chroma_mean / chroma_mean.sum()
    best = ("C", "major", -2.0)
    for i in range(12):
        c_maj = float(np.corrcoef(normed, np.roll(KS_MAJOR, i))[0, 1])
        c_min = float(np.corrcoef(normed, np.roll(KS_MINOR, i))[0, 1])
        if c_maj > best[2]:
            best = (PCS[i], "major", c_maj)
        if c_min > best[2]:
            best = (PCS[i], "minor", c_min)
    return {"tonic": best[0], "mode": best[1], "confidence": round(best[2], 3)}


def emit(fp, ts_ms: int, metric_type: str, value) -> None:
    """Append one JSONL line. Silently skips if the line would exceed
    PIPE_BUF and risk multi-writer interleaving."""
    entry = {"ts_ms": ts_ms, "source": "librosa",
             "type": metric_type, "value": value}
    line = json.dumps(entry, separators=(",", ":"), ensure_ascii=False)
    if len(line.encode("utf-8")) + 1 > MAX_LINE_BYTES:
        return
    fp.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Earshot streaming companion")
    ap.add_argument("--audio-file", required=True,
                    help="Path to the source audio file (mono/stereo, any format librosa reads).")
    ap.add_argument("--session-id", required=True,
                    help="Session id; the ambient stream lives at "
                         "~/.carla-mcp/earshot/sessions/{id}/ambient.jsonl")
    ap.add_argument("--playback-start-ms", type=int, required=True,
                    help="Wall-clock ms at which session playback started; "
                         "entry ts_ms = playback_start_ms + chunk_start_s*1000.")
    ap.add_argument("--chunk-seconds", type=float, default=2.0,
                    help="Analysis window length in seconds (default 2.0).")
    ap.add_argument("--lookahead-seconds", type=float, default=8.0,
                    help="How far ahead of inferred playback to stay (default 8.0).")
    ap.add_argument("--hop-seconds", type=float, default=None,
                    help="Step between chunk starts; defaults to chunk_seconds (non-overlapping).")
    args = ap.parse_args()

    audio_path = Path(args.audio_file)
    if not audio_path.exists():
        print(json.dumps({"status": "error",
                          "error": f"audio file not found: {audio_path}"}))
        return 2

    ambient_dir = (Path.home() / ".carla-mcp" / "earshot" / "sessions"
                   / args.session_id)
    ambient_dir.mkdir(parents=True, exist_ok=True)
    ambient_path = ambient_dir / "ambient.jsonl"

    # Defer heavy imports until after argparse so --help is snappy.
    import librosa
    import pyloudnorm

    # Single load — the whole file in memory. At 5 minutes mono 44.1kHz this
    # is ~50 MB. Worst case (15-min stereo 96kHz) ~330 MB; acceptable for v1.
    y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    duration_s = float(len(y) / sr)
    meter = pyloudnorm.Meter(sr)

    chunk_samples = int(args.chunk_seconds * sr)
    hop_seconds = args.hop_seconds if args.hop_seconds is not None else args.chunk_seconds

    signal.signal(signal.SIGTERM, _request_terminate)
    signal.signal(signal.SIGINT, _request_terminate)

    chunks_emitted = 0
    metrics_emitted = 0
    started_wall_ms = int(time.time() * 1000)

    # buffering=1 = line-buffered; pairs with the AmbientStreamWriter's
    # O_APPEND multi-writer safety contract (ambient_stream.py docstring).
    with open(ambient_path, "a", buffering=1, encoding="utf-8") as fp:
        chunk_start_s = 0.0
        while chunk_start_s < duration_s and not _terminate_requested:
            start_sample = int(chunk_start_s * sr)
            end_sample = min(start_sample + chunk_samples, len(y))
            chunk = y[start_sample:end_sample]
            if len(chunk) < int(sr * 0.5):
                # Less than 0.5s of audio left; pyloudnorm + chroma both
                # become unreliable below that threshold. Stop cleanly.
                break

            # Logical wall-clock at which this chunk's content corresponds
            # in the session's timeline. See module docstring.
            ts_ms = args.playback_start_ms + int(chunk_start_s * 1000)

            # --- Tempo ---
            try:
                tempo_arr, _ = librosa.beat.beat_track(y=chunk, sr=sr)
                tempo_val = float(np.asarray(tempo_arr).flatten()[0])
                if tempo_val > 0:
                    emit(fp, ts_ms, "tempo_bpm", round(tempo_val, 3))
                    metrics_emitted += 1
            except Exception:
                pass

            # --- Onset density (onsets per second within the chunk) ---
            try:
                onsets = librosa.onset.onset_detect(y=chunk, sr=sr, units="samples")
                density = float(len(onsets)) / max(args.chunk_seconds, 1e-6)
                emit(fp, ts_ms, "onset_density", round(density, 3))
                metrics_emitted += 1
            except Exception:
                pass

            # --- Key (Krumhansl-Schmuckler over chroma_cqt) ---
            try:
                chroma = librosa.feature.chroma_cqt(y=chunk, sr=sr)
                key_result = estimate_key_chroma(chroma.mean(axis=1))
                # Only emit when confidence clears a sanity floor — chroma
                # on percussive-only chunks otherwise reports noise.
                if key_result["confidence"] >= 0.3:
                    emit(fp, ts_ms, "key", key_result)
                    metrics_emitted += 1
            except Exception:
                pass

            # --- LUFS integrated over the chunk ---
            try:
                if len(chunk) >= int(sr * 0.4):  # pyloudnorm needs >=400ms
                    lufs = meter.integrated_loudness(chunk)
                    if np.isfinite(lufs):
                        emit(fp, ts_ms, "lufs_integrated", round(float(lufs), 3))
                        metrics_emitted += 1
            except Exception:
                pass

            # --- Spectral centroid mean ---
            try:
                centroid = float(
                    librosa.feature.spectral_centroid(y=chunk, sr=sr).mean()
                )
                emit(fp, ts_ms, "spectral_centroid", round(centroid, 2))
                metrics_emitted += 1
            except Exception:
                pass

            chunks_emitted += 1
            chunk_start_s += hop_seconds

            # Maintain lookahead window. If we're more than `lookahead_seconds`
            # ahead of inferred playback position, sleep until we're back at
            # the edge of the window. Inferred playback position uses the
            # caller-supplied playback_start_ms (a shared time axis).
            now_ms = int(time.time() * 1000)
            playback_position_s = (now_ms - args.playback_start_ms) / 1000.0
            ahead_s = chunk_start_s - playback_position_s
            if ahead_s > args.lookahead_seconds:
                time.sleep(min(ahead_s - args.lookahead_seconds, args.chunk_seconds))

    elapsed_ms = int(time.time() * 1000) - started_wall_ms
    print(json.dumps({
        "status": "complete" if not _terminate_requested else "terminated",
        "chunks_emitted": chunks_emitted,
        "metrics_emitted": metrics_emitted,
        "duration_processed_s": chunk_start_s,
        "elapsed_ms": elapsed_ms,
        "ambient_path": str(ambient_path),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
