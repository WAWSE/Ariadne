"""Scientific Research Harness project layer."""

__version__ = "0.1.0"

from .project import ProjectError, find_project, init_project

__all__ = ["ProjectError", "find_project", "init_project", "__version__"]

