#!/usr/bin/env python3
"""
Audio Fundamentals & Safety System for Carla MCP Server
Enforces non-negotiable audio engineering best practices
"""
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ViolationSeverity(Enum):
    """Severity levels for fundamental violations"""
    CRITICAL = "critical"  # Always block
    WARNING = "warning"    # Warn but allow with explicit override
    INFO = "info"          # Inform user but proceed


@dataclass
class SafetyViolation:
    """Represents a violation of audio fundamentals"""
    severity: ViolationSeverity
    rule: str
    message: str
    parameter: Optional[str] = None
    current_value: Optional[Any] = None
    safe_value: Optional[Any] = None
    explanation: str = ""


class AudioFundamentals:
    """
    Codifies non-negotiable audio engineering principles.

    These rules are ALWAYS enforced to prevent:
    - Digital clipping (true peak > 0dBTP)
    - Dangerous gain staging
    - Missing critical measurements
    - Unsafe plugin configurations
    """

    # =====================================================================
    # TIER 1: CRITICAL RULES (NEVER VIOLATED - OPERATIONS BLOCKED)
    # =====================================================================

    @staticmethod
    def check_true_peak_limit(dbtp: float) -> Optional[SafetyViolation]:
        """
        TIER 1 FUNDAMENTAL: True peak must NEVER exceed 0.0dBTP

        This prevents inter-sample peaks that cause distortion during
        format conversion (MP3, streaming, etc.)
        """
        MAX_TRUE_PEAK = -0.1  # Industry standard: -0.1dBTP for safety margin

        if dbtp > 0.0:
            return SafetyViolation(
                severity=ViolationSeverity.CRITICAL,
                rule="MAX_TRUE_PEAK",
                message=f"True peak {dbtp:.2f}dBTP exceeds 0.0dBTP - WILL CAUSE CLIPPING",
                current_value=dbtp,
                safe_value=MAX_TRUE_PEAK,
                explanation=(
                    "True peak above 0dBTP will cause distortion in format conversion "
                    "(MP3, AAC, streaming). Industry standard is -0.1dBTP for safety. "
                    "This is NON-NEGOTIABLE for professional audio."
                )
            )

        if dbtp > MAX_TRUE_PEAK:
            return SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="SAFE_TRUE_PEAK",
                message=f"True peak {dbtp:.2f}dBTP exceeds recommended -0.1dBTP",
                current_value=dbtp,
                safe_value=MAX_TRUE_PEAK,
                explanation=(
                    "While technically under 0dBTP, -0.1dBTP provides a safety margin "
                    "for format conversion and is the professional standard."
                )
            )

        return None

    @staticmethod
    def check_plugin_internal_clipping(plugin_id: str, internal_level: float) -> Optional[SafetyViolation]:
        """
        TIER 1 FUNDAMENTAL: NO PLUGIN SHOULD EVER CLIP INTERNALLY

        Plugins can clip internally even if output is below 0dBFS.
        This causes harmonic distortion before the signal even leaves the plugin.
        """
        if internal_level > 0.0:
            return SafetyViolation(
                severity=ViolationSeverity.CRITICAL,
                rule="PLUGIN_INTERNAL_CLIP",
                message=f"Plugin {plugin_id} is clipping internally at {internal_level:.2f}dBFS",
                parameter="internal_gain_staging",
                current_value=internal_level,
                safe_value=-6.0,
                explanation=(
                    "Plugin is clipping INSIDE its processing, before output stage. "
                    "This creates distortion that cannot be fixed later. "
                    "Reduce input gain or internal parameters. "
                    "NEVER ALLOW INTERNAL CLIPPING."
                )
            )
        return None

    @staticmethod
    def check_lufs_excessive(lufs: float) -> Optional[SafetyViolation]:
        """
        TIER 1 FUNDAMENTAL: LUFS > -8 is dangerously loud

        Excessive loudness destroys dynamics and causes listener fatigue.
        Modern streaming normalizes everything, so louder ≠ better.
        """
        DANGER_THRESHOLD = -8.0
        WARNING_THRESHOLD = -10.0

        if lufs > DANGER_THRESHOLD:
            return SafetyViolation(
                severity=ViolationSeverity.CRITICAL,
                rule="LUFS_TOO_LOUD",
                message=f"Integrated LUFS {lufs:.1f} exceeds -8 LKFS - DANGEROUSLY LOUD",
                current_value=lufs,
                safe_value=-10.0,
                explanation=(
                    "LUFS above -8 indicates severe over-compression and dynamic range loss. "
                    "Streaming services will normalize this DOWN, causing artifacts. "
                    "Modern targets: Streaming -14 LUFS, Mastering -10 to -12 LUFS, Broadcast -23 LUFS. "
                    "Louder is NOT better in modern audio distribution."
                )
            )

        if lufs > WARNING_THRESHOLD:
            return SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="LUFS_VERY_LOUD",
                message=f"Integrated LUFS {lufs:.1f} exceeds -10 LKFS - very loud",
                current_value=lufs,
                safe_value=-12.0,
                explanation=(
                    "LUFS above -10 is at the extreme loud end. "
                    "Ensure this is intentional (e.g., EDM, modern pop mastering). "
                    "Check dynamic range (PLR) - should be at least 6-8 dB."
                )
            )

        return None

    @staticmethod
    def check_sample_rate_mismatch(
        session_rate: int,
        file_rate: int,
        file_name: str
    ) -> Optional[SafetyViolation]:
        """
        TIER 1 FUNDAMENTAL: Sample rate mismatches require user awareness

        Resampling degrades audio quality. Always match session to source material.
        If resampling is unavoidable, use high-quality SRC (sample rate conversion).
        """
        if session_rate != file_rate:
            return SafetyViolation(
                severity=ViolationSeverity.CRITICAL,
                rule="SAMPLE_RATE_MISMATCH",
                message=(
                    f"Sample rate mismatch: Session is {session_rate}Hz but "
                    f"'{file_name}' is {file_rate}Hz"
                ),
                parameter="sample_rate",
                current_value=file_rate,
                safe_value=session_rate,
                explanation=(
                    f"Resampling from {file_rate}Hz to {session_rate}Hz will degrade audio quality. "
                    f"\n\nRECOMMENDED ACTION:"
                    f"\n1. BEST: Change session sample rate to {file_rate}Hz (no quality loss)"
                    f"\n2. ACCEPTABLE: Manually resample source files offline with high-quality SRC"
                    f"\n3. AVOID: Real-time resampling during playback (quality loss + CPU cost)"
                    f"\n\nDSP PRINCIPLE: Never resample unless absolutely necessary. "
                    f"When you must resample, do it ONCE at highest quality, not in real-time."
                )
            )
        return None

    @staticmethod
    def check_bit_depth_reduction(
        source_depth: int,
        target_depth: int,
        dither_enabled: bool,
        dither_type: Optional[str] = None
    ) -> Optional[SafetyViolation]:
        """
        TIER 1 FUNDAMENTAL: Bit depth reduction REQUIRES dither

        Going from 24-bit to 16-bit without dither causes quantization distortion.
        Dither adds very low-level noise that masks quantization errors.
        """
        if target_depth < source_depth:
            if not dither_enabled:
                return SafetyViolation(
                    severity=ViolationSeverity.CRITICAL,
                    rule="BIT_DEPTH_REDUCTION_NO_DITHER",
                    message=(
                        f"Reducing bit depth from {source_depth}-bit to {target_depth}-bit "
                        f"without dither - WILL CAUSE QUANTIZATION DISTORTION"
                    ),
                    parameter="dither",
                    current_value=False,
                    safe_value=True,
                    explanation=(
                        f"Bit depth reduction without dither creates harsh quantization distortion. "
                        f"\n\nREQUIRED: Enable dither when reducing bit depth."
                        f"\n\nDither Types (best to worst for music):"
                        f"\n1. TPDF (Triangular PDF) - Industry standard, best for music"
                        f"\n2. RPDF (Rectangular PDF) - Acceptable"
                        f"\n3. Shaped dither - Best for classical/quiet material"
                        f"\n\nDSP PRINCIPLE: Always dither when quantizing to lower bit depth. "
                        f"The tiny noise is inaudible and prevents ugly distortion."
                    )
                )

            # Check dither type is appropriate
            recommended_dither = "TPDF"
            if dither_enabled and dither_type and dither_type not in ["TPDF", "POW-R", "shaped"]:
                return SafetyViolation(
                    severity=ViolationSeverity.WARNING,
                    rule="SUBOPTIMAL_DITHER_TYPE",
                    message=f"Dither type '{dither_type}' may not be optimal for music",
                    current_value=dither_type,
                    safe_value=recommended_dither,
                    explanation=(
                        f"Current dither: {dither_type}. "
                        f"Recommended: TPDF (Triangular PDF) for music. "
                        f"Shaped dither is best for classical/quiet material. "
                        f"POW-R is good for transparent dithering."
                    )
                )

        return None

    @staticmethod
    def check_any_clipping_anywhere(
        sample_peak_db: float,
        true_peak_dbtp: float,
        plugin_internal_levels: Optional[Dict[str, float]] = None
    ) -> List[SafetyViolation]:
        """
        TIER 1 FUNDAMENTAL: ABSOLUTELY NO CLIPPING ANYWHERE IN SIGNAL CHAIN

        This is the master check that looks for clipping at ANY stage:
        - Sample peaks > 0dBFS
        - True peaks > 0dBTP
        - Plugin internal clipping
        """
        violations = []

        # Check sample peak
        if sample_peak_db > 0.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.CRITICAL,
                rule="SAMPLE_PEAK_CLIPPING",
                message=f"Sample peak {sample_peak_db:.2f}dBFS exceeds 0dBFS - CLIPPING",
                current_value=sample_peak_db,
                safe_value=-0.1,
                explanation=(
                    "Sample peak above 0dBFS means digital clipping is occurring. "
                    "This creates harsh distortion. Reduce gain immediately."
                )
            ))

        # Check true peak (using existing method)
        true_peak_violation = AudioFundamentals.check_true_peak_limit(true_peak_dbtp)
        if true_peak_violation and true_peak_violation.severity == ViolationSeverity.CRITICAL:
            violations.append(true_peak_violation)

        # Check all plugin internal levels
        if plugin_internal_levels:
            for plugin_id, level in plugin_internal_levels.items():
                plugin_violation = AudioFundamentals.check_plugin_internal_clipping(plugin_id, level)
                if plugin_violation:
                    violations.append(plugin_violation)

        return violations

    @staticmethod
    def check_limiter_configuration(
        threshold: float,
        ceiling: float,
        attack: float,
        release: float
    ) -> List[SafetyViolation]:
        """
        FUNDAMENTAL: Limiter must be configured safely

        Ceiling: Must be ≤ -0.1dBTP (never 0.0 or above)
        Threshold: Must be reasonable (typically -12dB to -3dB)
        Attack: Must be fast enough (typically ≤ 1ms for true peak)
        Release: Must allow natural dynamics
        """
        violations = []

        # Critical: Ceiling must never allow clipping
        if ceiling > -0.1:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.CRITICAL,
                rule="LIMITER_CEILING",
                message=f"Limiter ceiling {ceiling:.2f}dB is too high - WILL CAUSE CLIPPING",
                parameter="ceiling",
                current_value=ceiling,
                safe_value=-0.1,
                explanation=(
                    "Limiter ceiling MUST be set to -0.1dBTP or lower to prevent "
                    "true peak clipping. This is the output ceiling of your entire mix."
                )
            ))

        # Warning: Threshold too aggressive
        if threshold > -3.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="LIMITER_THRESHOLD",
                message=f"Limiter threshold {threshold:.2f}dB is very aggressive",
                parameter="threshold",
                current_value=threshold,
                safe_value=-6.0,
                explanation=(
                    "Aggressive limiter threshold (> -3dB) will cause excessive "
                    "gain reduction and squash dynamics. Typical range: -12dB to -6dB."
                )
            ))

        # Warning: Attack too slow for true peak limiting
        if attack > 1.0:  # milliseconds
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="LIMITER_ATTACK",
                message=f"Limiter attack {attack:.2f}ms may be too slow for true peak control",
                parameter="attack",
                current_value=attack,
                safe_value=0.5,
                explanation=(
                    "True peak limiting requires fast attack (typically ≤1ms) to catch "
                    "inter-sample peaks. Slower attack may allow clipping."
                )
            ))

        # Warning: Release too fast (pumping) or too slow (distortion)
        if release < 10.0 or release > 1000.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="LIMITER_RELEASE",
                message=f"Limiter release {release:.0f}ms may cause artifacts",
                parameter="release",
                current_value=release,
                safe_value=100.0,
                explanation=(
                    "Release too fast (< 10ms) causes pumping. "
                    "Release too slow (> 1000ms) causes distortion. "
                    "Typical range: 50-500ms depending on material."
                )
            ))

        return violations

    @staticmethod
    def check_gain_staging(input_level_db: float, output_level_db: float) -> List[SafetyViolation]:
        """
        FUNDAMENTAL: Proper gain staging prevents clipping and maintains headroom

        Input: Should have headroom (typically -6dB to -18dB peak)
        Output: Must not clip (< 0dBFS, ideally with headroom)
        """
        violations = []

        # Critical: Output clipping
        if output_level_db > 0.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.CRITICAL,
                rule="OUTPUT_CLIPPING",
                message=f"Output level {output_level_db:.2f}dB exceeds 0dBFS - CLIPPING",
                current_value=output_level_db,
                safe_value=-6.0,
                explanation=(
                    "Output exceeds digital full scale (0dBFS). This WILL clip. "
                    "Reduce gain or apply limiting."
                )
            ))

        # Warning: Not enough headroom at input
        if input_level_db > -6.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="INPUT_HEADROOM",
                message=f"Input level {input_level_db:.2f}dB has minimal headroom",
                current_value=input_level_db,
                safe_value=-12.0,
                explanation=(
                    "Input should have 6-12dB headroom for processing. "
                    "Peaks above -6dBFS leave little room for EQ/compression boosts."
                )
            ))

        # Warning: Too much headroom (too quiet)
        if input_level_db < -30.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="INPUT_TOO_QUIET",
                message=f"Input level {input_level_db:.2f}dB is very quiet",
                current_value=input_level_db,
                safe_value=-18.0,
                explanation=(
                    "Input is too quiet (< -30dBFS). This will increase noise floor. "
                    "Increase gain earlier in signal chain."
                )
            ))

        return violations

    @staticmethod
    def require_metering_standards() -> Dict[str, Any]:
        """
        FUNDAMENTAL: These meters MUST ALWAYS be used

        Returns required metering configuration that must be active.
        """
        return {
            "required_meters": {
                "lufs": {
                    "enabled": True,
                    "standards": ["EBU R128", "ATSC A/85"],
                    "targets": {
                        "streaming": -14.0,  # Spotify, YouTube, Apple Music
                        "broadcast": -23.0,   # EBU R128 (Europe)
                        "atsc": -24.0,        # ATSC A/85 (US TV)
                    },
                    "explanation": (
                        "LUFS (Loudness Units relative to Full Scale) is the "
                        "international standard for perceived loudness. ALWAYS measure "
                        "integrated LUFS for the entire mix."
                    )
                },
                "true_peak": {
                    "enabled": True,
                    "max_dbtp": -0.1,
                    "explanation": (
                        "True Peak measurement detects inter-sample peaks that can "
                        "cause clipping during format conversion. MUST be measured "
                        "and kept below -0.1dBTP."
                    )
                },
                "peak": {
                    "enabled": True,
                    "type": "sample_peak",
                    "explanation": (
                        "Sample peak shows highest individual sample value. "
                        "Used in conjunction with True Peak for complete picture."
                    )
                },
                "dynamic_range": {
                    "enabled": True,
                    "metric": "PLR",  # Peak to Loudness Ratio
                    "explanation": (
                        "Dynamic range (PLR) shows the difference between peak and "
                        "average loudness. Helps assess compression/limiting."
                    )
                }
            },
            "measurement_duration": "entire_mix",  # Never measure just a snippet
            "mandatory": True,  # Cannot be disabled
            "auto_report": True  # Always report these metrics
        }

    # =====================================================================
    # TIER 2: INFORMATIONAL OBSERVATIONS (Helpful context, not strict rules)
    # =====================================================================

    @staticmethod
    def check_problematic_frequency_bands(
        frequency_spectrum: Dict[float, float]  # freq_hz: level_db
    ) -> List[SafetyViolation]:
        """
        TIER 2 FUNDAMENTAL: Frequency balance observations based on psychoacoustics

        These are informational observations based on Fletcher-Munson curves.
        They highlight frequency ranges that MAY benefit from attention, but are NOT strict rules.
        Mix aesthetics vary by genre, intent, and creative vision.
        """
        violations = []

        # Define frequency ranges to observe (mostly informational)
        PROBLEMATIC_BANDS = {
            "harsh_sibilance": {
                "range": (6000, 8000),  # 6-8 kHz
                "threshold_db": -10.0,   # Relative to mix average
                "severity": ViolationSeverity.INFO,
                "name": "Sibilance Range",
                "explanation": (
                    "FYI: 6-8 kHz (sibilance) energy is elevated. "
                    "The ear is quite sensitive here, so this may or may not be intentional. "
                    "If it sounds harsh: De-esser on vocals, gentle cut at 6-7kHz. "
                    "If it sounds good: Trust your ears - meters don't mix records."
                )
            },
            "presence_fatigue": {
                "range": (3000, 5000),  # 3-5 kHz
                "threshold_db": -8.0,
                "severity": ViolationSeverity.INFO,
                "name": "Presence Range",
                "explanation": (
                    "FYI: 3-5 kHz (presence) energy is prominent. "
                    "This adds clarity and forward character but can fatigue with extended listening. "
                    "Consider: Listening at lower volumes, checking against references. "
                    "Many modern mixes emphasize this range intentionally."
                )
            },
            "nasal_honk": {
                "range": (800, 1200),  # 800Hz-1.2kHz
                "threshold_db": -6.0,
                "severity": ViolationSeverity.INFO,
                "name": "Nasal/Boxy Range",
                "explanation": (
                    "FYI: 800-1200 Hz energy is noticeable. "
                    "This can sound 'honky' or 'boxy' in some contexts. "
                    "If the mix sounds good, ignore this. If it sounds nasal: "
                    "Try a narrow cut around 900-1000 Hz on problem sources."
                )
            },
            "low_mid_mud": {
                "range": (200, 500),  # 200-500 Hz
                "threshold_db": -4.0,
                "severity": ViolationSeverity.INFO,
                "name": "Low-Mid Range",
                "explanation": (
                    "FYI: 200-500 Hz energy is substantial. "
                    "This range can build up in dense arrangements. "
                    "If it sounds muddy: High-pass filters on non-bass instruments. "
                    "If it sounds warm and full: You're probably fine."
                )
            },
            "subsonic_waste": {
                "range": (0, 30),  # Below 30 Hz
                "threshold_db": -20.0,
                "severity": ViolationSeverity.INFO,
                "name": "Subsonic Range",
                "explanation": (
                    "FYI: Sub-30 Hz energy detected. "
                    "This is mostly felt rather than heard and consumes headroom. "
                    "Consider: High-pass filter at 30 Hz on master (if not already present). "
                    "Keeps low end tight and saves headroom for audible frequencies."
                )
            },
            "ear_damage_zone": {
                "range": (2000, 4000),  # 2-4 kHz (ear canal resonance)
                "threshold_db": -6.0,
                "severity": ViolationSeverity.CRITICAL,  # This one can literally damage hearing
                "name": "Ear Damage Resonance Zone",
                "explanation": (
                    "2-4 kHz is where the human ear canal naturally resonates (peak sensitivity). "
                    "Excessive energy here can cause actual hearing damage with prolonged exposure. "
                    "This frequency range is perceived as MUCH louder than it measures. "
                    "Solution: VERY careful with boosts here. Check at low volume. Use analyzer. "
                    "CRITICAL: This is not just about sound quality - it's about listener safety."
                )
            }
        }

        # Analyze each problematic band
        for band_id, band_info in PROBLEMATIC_BANDS.items():
            freq_low, freq_high = band_info["range"]
            threshold = band_info["threshold_db"]

            # Calculate average level in this band
            levels_in_band = [
                level for freq, level in frequency_spectrum.items()
                if freq_low <= freq <= freq_high
            ]

            if levels_in_band:
                avg_level = sum(levels_in_band) / len(levels_in_band)

                # Get overall mix average for comparison
                overall_avg = sum(frequency_spectrum.values()) / len(frequency_spectrum)
                relative_level = avg_level - overall_avg

                if relative_level > threshold:
                    violations.append(SafetyViolation(
                        severity=band_info["severity"],
                        rule=f"PROBLEMATIC_FREQ_{band_id.upper()}",
                        message=(
                            f"{band_info['name']} ({freq_low}-{freq_high}Hz) is "
                            f"{relative_level:.1f}dB above mix average (threshold: {threshold:.1f}dB)"
                        ),
                        parameter="frequency_balance",
                        current_value=relative_level,
                        safe_value=threshold - 3.0,  # Suggest 3dB below threshold
                        explanation=band_info["explanation"]
                    ))

        return violations

    @staticmethod
    def check_frequency_balance_overall(
        low_energy: float,      # 20-200 Hz
        low_mid_energy: float,  # 200-500 Hz
        mid_energy: float,      # 500-2000 Hz
        high_mid_energy: float, # 2000-8000 Hz
        high_energy: float      # 8000-20000 Hz
    ) -> List[SafetyViolation]:
        """
        TIER 2: Check overall frequency balance (INFORMATIONAL ONLY)

        Provides frequency balance observations for reference.
        These are NOT strict requirements - mix aesthetics vary by genre and intent.
        """
        violations = []

        # Calculate total energy
        total = low_energy + low_mid_energy + mid_energy + high_mid_energy + high_energy
        if total == 0:
            return violations

        # Calculate percentages
        low_pct = (low_energy / total) * 100
        low_mid_pct = (low_mid_energy / total) * 100
        mid_pct = (mid_energy / total) * 100
        high_mid_pct = (high_mid_energy / total) * 100
        high_pct = (high_energy / total) * 100

        # Provide informational observations about frequency balance
        if low_pct > 45.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.INFO,
                rule="OBSERVATION_BASS_HEAVY",
                message=f"FYI: Low frequencies (20-200Hz) are {low_pct:.1f}% of total energy",
                explanation=(
                    "Observation: Mix has substantial low-frequency energy. "
                    "This may be intentional (EDM, hip-hop, reggae) or could indicate "
                    "bass/kick overlap. Consider checking translation on smaller speakers."
                )
            ))

        if high_mid_pct > 40.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.INFO,
                rule="OBSERVATION_BRIGHT_MIX",
                message=f"FYI: High-mids (2-8kHz) are {high_mid_pct:.1f}% of total energy",
                explanation=(
                    "Observation: Mix has prominent high-mid presence. "
                    "This may be intentional (modern pop, rock) or could create listening fatigue. "
                    "Consider: Reference tracks, listening at lower volumes for extended periods."
                )
            ))

        if low_mid_pct > 35.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.INFO,
                rule="OBSERVATION_LOW_MID_BUILDUP",
                message=f"FYI: Low-mids (200-500Hz) are {low_mid_pct:.1f}% of total energy",
                explanation=(
                    "Observation: Noticeable low-mid energy. "
                    "This range can accumulate in dense arrangements. "
                    "If it sounds muddy, consider: High-pass filtering non-bass instruments."
                )
            ))

        if high_pct < 3.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.INFO,
                rule="OBSERVATION_LIMITED_AIR",
                message=f"FYI: Highs (8-20kHz) are {high_pct:.1f}% of total energy",
                explanation=(
                    "Observation: Limited high-frequency content. "
                    "This may be intentional (vintage sound, warm aesthetic) or could "
                    "indicate missing overhead mics, rolled-off reverb, or dark-sounding sources."
                )
            ))

        return violations

    # =====================================================================
    # PLUGIN-SPECIFIC SAFETY RULES
    # =====================================================================

    @staticmethod
    def check_compressor_safety(
        ratio: float,
        attack: float,
        release: float,
        threshold: float,
        knee: float
    ) -> List[SafetyViolation]:
        """Safety checks for compressor parameters"""
        violations = []

        # Extreme ratio without proper settings
        if ratio > 10.0 and attack < 1.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="COMPRESSOR_EXTREME_RATIO",
                message=f"Ratio {ratio:.1f}:1 with fast attack {attack:.2f}ms may cause pumping",
                explanation=(
                    "High ratio with very fast attack can create pumping artifacts. "
                    "Consider: slower attack (5-10ms), or soft knee for smoother action."
                )
            ))

        # Very fast release with high ratio
        if ratio > 4.0 and release < 50.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="COMPRESSOR_FAST_RELEASE",
                message=f"Fast release {release:.0f}ms with ratio {ratio:.1f}:1 may distort",
                explanation=(
                    "Fast release with high ratio can create distortion on sustained notes. "
                    "Typical releases: 100-300ms for smooth compression."
                )
            ))

        return violations

    @staticmethod
    def check_eq_safety(bands: List[Dict[str, float]]) -> List[SafetyViolation]:
        """Safety checks for EQ parameters"""
        violations = []

        for i, band in enumerate(bands):
            freq = band.get('frequency', 0)
            gain = band.get('gain', 0)
            q = band.get('q', 1.0)

            # Extreme boost with narrow Q
            if gain > 12.0 and q > 3.0:
                violations.append(SafetyViolation(
                    severity=ViolationSeverity.WARNING,
                    rule="EQ_EXTREME_BOOST",
                    message=f"Band {i+1}: +{gain:.1f}dB boost with Q={q:.1f} is very aggressive",
                    explanation=(
                        "Narrow, large boosts (> 12dB, Q > 3) can cause resonance "
                        "and phase issues. Consider: wider Q, smaller boost, or multiband approach."
                    )
                ))

            # Dangerous low-frequency boost
            if freq < 60.0 and gain > 6.0:
                violations.append(SafetyViolation(
                    severity=ViolationSeverity.WARNING,
                    rule="EQ_SUBSONIC_BOOST",
                    message=f"Band {i+1}: +{gain:.1f}dB boost at {freq:.0f}Hz may cause headroom issues",
                    explanation=(
                        "Large low-frequency boosts (< 60Hz) consume headroom and can "
                        "cause speaker issues. Consider: high-pass filter + moderate boost."
                    )
                ))

        return violations

    # =====================================================================
    # FORMAT & DELIVERY STANDARDS
    # =====================================================================

    @staticmethod
    def get_delivery_standards(format_type: str) -> Dict[str, Any]:
        """
        FUNDAMENTAL: Different formats have different requirements

        Returns non-negotiable specifications for delivery format.
        """
        standards = {
            "streaming": {
                "name": "Streaming Services (Spotify, Apple Music, YouTube)",
                "lufs_target": -14.0,
                "lufs_tolerance": 1.0,  # ±1 LU
                "true_peak_max": -1.0,  # -1dBTP for safety with lossy encoding
                "sample_rate": 44100,
                "bit_depth": 16,
                "format": "WAV",
                "explanation": (
                    "Streaming services normalize to -14 LUFS. Louder masters will be "
                    "turned down. Quieter masters may be turned up (with possible artifacts). "
                    "True peak must be -1dBTP minimum due to lossy encoding."
                )
            },
            "broadcast_eu": {
                "name": "European Broadcast (EBU R128)",
                "lufs_target": -23.0,
                "lufs_tolerance": 0.5,  # Strict
                "true_peak_max": -1.0,
                "sample_rate": 48000,
                "bit_depth": 24,
                "format": "WAV",
                "explanation": (
                    "EBU R128 is mandatory for European broadcast. -23 LUFS ±0.5 LU. "
                    "Non-compliance will be rejected or normalized with artifacts."
                )
            },
            "broadcast_us": {
                "name": "US Broadcast (ATSC A/85)",
                "lufs_target": -24.0,
                "lufs_tolerance": 2.0,
                "true_peak_max": -2.0,  # More conservative
                "sample_rate": 48000,
                "bit_depth": 24,
                "format": "WAV",
                "explanation": (
                    "ATSC A/85 for US TV. -24 LUFS ±2 LU. True peak -2dBTP for "
                    "additional safety in transmission chain."
                )
            },
            "cd": {
                "name": "CD Master",
                "lufs_target": -9.0,  # Typical modern CD
                "lufs_tolerance": 3.0,
                "true_peak_max": -0.1,
                "sample_rate": 44100,
                "bit_depth": 16,
                "format": "WAV",
                "dither": "TPDF",  # Required for 16-bit
                "explanation": (
                    "CD format allows higher loudness than streaming. Modern CDs typically "
                    "-9 to -12 LUFS. MUST use dither when converting from 24-bit to 16-bit."
                )
            },
            "mastering": {
                "name": "Mastering / High-Resolution",
                "lufs_target": None,  # Varies by client
                "lufs_tolerance": None,
                "true_peak_max": -0.1,
                "sample_rate": 96000,  # Or client-specified
                "bit_depth": 24,
                "format": "WAV",
                "explanation": (
                    "High-resolution master for further processing. Maintain headroom, "
                    "use highest quality settings. True peak still critical."
                )
            }
        }

        return standards.get(format_type, {})

    # =====================================================================
    # AUTOMATIC CORRECTIONS
    # =====================================================================

    @staticmethod
    def auto_correct_limiter_ceiling(requested_ceiling: float) -> Tuple[float, Optional[SafetyViolation]]:
        """
        Automatically correct unsafe limiter ceiling

        Never allows ceiling above -0.1dBTP, regardless of user request.
        """
        SAFE_CEILING = -0.1

        if requested_ceiling > SAFE_CEILING:
            violation = SafetyViolation(
                severity=ViolationSeverity.CRITICAL,
                rule="AUTO_CORRECTED_CEILING",
                message=f"Limiter ceiling auto-corrected from {requested_ceiling:.2f}dB to {SAFE_CEILING}dB",
                current_value=requested_ceiling,
                safe_value=SAFE_CEILING,
                explanation=(
                    "Requested ceiling would cause clipping. Auto-corrected to -0.1dBTP. "
                    "This is NON-NEGOTIABLE for professional audio safety."
                )
            )
            return SAFE_CEILING, violation

        return requested_ceiling, None

    @staticmethod
    def auto_enable_metering() -> Dict[str, Any]:
        """
        Automatically enable required metering

        These meters are ALWAYS active, user cannot disable them.
        """
        return {
            "meters": [
                {
                    "type": "lufs",
                    "standard": "EBU R128",
                    "integration": "full",
                    "auto_enabled": True,
                    "user_disable": False
                },
                {
                    "type": "true_peak",
                    "oversampling": 4,  # Standard for true peak
                    "auto_enabled": True,
                    "user_disable": False
                },
                {
                    "type": "peak",
                    "mode": "sample",
                    "auto_enabled": True,
                    "user_disable": False
                },
                {
                    "type": "dynamic_range",
                    "metric": "PLR",  # Peak to Loudness Ratio
                    "auto_enabled": True,
                    "user_disable": False
                }
            ],
            "measurement_points": [
                "master_output",  # Always measure final output
                "pre_limiter",    # Always measure before limiting
            ]
        }


