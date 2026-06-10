"""
Shared SlowAPI rate limiter instance.

Import this in main.py (to register with the app) and in any router
that needs @limiter.limit(...) decorators.  Having a single instance
ensures the RateLimitExceeded error handler wired up in main.py fires
correctly for every route.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
