#!/usr/bin/env python3
"""
Integration of Audio Fundamentals into Carla MCP Server Execution Pipeline

This module wires the audio fundamentals validation into the server's
tool execution flow, ensuring all operations are checked against
professional audio engineering standards.
"""
import logging
from typing import Dict, Any, List, Optional, Tuple
from audio_fundamentals import (
    AudioFundamentals,
    SafetyValidator,
    SafetyViolation,
    ViolationSeverity,
    get_safety_validator
)

logger = logging.getLogger(__name__)


class FundamentalsMiddleware:
    """
    Middleware that wraps tool execution with fundamentals validation

    This sits between the MCP server and the actual tool execution,
    intercepting all operations to validate them first.
    """

    def __init__(self, carla_controller):
        self.carla = carla_controller
        self.validator = get_safety_validator()
        self.fundamentals = AudioFundamentals()

        # Always-on monitoring
        self.continuous_monitoring_enabled = True
        self.auto_metering_enabled = True

        # Statistics
        self.stats = {
            "total_operations": 0,
            "blocked_operations": 0,
            "warnings_issued": 0,
            "auto_corrections": 0
        }

    async def execute_with_validation(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        execute_func: callable
    ) -> Dict[str, Any]:
        """
        Execute a tool with full fundamentals validation

        Flow:
        1. Pre-execution validation (check parameters)
        2. Auto-corrections if needed
        3. Execute operation
        4. Post-execution validation (check results)
        5. Continuous monitoring triggers
        """
        self.stats["total_operations"] += 1

        # Step 1: Pre-execution validation
        pre_violations = await self._pre_execution_check(tool_name, arguments)

        # Step 2: Handle critical violations (BLOCK)
        critical_violations = [v for v in pre_violations if v.severity == ViolationSeverity.CRITICAL]
        if critical_violations:
            self.stats["blocked_operations"] += 1
            return self._create_blocked_response(tool_name, critical_violations)

        # Step 3: Auto-corrections
        corrected_arguments, corrections = await self._auto_correct_parameters(tool_name, arguments)
        if corrections:
            self.stats["auto_corrections"] += len(corrections)
            logger.info(f"Auto-corrected {len(corrections)} parameters for {tool_name}")

        # Step 4: Warnings (but allow execution)
        warnings = [v for v in pre_violations if v.severity == ViolationSeverity.WARNING]
        if warnings:
            self.stats["warnings_issued"] += len(warnings)

        # Step 5: Execute the actual operation
        try:
            result = await execute_func(tool_name, corrected_arguments)
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return {"success": False, "error": str(e)}

        # Step 6: Post-execution validation
        post_violations = await self._post_execution_check(tool_name, result)

        # Step 7: Add validation info to result
        result["fundamentals_validation"] = {
            "pre_execution_violations": len(pre_violations),
            "auto_corrections": len(corrections),
            "post_execution_violations": len(post_violations),
            "warnings": [
                {
                    "severity": v.severity.value,
                    "rule": v.rule,
                    "message": v.message,
                    "explanation": v.explanation
                }
                for v in warnings + post_violations
            ]
        }

        # Step 8: Trigger continuous monitoring if enabled
        if self.continuous_monitoring_enabled:
            await self._trigger_continuous_monitoring()

        return result

    async def _pre_execution_check(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> List[SafetyViolation]:
        """
        Pre-execution validation: Check parameters before operation
        """
        violations = []

        # Get current audio state for context
        context = await self._get_audio_context()

        # Validate operation against fundamentals
        allowed, operation_violations = self.validator.validate_operation(
            tool_name,
            arguments,
            context
        )

        violations.extend(operation_violations)

        # Specific checks based on operation type
        if "set_parameter" in tool_name.lower():
            violations.extend(await self._check_parameter_safety(arguments, context))

        elif "load_plugin" in tool_name.lower() or "add" in tool_name.lower():
            violations.extend(await self._check_plugin_load_safety(arguments, context))

        elif "export" in tool_name.lower() or "bounce" in tool_name.lower():
            violations.extend(await self._check_export_safety(arguments, context))

        return violations

    async def _post_execution_check(
        self,
        tool_name: str,
        result: Dict[str, Any]
    ) -> List[SafetyViolation]:
        """
        Post-execution validation: Check results after operation
        """
        violations = []

        # Get current audio levels
        levels = await self._measure_current_levels()

        # TIER 1: Check for any clipping
        if levels:
            clip_violations = self.fundamentals.check_any_clipping_anywhere(
                sample_peak_db=levels.get("sample_peak_db", -100.0),
                true_peak_dbtp=levels.get("true_peak_dbtp", -100.0),
                plugin_internal_levels=levels.get("plugin_internal_levels", {})
            )
            violations.extend(clip_violations)

            # TIER 1: Check LUFS if available
            if "lufs" in levels:
                lufs_violation = self.fundamentals.check_lufs_excessive(levels["lufs"])
                if lufs_violation:
                    violations.append(lufs_violation)

        # TIER 2: Check frequency balance
        spectrum = await self._measure_frequency_spectrum()
        if spectrum:
            freq_violations = self.fundamentals.check_problematic_frequency_bands(spectrum)
            violations.extend(freq_violations)

        return violations

    async def _auto_correct_parameters(
        self,
        tool_name: str,
        arguments: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Automatically correct dangerous parameters

        Returns: (corrected_arguments, list_of_corrections)
        """
        corrected = arguments.copy()
        corrections = []

        # Auto-correct limiter ceiling
        if "limiter" in tool_name.lower() or "ceiling" in str(arguments.keys()).lower():
            ceiling_key = None
            for key in ["ceiling", "output_level", "max_output"]:
                if key in corrected:
                    ceiling_key = key
                    break

            if ceiling_key:
                requested_ceiling = corrected[ceiling_key]
                safe_ceiling, violation = self.fundamentals.auto_correct_limiter_ceiling(requested_ceiling)

                if violation:
                    corrected[ceiling_key] = safe_ceiling
                    corrections.append(
                        f"Limiter ceiling auto-corrected: {requested_ceiling:.2f}dB → {safe_ceiling}dB"
                    )
                    logger.warning(f"AUTO-CORRECTION: {violation.message}")

        # Auto-enable dither on bit depth reduction
        if "export" in tool_name.lower() or "bounce" in tool_name.lower():
            bit_depth = corrected.get("bit_depth")
            if bit_depth == 16:  # Assuming session is 24-bit
                if not corrected.get("dither_enabled"):
                    corrected["dither_enabled"] = True
                    corrected["dither_type"] = "TPDF"
                    corrections.append("Auto-enabled TPDF dither for 16-bit export")
                    logger.info("AUTO-CORRECTION: Enabled dither for bit depth reduction")

        return corrected, corrections

    async def _check_parameter_safety(
        self,
        arguments: Dict[str, Any],
        context: Dict[str, Any]
    ) -> List[SafetyViolation]:
        """Check if parameter change will cause problems"""
        violations = []

        plugin_id = arguments.get("plugin_id")
        parameter = arguments.get("parameter_id")
        value = arguments.get("value")

        if not all([plugin_id, parameter is not None, value is not None]):
            return violations

        # Check if this parameter change could cause clipping
        plugin_info = context.get("plugins", {}).get(plugin_id, {})
        current_output = plugin_info.get("output_level", -100.0)

        # Estimate output change (this is simplified - real implementation
        # would analyze parameter type and impact)
        if "gain" in str(parameter).lower() or "output" in str(parameter).lower():
            estimated_change = value * 10.0  # Rough estimate
            estimated_output = current_output + estimated_change

            if estimated_output > 0.0:
                violations.append(SafetyViolation(
                    severity=ViolationSeverity.CRITICAL,
                    rule="PARAMETER_WILL_CAUSE_CLIPPING",
                    message=f"Parameter change will cause clipping: estimated output {estimated_output:.2f}dBFS",
                    parameter=str(parameter),
                    current_value=value,
                    safe_value=value * (0.0 - current_output) / estimated_change,
                    explanation=(
                        "This parameter change will push the output above 0dBFS. "
                        "Reduce the value or lower other gains in the chain first."
                    )
                ))

        return violations

    async def _check_plugin_load_safety(
        self,
        arguments: Dict[str, Any],
        context: Dict[str, Any]
    ) -> List[SafetyViolation]:
        """Check if loading plugin will cause problems"""
        violations = []

        # Check CPU headroom
        current_cpu = context.get("cpu_usage", 0.0)
        if current_cpu > 80.0:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="HIGH_CPU_USAGE",
                message=f"CPU usage is {current_cpu:.1f}% - loading plugin may cause dropouts",
                explanation=(
                    "High CPU usage can cause audio dropouts. "
                    "Consider: freezing tracks, increasing buffer size, or bouncing effects."
                )
            ))

        return violations

    async def _check_export_safety(
        self,
        arguments: Dict[str, Any],
        context: Dict[str, Any]
    ) -> List[SafetyViolation]:
        """Check export settings against fundamentals"""
        violations = []

        sample_rate = arguments.get("sample_rate")
        bit_depth = arguments.get("bit_depth")
        dither = arguments.get("dither_enabled", False)
        dither_type = arguments.get("dither_type")

        # Check if metering plugins are available
        has_meters = self._check_metering_available(context)
        if not has_meters:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="NO_METERING_PLUGINS",
                message="Cannot validate LUFS/True Peak - no metering plugins detected",
                explanation=(
                    "Export safety validation requires True Peak and LUFS measurements. "
                    "No meter plugins found in session. "
                    "Consider adding: x42 EBU R128 Meter, meters.lv2, or similar. "
                    "Without meters, cannot enforce -0.1dBTP ceiling or LUFS targets."
                )
            ))

        # Check bit depth + dither
        session_bit_depth = context.get("session_bit_depth", 24)
        if bit_depth and bit_depth < session_bit_depth:
            violation = self.fundamentals.check_bit_depth_reduction(
                session_bit_depth,
                bit_depth,
                dither,
                dither_type
            )
            if violation:
                violations.append(violation)

        # Check sample rate mismatch
        session_rate = context.get("session_sample_rate", 48000)
        if sample_rate and sample_rate != session_rate:
            violations.append(SafetyViolation(
                severity=ViolationSeverity.WARNING,
                rule="EXPORT_SAMPLE_RATE_CONVERSION",
                message=f"Exporting at different sample rate: {session_rate}Hz → {sample_rate}Hz",
                explanation=(
                    "Sample rate conversion during export can degrade quality. "
                    "Best practice: Export at session rate, resample offline if needed."
                )
            ))

        return violations

    def _check_metering_available(self, context: Dict[str, Any]) -> bool:
        """Check if metering plugins are available in session"""
        # Check if any plugins in the session are meter plugins
        plugins = context.get("plugins", {})

        # Common meter plugin identifiers
        meter_keywords = [
            "meter", "lufs", "r128", "ebu", "true peak", "ebur128",
            "k-meter", "vu meter", "ppm"
        ]

        for plugin_id, plugin_info in plugins.items():
            plugin_name = str(plugin_info.get("name", "")).lower()
            plugin_label = str(plugin_info.get("label", "")).lower()

            for keyword in meter_keywords:
                if keyword in plugin_name or keyword in plugin_label:
                    return True

        return False

    async def _trigger_continuous_monitoring(self):
        """
        Continuous monitoring: Always check critical metrics

        This runs in background and alerts if fundamentals are violated.
        """
        # Measure current state
        levels = await self._measure_current_levels()
        if not levels:
            return

        # TIER 1: Check for clipping (ALWAYS)
        if levels.get("sample_peak_db", -100.0) > 0.0:
            logger.critical(
                f"⚠️  CLIPPING DETECTED: Sample peak {levels['sample_peak_db']:.2f}dBFS"
            )

        if levels.get("true_peak_dbtp", -100.0) > 0.0:
            logger.critical(
                f"⚠️  TRUE PEAK CLIPPING: {levels['true_peak_dbtp']:.2f}dBTP"
            )

        # TIER 1: Check LUFS (ALWAYS)
        if "lufs" in levels and levels["lufs"] > -8.0:
            logger.warning(
                f"⚠️  EXCESSIVE LOUDNESS: {levels['lufs']:.1f} LUFS (danger threshold: -8 LUFS)"
            )

        # Check plugin internal clipping (ALWAYS)
        if "plugin_internal_levels" in levels:
            for plugin_id, level in levels["plugin_internal_levels"].items():
                if level > 0.0:
                    logger.critical(
                        f"⚠️  PLUGIN INTERNAL CLIPPING: {plugin_id} at {level:.2f}dBFS"
                    )

    async def _get_audio_context(self) -> Dict[str, Any]:
        """Get current audio session context"""
        return {
            "session_sample_rate": self.carla.get_sample_rate() if hasattr(self.carla, 'get_sample_rate') else 48000,
            "session_bit_depth": 24,  # Carla typically works in 32-bit float internally
            "cpu_usage": self.carla.get_cpu_load() if hasattr(self.carla, 'get_cpu_load') else 0.0,
            "plugins": {},  # Would be populated with actual plugin states
        }

    async def _measure_current_levels(self) -> Optional[Dict[str, Any]]:
        """Measure current audio levels"""
        # This would integrate with your actual metering
        # For now, returning placeholder structure
        return {
            "sample_peak_db": -12.0,  # Placeholder
            "true_peak_dbtp": -0.5,   # Placeholder
            "lufs": -14.0,             # Placeholder
            "plugin_internal_levels": {}  # Would be populated with actual levels
        }

    async def _measure_frequency_spectrum(self) -> Optional[Dict[float, float]]:
        """Measure frequency spectrum"""
        # This would integrate with your spectrum analyzer
        # Return dict of freq_hz: level_db
        return None  # Placeholder

    def _create_blocked_response(
        self,
        tool_name: str,
        violations: List[SafetyViolation]
    ) -> Dict[str, Any]:
        """Create response when operation is blocked"""
        violation_messages = "\n\n".join([
            f"🚨 {v.rule}: {v.message}\n   {v.explanation}"
            for v in violations
        ])

        return {
            "success": False,
            "blocked": True,
            "reason": "AUDIO FUNDAMENTALS VIOLATION",
            "tool": tool_name,
            "message": (
                f"Operation '{tool_name}' was BLOCKED due to critical audio engineering violations.\n\n"
                f"{violation_messages}\n\n"
                f"These are non-negotiable safety rules. The operation cannot proceed."
            ),
            "violations": [
                {
                    "rule": v.rule,
                    "severity": v.severity.value,
                    "message": v.message,
                    "current_value": v.current_value,
                    "safe_value": v.safe_value,
                    "explanation": v.explanation
                }
                for v in violations
            ]
        }

    def get_statistics(self) -> Dict[str, Any]:
        """Get validation statistics"""
        return {
            **self.stats,
            "block_rate": (
                self.stats["blocked_operations"] / self.stats["total_operations"] * 100
                if self.stats["total_operations"] > 0 else 0.0
            ),
            "warning_rate": (
                self.stats["warnings_issued"] / self.stats["total_operations"] * 100
                if self.stats["total_operations"] > 0 else 0.0
            ),
            "auto_correction_rate": (
                self.stats["auto_corrections"] / self.stats["total_operations"] * 100
                if self.stats["total_operations"] > 0 else 0.0
            )
        }


# Global middleware instance
_fundamentals_middleware: Optional[FundamentalsMiddleware] = None


def get_fundamentals_middleware(carla_controller) -> FundamentalsMiddleware:
    """Get global fundamentals middleware instance"""
    global _fundamentals_middleware
    if _fundamentals_middleware is None:
        _fundamentals_middleware = FundamentalsMiddleware(carla_controller)
    return _fundamentals_middleware
