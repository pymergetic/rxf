"""An image as one delta per module."""

import os

import pymergetic.rxf.core.object


class TreeError(RuntimeError):
    pass

INIT_FILE: str = '__init__.rxfd'

def files(image: pymergetic.rxf.core.object.Image, threads: int = 0) -> list[tuple[str, str]]:
    """
    The canonical files of the image as (name, XML): __init__.rxfd first, then one per module by file name.
    """

def write(image: pymergetic.rxf.core.object.Image, directory: str | os.PathLike[str], overwrite: bool = False, threads: int = 0) -> int:
    """
    Writes the files below the directory and returns how many. A used directory needs `overwrite`, and only .rxfd files are replaced.
    """

def load(directory: str | os.PathLike[str], limit: int = ..., threads: int = 0) -> pymergetic.rxf.core.object.Image:
    """
    A new image from the tree: the files are parsed in parallel, then replayed as one atomic delta, the root __init__.rxfd last.
    """
