"""Motion stage: real B-roll footage acquisition (wraps lib.footage).

Every scene gets a real moving video clip from free stock providers
(Pexels / Pixabay with a free key, Wikimedia Commons + NASA without any
key). The renderer cuts, slows and grades the clips. Additionally this
stage builds the PRESENTER pool: real moving anchor/host/reporter clips
that replace every static persona frame in the render.
"""
from . import footage


def generate_clips(cfg, run, script, logger):
    ok = footage.assign_footage(cfg, run, script, logger)
    # PRESENTER v6: fetch moving presenter clips AFTER the b-roll pass so
    # the anti-repetition ledger already contains this run's b-roll URLs
    try:
        pres = footage.acquire_presenters(cfg, run, logger)
        if not pres:
            logger.warning("presenter pool empty — host slots will use "
                           "generic moving b-roll (never a still)")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"presenter pool failed ({str(e)[:140]}) — host "
                       "slots will use generic moving b-roll")
    return ok