class SafetyValidator:
    """
    Validates all operations against audio fundamentals

    Integrates into tool execution pipeline to catch violations
    before they happen.
    """

    def __init__(self):
        self.fundamentals = AudioFundamentals()
        self.violations_log: List[SafetyViolation] = []

    def validate_operation(
        self,
        operation: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, List[SafetyViolation]]:
        """
        Validate operation against fundamentals

        Returns:
            (allowed, violations) - allowed=False blocks operation
        """
        violations = []

        # Route to appropriate validation
        if "limiter" in operation.lower():
            violations.extend(self._validate_limiter(parameters))
        elif "compressor" in operation.lower() or "compress" in operation.lower():
            violations.extend(self._validate_compressor(parameters))
        elif "eq" in operation.lower() or "equalizer" in operation.lower():
            violations.extend(self._validate_eq(parameters))
        elif "gain" in operation.lower():
            violations.extend(self._validate_gain(parameters, context))

        # Check for critical violations
        critical = any(v.severity == ViolationSeverity.CRITICAL for v in violations)

        # Log all violations
        self.violations_log.extend(violations)

        return (not critical, violations)

    def _validate_limiter(self, params: Dict[str, Any]) -> List[SafetyViolation]:
        """Validate limiter parameters"""
        violations = []

        ceiling = params.get('ceiling', params.get('output_level', 0.0))
        threshold = params.get('threshold', -10.0)
        attack = params.get('attack', 1.0)
        release = params.get('release', 100.0)

        # Check limiter configuration
        violations.extend(
            self.fundamentals.check_limiter_configuration(
                threshold, ceiling, attack, release
            )
        )

        return violations

    def _validate_compressor(self, params: Dict[str, Any]) -> List[SafetyViolation]:
        """Validate compressor parameters"""
        violations = []

        ratio = params.get('ratio', 2.0)
        attack = params.get('attack', 10.0)
        release = params.get('release', 100.0)
        threshold = params.get('threshold', -20.0)
        knee = params.get('knee', 0.0)

        violations.extend(
            self.fundamentals.check_compressor_safety(
                ratio, attack, release, threshold, knee
            )
        )

        return violations

    def _validate_eq(self, params: Dict[str, Any]) -> List[SafetyViolation]:
        """Validate EQ parameters"""
        bands = params.get('bands', [])
        if not bands:
            return []

        return self.fundamentals.check_eq_safety(bands)

    def _validate_gain(self, params: Dict[str, Any], context: Optional[Dict[str, Any]]) -> List[SafetyViolation]:
        """Validate gain staging"""
        violations = []

        if context and 'current_level' in context:
            input_level = context['current_level']
            gain_change = params.get('gain', 0.0)
            output_level = input_level + gain_change

            violations.extend(
                self.fundamentals.check_gain_staging(input_level, output_level)
            )

        return violations

    def format_violations_report(self, violations: List[SafetyViolation]) -> str:
        """Format violations for user feedback"""
        if not violations:
            return "✅ All audio fundamentals checks passed"

        report = []

        critical = [v for v in violations if v.severity == ViolationSeverity.CRITICAL]
        warnings = [v for v in violations if v.severity == ViolationSeverity.WARNING]
        info = [v for v in violations if v.severity == ViolationSeverity.INFO]

        if critical:
            report.append("🚨 CRITICAL VIOLATIONS (Operation blocked):\n")
            for v in critical:
                report.append(f"  ❌ {v.message}")
                if v.safe_value is not None:
                    report.append(f"     → Recommended: {v.safe_value}")
                report.append(f"     ℹ️  {v.explanation}\n")

        if warnings:
            report.append("⚠️  WARNINGS (Applied with caution):\n")
            for v in warnings:
                report.append(f"  ⚠️  {v.message}")
                if v.safe_value is not None:
                    report.append(f"     → Recommended: {v.safe_value}")
                report.append(f"     ℹ️  {v.explanation}\n")

        if info:
            report.append("ℹ️  INFORMATIONAL OBSERVATIONS:\n")
            for v in info:
                report.append(f"  💡 {v.message}")
                report.append(f"     {v.explanation}\n")

        return "\n".join(report)


# Global validator instance
_safety_validator: Optional[SafetyValidator] = None


def get_safety_validator() -> SafetyValidator:
    """Get global safety validator instance"""
    global _safety_validator
    if _safety_validator is None:
        _safety_validator = SafetyValidator()
    return _safety_validator
