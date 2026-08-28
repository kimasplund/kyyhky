"""Kyyhky - address labels for the Brother QL-580N over the network.

Named for the Finnish word for a carrier pigeon: it takes an address and
delivers it onto a label.
"""

from .addresses import Address, load, expand_copies
from .layout import LayoutOptions, render, to_printer_space, preview
from .media import ALL as MEDIA, DEFAULT_MEDIA, LabelSpec, get as get_media
from .protocol import (
    JobOptions,
    PrinterError,
    build_job,
    image_to_raster_lines,
    packbits,
    query_status,
    send,
)

__version__ = "1.0.0"
__all__ = [
    "Address",
    "JobOptions",
    "LabelSpec",
    "LayoutOptions",
    "MEDIA",
    "DEFAULT_MEDIA",
    "PrinterError",
    "build_job",
    "expand_copies",
    "get_media",
    "image_to_raster_lines",
    "load",
    "packbits",
    "preview",
    "query_status",
    "render",
    "send",
    "to_printer_space",
]
