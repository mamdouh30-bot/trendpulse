"""Motion clips (portable edition).

AI video generation needs a paid/keyed provider which free hosts do not
have by default, so on this edition key scenes gracefully use the cinematic
Ken Burns stills from the renderer instead. The module keeps the same
interface so a provider can be added later via TRENDPULSE_MOTION_PROVIDER.
"""
from pathlib import Path


def generate_clips(cfg, run, script, logger):
    if not cfg["motion"]["enabled"]:
        logger.info("motion disabled, skipping")
        return True
    import os
    provider = os.environ.get("TRENDPULSE_MOTION_PROVIDER", "").strip()
    if not provider:
        logger.info("motion: no video provider configured on this host — "
                    "key scenes will use cinematic Ken Burns instead")
        return True
    logger.warning(f"motion provider '{provider}' is not supported by the "
                   f"portable edition — using Ken Burns fallback")
    return True
