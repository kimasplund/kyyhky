"""Kyyhky - labels for Brother QL printers over the network.

Named for the Finnish word for a carrier pigeon: it takes what you give it
and delivers it onto a label.  Addresses, bar codes, QR codes, or any
layout you describe yourself.
"""

from .addresses import Address, load, expand_copies
from .codes import (
    CodeError,
    QR_ECC,
    SYMBOLOGIES,
    barcode_image,
    barcode_modules,
    qr_image,
    qr_matrix,
    resolve_symbology,
)
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
from .template import (
    BUILTIN as TEMPLATES,
    Template,
    TemplateError,
    builtin as get_template,
    load as load_template,
    read_rows,
)
from .template import render as render_template

__version__ = "1.1.0"
__all__ = [
    "Address",
    "CodeError",
    "JobOptions",
    "LabelSpec",
    "LayoutOptions",
    "MEDIA",
    "DEFAULT_MEDIA",
    "PrinterError",
    "QR_ECC",
    "SYMBOLOGIES",
    "TEMPLATES",
    "Template",
    "TemplateError",
    "barcode_image",
    "barcode_modules",
    "build_job",
    "expand_copies",
    "get_media",
    "get_template",
    "image_to_raster_lines",
    "load",
    "load_template",
    "packbits",
    "preview",
    "qr_image",
    "qr_matrix",
    "query_status",
    "read_rows",
    "render",
    "render_template",
    "resolve_symbology",
    "send",
    "to_printer_space",
]
