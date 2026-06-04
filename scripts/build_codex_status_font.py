#!/usr/bin/env python3
# /// script
# dependencies = ["fonttools==4.59.0", "pillow==11.3.0"]
# ///

from __future__ import annotations

from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "fonts" / "CodexStatusSymbols.ttf"
FAMILY_NAME = "Codex Status Symbols"
STYLE_NAME = "Regular"
FULL_NAME = f"{FAMILY_NAME} {STYLE_NAME}"
POSTSCRIPT_NAME = "CodexStatusSymbols-Regular"
CODEX_CODEPOINT = 0xE00B
TERMINAL_CODEPOINT = 0xE00C
SPLIT_CODEPOINT = 0xE00D
LEGACY_CODEX_CODEPOINT = 0x100000
UNITS_PER_EM = 1000
ADVANCE_WIDTH = 1000
ASCENT = 1020
DESCENT = -300
WIN_DESCENT = 300
VISUAL_CENTER_Y = 360
CODEX_TEMPLATE_PNG = Path("/Applications/Codex.app/Contents/Resources/codexTemplate@2x.png")


def draw_polygon(pen: TTGlyphPen, points: list[tuple[int, int]], reverse: bool = False) -> None:
    if reverse:
        points = list(reversed(points))

    pen.moveTo(points[0])
    for point in points[1:]:
        pen.lineTo(point)
    pen.closePath()


def build_codex_glyph():
    if CODEX_TEMPLATE_PNG.exists():
        return build_codex_glyph_from_template(CODEX_TEMPLATE_PNG)

    pen = TTGlyphPen(None)

    # Fallback used when Codex.app is not installed.
    draw_polygon(
        pen,
        [
            (500, 760),
            (600, 470),
            (900, 360),
            (600, 250),
            (500, -40),
            (400, 250),
            (100, 360),
            (400, 470),
        ],
    )
    draw_polygon(
        pen,
        [
            (500, 505),
            (640, 360),
            (500, 215),
            (360, 360),
        ],
        reverse=True,
    )

    return pen.glyph()


def build_codex_glyph_from_template(path: Path):
    from PIL import Image

    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError(f"Codex template has no visible pixels: {path}")

    left, top, right, bottom = bbox
    source_width = right - left
    source_height = bottom - top
    target_size = 680
    offset_x = (ADVANCE_WIDTH - target_size) // 2
    offset_y = 20
    pixel = target_size / max(source_width, source_height)

    pen = TTGlyphPen(None)
    for y in range(top, bottom):
        run_start = None
        for x in range(left, right + 1):
            visible = x < right and alpha.getpixel((x, y)) >= 80
            if visible and run_start is None:
                run_start = x
            if (not visible) and run_start is not None:
                x0 = offset_x + (run_start - left) * pixel
                x1 = offset_x + (x - left) * pixel
                y_top = offset_y + target_size - (y - top) * pixel
                y_bottom = offset_y + target_size - (y + 1 - top) * pixel
                draw_polygon(
                    pen,
                    [
                        (round(x0), round(y_bottom)),
                        (round(x1), round(y_bottom)),
                        (round(x1), round(y_top)),
                        (round(x0), round(y_top)),
                    ],
                )
                run_start = None

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


def build_font() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    glyph_order = [".notdef", "space", "codex", "terminal", "split"]
    glyphs = {
        ".notdef": TTGlyphPen(None).glyph(),
        "space": TTGlyphPen(None).glyph(),
        "codex": build_codex_glyph(),
        "terminal": build_terminal_glyph(),
        "split": build_split_glyph(),
    }
    metrics = {
        ".notdef": (ADVANCE_WIDTH, 0),
        "space": (ADVANCE_WIDTH, 0),
        "codex": (ADVANCE_WIDTH, 0),
        "terminal": (ADVANCE_WIDTH, 0),
        "split": (ADVANCE_WIDTH, 0),
    }

    fb = FontBuilder(UNITS_PER_EM, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(
        {
            0x20: "space",
            CODEX_CODEPOINT: "codex",
            TERMINAL_CODEPOINT: "terminal",
            SPLIT_CODEPOINT: "split",
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
            "uniqueFontIdentifier": f"{POSTSCRIPT_NAME};1.000",
            "fullName": FULL_NAME,
            "psName": POSTSCRIPT_NAME,
            "version": "Version 1.000",
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
    fb.save(OUTPUT)


if __name__ == "__main__":
    build_font()
