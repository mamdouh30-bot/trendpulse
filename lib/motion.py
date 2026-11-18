"""Motion stage: real B-roll footage acquisition (wraps lib.footage).

Every scene gets a real moving video clip from free stock providers
(Pexels / Pixabay with a free key, Wikimedia Commons + NASA without any
key). The renderer cuts, slows and grades the clips; still photos remain
the graceful fallback for scenes whose queries returned no footage.
"""
from . import footage


def generate_clips(cfg, run, script, logger):
    return footage.assign_footage(cfg, run, script, logger)
