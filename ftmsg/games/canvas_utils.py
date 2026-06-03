from textual_canvas import Canvas
from textual.color import Color

# 3x5 font for numbers and some letters
FONT_3x5 = {
    '0': ["XXX", "X X", "X X", "X X", "XXX"],
    '1': [" XX", "  X", "  X", "  X", "XXX"],
    '2': ["XXX", "  X", "XXX", "X  ", "XXX"],
    '3': ["XXX", "  X", "XXX", "  X", "XXX"],
    '4': ["X X", "X X", "XXX", "  X", "  X"],
    '5': ["XXX", "X  ", "XXX", "  X", "XXX"],
    '6': ["XXX", "X  ", "XXX", "X X", "XXX"],
    '7': ["XXX", "  X", "  X", "  X", "  X"],
    '8': ["XXX", "X X", "XXX", "X X", "XXX"],
    '9': ["XXX", "X X", "XXX", "  X", "XXX"],
    'A': ["XXX", "X X", "XXX", "X X", "X X"],
    'B': ["XX ", "X X", "XX ", "X X", "XX "],
    'C': ["XXX", "X  ", "X  ", "X  ", "XXX"],
    'E': ["XXX", "X  ", "XX ", "X  ", "XXX"],
    'G': ["XXX", "X  ", "X X", "X X", "XXX"],
    'M': ["X X", "XXX", "X X", "X X", "X X"],
    'O': ["XXX", "X X", "X X", "X X", "XXX"],
    'P': ["XXX", "X X", "XXX", "X  ", "X  "],
    'R': ["XXX", "X X", "XX ", "X X", "X X"],
    'S': ["XXX", "X  ", "XXX", "  X", "XXX"],
    'T': ["XXX", " X ", " X ", " X ", " X "],
    'V': ["X X", "X X", "X X", "X X", " X "],
}

def draw_text(canvas: Canvas, x: int, y: int, text: str, color: Color):
    """Draw text using a 3x5 pixel font."""
    curr_x = x
    for char in str(text).upper():
        if char == ' ':
            curr_x += 4
            continue
        if char in FONT_3x5:
            pattern = FONT_3x5[char]
            for row_idx, row in enumerate(pattern):
                for col_idx, pixel in enumerate(row):
                    if pixel == 'X':
                        canvas.set_pixel(curr_x + col_idx, y + row_idx, color)
        curr_x += 4
