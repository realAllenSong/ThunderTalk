# ruff: noqa  # Pinned upstream source; local integration is linted separately.
"""ThunderTalk worker hooks; default callbacks do nothing."""
TIMEOUT_S = 1800

def on_step(done, total):
    pass

def on_tokens(done, total):
    pass

# Opt-in materialization at small GPU boundaries; inference outside the worker stays unchanged.
on_gpu_unit = None

def gpu_unit(value, evaluate):
    if on_gpu_unit is not None:
        evaluate(value)
        on_gpu_unit()
