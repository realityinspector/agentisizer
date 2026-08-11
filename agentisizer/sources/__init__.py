"""Input modules. Each one turns some stream of the world into Events."""
from .filedrop import FileDropSource
from .http_api import HttpSource

__all__ = ["FileDropSource", "HttpSource"]
