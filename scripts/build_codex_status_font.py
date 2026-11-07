#!/usr/bin/env python3
# /// script
# dependencies = ["fonttools==4.59.0"]
# ///

from __future__ import annotations

import argparse
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.svgLib.path import SVGPath


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "fonts" / "CodexStatusSymbols.ttf"
FAMILY_NAME = "Codex Status Symbols"
STYLE_NAME = "Regular"
FULL_NAME = f"{FAMILY_NAME} {STYLE_NAME}"
POSTSCRIPT_NAME = "CodexStatusSymbols-Regular"
CODEX_CODEPOINT = 0xE00B
TERMINAL_CODEPOINT = 0xE00C
SPLIT_CODEPOINT = 0xE00D
OPENAI_CODEPOINT = 0xE00E
DEEPSEEK_CODEPOINT = 0xE00F
LEGACY_CODEX_CODEPOINT = 0x100000
UNITS_PER_EM = 1000
ADVANCE_WIDTH = 1000
ASCENT = 1020
DESCENT = -300
WIN_DESCENT = 300
VISUAL_CENTER_Y = 360


def build_brand_glyph(name: str):
    svg_path = REPO_ROOT / "fonts" / "lobehub" / f"{name}.svg"
    bounds_pen = BoundsPen(None)
    SVGPath(svg_path).draw(bounds_pen)
    if bounds_pen.bounds is None:
        raise ValueError(f"SVG has no visible paths: {svg_path}")
    x_min, y_min, x_max, y_max = bounds_pen.bounds
    scale = 720 / max(x_max - x_min, y_max - y_min)
    transform = (
        scale, 0, 0, -scale,
        ADVANCE_WIDTH / 2 - (x_min + x_max) / 2 * scale,
        VISUAL_CENTER_Y + (y_min + y_max) / 2 * scale,
    )
    pen = TTGlyphPen(None)
    SVGPath(svg_path, transform=transform).draw(Cu2QuPen(pen, max_err=0.5, reverse_direction=True))
    return pen.glyph()


def draw_polygon(pen: TTGlyphPen, points: list[tuple[int, int]], reverse: bool = False) -> None:
    if reverse:
        points = list(reversed(points))

    pen.moveTo(points[0])
    for point in points[1:]:
        pen.lineTo(point)
    pen.closePath()


def build_codex_glyph():
    pen = TTGlyphPen(None)
    # Convert the preserved SVG's screen coordinates back to font coordinates.
    SVGPath(REPO_ROOT / "fonts" / "codex.svg", transform=(1, 0, 0, -1, 0, 0)).draw(pen)
    return pen.glyph()


def build_terminal_glyph():
    pen = TTGlyphPen(None)
    stroke = 52
    left = 170
    right = 830
    bottom = 95
    top = 625

    # Lightweight terminal outline.
    draw_polygon(pen, [(left, bottom), (right, bottom), (right, bottom + stroke), (left, bottom + stroke)])
    draw_polygon(pen, [(left, top - stroke), (right, top - stroke), (right, top), (left, top)])
    draw_polygon(pen, [(left, bottom), (left + stroke, bottom), (left + stroke, top), (left, top)])
    draw_polygon(pen, [(right - stroke, bottom), (right, bottom), (right, top), (right - stroke, top)])

    # Prompt chevron and cursor.
    draw_polygon(pen, [(320, 450), (510, 360), (320, 270), (320, 330), (410, 360), (320, 390)])
    draw_polygon(pen, [(540, 240), (705, 240), (705, 292), (540, 292)])

    return pen.glyph()


def build_split_glyph():
    pen = TTGlyphPen(None)
    stroke = 46
    left = 190
    right = 810
    bottom = 105
    top = 615
    mid_x = 500
    mid_y = VISUAL_CENTER_Y

    # Compact four-pane layout mark, used when a window has several shell panes.
    draw_polygon(pen, [(left, bottom), (right, bottom), (right, bottom + stroke), (left, bottom + stroke)])
    draw_polygon(pen, [(left, top - stroke), (right, top - stroke), (right, top), (left, top)])
    draw_polygon(pen, [(left, bottom), (left + stroke, bottom), (left + stroke, top), (left, top)])
    draw_polygon(pen, [(right - stroke, bottom), (right, bottom), (right, top), (right - stroke, top)])
    draw_polygon(pen, [(mid_x - stroke // 2, bottom), (mid_x + stroke // 2, bottom), (mid_x + stroke // 2, top), (mid_x - stroke // 2, top)])
    draw_polygon(pen, [(left, mid_y - stroke // 2), (right, mid_y - stroke // 2), (right, mid_y + stroke // 2), (left, mid_y + stroke // 2)])

    return pen.glyph()


def build_font(output: Path = OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)

    glyph_order = [".notdef", "space", "codex", "terminal", "split", "openai", "deepseek"]
    glyphs = {
        ".notdef": TTGlyphPen(None).glyph(),
        "space": TTGlyphPen(None).glyph(),
        "codex": build_codex_glyph(),
        "terminal": build_terminal_glyph(),
        "split": build_split_glyph(),
        "openai": build_brand_glyph("openai"),
        "deepseek": build_brand_glyph("deepseek"),
    }
    metrics = {name: (ADVANCE_WIDTH, 0) for name in glyph_order}

    fb = FontBuilder(UNITS_PER_EM, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(
        {
            0x20: "space",
            CODEX_CODEPOINT: "codex",
            TERMINAL_CODEPOINT: "terminal",
            SPLIT_CODEPOINT: "split",
            OPENAI_CODEPOINT: "openai",
            DEEPSEEK_CODEPOINT: "deepseek",
            LEGACY_CODEX_CODEPOINT: "codex",
        }
    )
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=ASCENT, descent=DESCENT)
    fb.setupOS2(
        sTypoAscender=ASCENT,
        sTypoDescender=DESCENT,
        usWinAscent=ASCENT,
        usWinDescent=WIN_DESCENT,
    )
    fb.setupNameTable(
        {
            "familyName": FAMILY_NAME,
            "styleName": STYLE_NAME,
            "uniqueFontIdentifier": f"{POSTSCRIPT_NAME};1.100",
            "fullName": FULL_NAME,
            "psName": POSTSCRIPT_NAME,
            "version": "Version 1.100",
        }
    )
    fb.setupPost(isFixedPitch=1)
    fb.setupMaxp()
    fb.font["OS/2"].xAvgCharWidth = ADVANCE_WIDTH
    fb.font["OS/2"].panose.bFamilyType = 2
    fb.font["OS/2"].panose.bSerifStyle = 11
    fb.font["OS/2"].panose.bProportion = 9
    fb.font["OS/2"].sxHeight = 550
    fb.font["OS/2"].sCapHeight = 730
    # Fixed OpenType epoch timestamps keep rebuilds byte-identical.
    fb.font["head"].created = fb.font["head"].modified = 2082844800
    fb.font.recalcTimestamp = False
    fb.save(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the terminal status font from vendored icon sources.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    build_font(parser.parse_args().output)
