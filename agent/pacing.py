"""Human-like pauses between page actions. Set AGENT_FAST=1 (tests do) to skip them."""
import os
import random


def wait(page, lo_ms: int, hi_ms: int) -> None:
    if os.environ.get("AGENT_FAST"):
        return
    page.wait_for_timeout(random.randint(lo_ms, hi_ms))
