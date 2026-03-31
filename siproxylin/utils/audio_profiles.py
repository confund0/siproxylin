"""
Audio card profile management for Linux.

Automatically fixes USB audio cards that are stuck in input-only or output-only
profiles by switching them to duplex profiles (both mic and speakers available).

This makes Linux behave like Windows/Mac where all USB audio endpoints are
exposed simultaneously.
"""

import json
import logging
import subprocess
import platform
from typing import Optional

logger = logging.getLogger(__name__)


def fix_audio_card_profiles() -> None:
    """
    Auto-fix Linux audio card profiles to enable duplex mode on USB devices.

    This function scans all PulseAudio/PipeWire cards and switches any card
    with an input-only or output-only profile to a duplex profile if available.

    Only runs on Linux. Silently returns on other platforms.
    """
    # Only run on Linux
    if platform.system() != "Linux":
        return

    # Check if pactl is available
    try:
        subprocess.run(["pactl", "--version"],
                      capture_output=True,
                      check=True,
                      timeout=2)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        logger.debug("pactl not available, skipping audio profile auto-fix")
        return

    try:
        # Get all cards with their profiles (JSON format)
        result = subprocess.run(
            ["pactl", "-f", "json", "list", "cards"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        )

        cards = json.loads(result.stdout)

        for card in cards:
            try:
                card_name = card.get("name", "unknown")
                active_profile = card.get("active_profile")
                profiles = card.get("profiles", {})

                # Skip if no profiles available
                if not profiles or not active_profile:
                    continue

                # Get active profile details
                active_prof_info = profiles.get(active_profile, {})
                active_sinks = active_prof_info.get("sinks", 0)
                active_sources = active_prof_info.get("sources", 0)

                # Check if current profile is input-only or output-only
                is_input_only = active_sources > 0 and active_sinks == 0
                is_output_only = active_sinks > 0 and active_sources == 0

                if not (is_input_only or is_output_only):
                    # Already has duplex or is off, skip
                    continue

                # Find best duplex profile
                duplex_profile = _find_best_duplex_profile(profiles)

                if duplex_profile:
                    logger.info(f"Auto-switching audio card '{card_name}' from "
                               f"'{active_profile}' to duplex profile '{duplex_profile}'")

                    # Switch profile
                    subprocess.run(
                        ["pactl", "set-card-profile", card_name, duplex_profile],
                        check=True,
                        timeout=5
                    )

                    logger.info(f"Successfully switched {card_name} to duplex mode")
                else:
                    logger.debug(f"Card '{card_name}' has no duplex profile available")

            except Exception as e:
                logger.warning(f"Failed to process card: {e}")
                continue

    except subprocess.CalledProcessError as e:
        logger.debug(f"pactl command failed: {e}")
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse pactl JSON output: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error fixing audio profiles: {e}")


def _find_best_duplex_profile(profiles: dict) -> Optional[str]:
    """
    Find the best duplex profile (has both sources and sinks).

    Preference order:
    1. "pro-audio" - Professional mode with all endpoints
    2. Any profile with both sinks > 0 and sources > 0

    Args:
        profiles: Dict of profile_name -> {'sinks': int, 'sources': int}

    Returns:
        Profile name, or None if no duplex profile found
    """
    # First preference: pro-audio
    if "pro-audio" in profiles:
        prof_info = profiles["pro-audio"]
        if prof_info.get("sinks", 0) > 0 and prof_info.get("sources", 0) > 0:
            return "pro-audio"

    # Second preference: any duplex profile
    for profile_name, prof_info in profiles.items():
        # Skip "off" profile
        if profile_name == "off":
            continue

        sinks = prof_info.get("sinks", 0)
        sources = prof_info.get("sources", 0)

        if sinks > 0 and sources > 0:
            return profile_name

    return None
