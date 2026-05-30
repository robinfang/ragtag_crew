"""生成美化歌词 PDF — Rock, Scissors, Paper"""
from fpdf import FPDF
import os

FONT_DIR = r'C:\Windows\Fonts'

verses = [
    {
        "lines": [
            "Rock, scissors, paper.",
            "Rock, scissors, paper.",
            "One, two, three.",
            "Play with me.",
            "Right hand PAPER!",
            "Left hand PAPER!",
            "It's a butterfly!",
        ],
        "animal": "🦋",
    },
    {
        "lines": [
            "Rock, scissors, paper.",
            "Rock, scissors, paper.",
            "One, two, three.",
            "Play with me.",
            "Right hand ROCK!",
            "Left hand SCISSORS!",
            "It's a snail!",
        ],
        "animal": "🐌",
    },
    {
        "lines": [
            "Rock, scissors, paper.",
            "Rock, scissors, paper.",
            "One, two, three.",
            "Play with me.",
            "Right hand SCISSORS!",
            "Left hand SCISSORS!",
            "It's a crab!",
        ],
        "animal": "🦀",
    },
    {
        "lines": [
            "Rock, scissors, paper.",
            "Rock, scissors, paper.",
            "One, two, three.",
            "Play with me.",
            "Right hand PAPER!",
            "Left hand PAPER!",
            "Oh no! It's a lion!",
        ],
        "animal": "🦁",
    },
]


class LyricsPDF(FPDF):
    def __init__(self):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.add_font('DejaVu', '', os.path.join(FONT_DIR, 'DejaVuSans.ttf'), uni=True)
        self.add_font('DejaVu', 'B', os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf'), uni=True)
        self.add_font('SimHei', '', os.path.join(FONT_DIR, 'simhei.ttf'), uni=True)
        self.add_font('SimHei', 'B', os.path.join(FONT_DIR, 'simhei.ttf'), uni=True)
        self.add_font('NotoEmoji', '', os.path.join(FONT_DIR, 'seguiemj.ttf'), uni=True)


pdf = LyricsPDF()
pdf.set_auto_page_break(auto=False)

# ── Page: Title + full lyrics ──
pdf.add_page()

# Top bar
pdf.set_fill_color(255, 183, 77)
pdf.rect(0, 0, 210, 18, 'F')

# Title
pdf.set_font('DejaVu', 'B', 28)
pdf.set_text_color(255, 255, 255)
pdf.set_xy(0, 2)
pdf.cell(210, 14, 'Rock, Scissors, Paper!', align='C')

# Subtitle
pdf.set_font('SimHei', '', 12)
pdf.set_text_color(140, 90, 30)
pdf.set_xy(0, 22)
pdf.cell(210, 8, '— 英文儿歌 · 石头剪刀布 —', align='C')

# Verses
start_y = 36
verse_h = 60
for vi, verse in enumerate(verses):
    y = start_y + vi * verse_h

    # Verse number badge
    pdf.set_fill_color(255, 152, 0)
    pdf.set_font('DejaVu', 'B', 11)
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(16, y)
    pdf.cell(8, 8, str(vi + 1), align='C', fill=True)

    # Animal emoji
    pdf.set_font('NotoEmoji', '', 16)
    pdf.set_text_color(80, 80, 80)
    pdf.set_xy(186, y)
    pdf.cell(10, 8, verse['animal'], align='C')

    # Lyrics lines
    for line in verse['lines']:
        pdf.set_text_color(50, 50, 50)
        if line.startswith(('Right', 'Left', "It's", 'Oh')):
            pdf.set_font('DejaVu', 'B', 13)
            if 'PAPER' in line or 'ROCK' in line or 'SCISSORS' in line:
                pdf.set_text_color(211, 47, 47)
            elif any(k in line.lower() for k in ('butterfly', 'snail', 'crab', 'lion')):
                pdf.set_text_color(46, 125, 50)
        else:
            pdf.set_font('DejaVu', '', 13)
        pdf.set_xy(28, y)
        pdf.cell(155, 7, line, align='L')
        y += 7.2

    # Separator
    if vi < len(verses) - 1:
        pdf.set_draw_color(220, 200, 170)
        pdf.set_line_width(0.3)
        pdf.line(20, start_y + (vi + 1) * verse_h - 4, 190, start_y + (vi + 1) * verse_h - 4)

# Footer
pdf.set_font('SimHei', '', 9)
pdf.set_text_color(170, 170, 170)
pdf.set_xy(0, 285)
pdf.cell(210, 6, '一年级英语 · 打印版', align='C')

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '歌词.pdf')
pdf.output(out_path)
print(f'OK: {os.path.basename(out_path)} ({os.path.getsize(out_path)} bytes)')
