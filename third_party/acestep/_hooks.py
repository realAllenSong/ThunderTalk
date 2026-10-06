# ruff: noqa  # Pinned upstream source; local integration is linted separately.
"""ThunderTalk worker hooks; default callbacks do nothing."""
TIMEOUT_S = 1800

def on_step(done, total):
    pass

def on_tokens(done, total):
    pass
