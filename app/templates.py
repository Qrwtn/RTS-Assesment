"""
Shared Jinja2Templates instance.

All routers import from here rather than constructing their own instances,
keeping the template directory config in one place.
"""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
