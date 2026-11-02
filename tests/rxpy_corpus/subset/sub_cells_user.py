"""Imports of cells nothing in their module touches, and of a never-touched constant."""

import sub_cells
from sub_cells import ALPHA as A
from sub_defaults import SPARE


def both():
    return sub_cells.ALPHA * 100 + sub_cells.BETA


def snap():
    return A


def poke(v):
    sub_cells.BETA = v
    return sub_cells.BETA


def spare():
    return SPARE
