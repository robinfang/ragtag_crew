"""生成单词卡片 PDF — Rock, Scissors, Paper 儿歌词汇"""
from fpdf import FPDF
import os

FONT_DIR = r'C:\Windows\Fonts'

# 适合一年级下学期的词汇，按类别分组
words = [
    # 核心词 — 石头剪刀布
    ("rock",       "/rɒk/",       "n. 石头"),
    ("scissors",   "/ˈsɪzəz/",    "n. 剪刀"),
    ("paper",      "/ˈpeɪpə/",    "n. 纸"),
    # 数字
    ("one",        "/wʌn/",       "num. 一"),
    ("two",        "/tuː/",       "num. 二"),
    ("three",      "/θriː/",      "num. 三"),
    # 动作
    ("play",       "/pleɪ/",      "v. 玩"),
    # 身体
    ("hand",       "/hænd/",      "n. 手"),
    ("right",      "/raɪt/",      "adj. 右边的"),
    ("left",       "/left/",      "adj. 左边的"),
    # 动物
    ("butterfly",  "/ˈbʌtəflaɪ/", "n. 蝴蝶"),
    ("snail",      "/sneɪl/",     "n. 蜗牛"),
    ("crab",       "/kræb/",      "n. 螃蟹"),
    ("lion",       "/ˈlaɪən/",    "n. 狮子"),
    # 常用
    ("me",         "/miː/",       "pron. 我（宾格）"),
    ("it",         "/ɪt/",        "pron. 它"),
]


class CardPDF(FPDF):
    def __init__(self):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.add_font('DejaVu', '', os.path.join(FONT_DIR, 'DejaVuSans.ttf'), uni=True)
        self.add_font('DejaVu', 'B', os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf'), uni=True)
        self.add_font('SimHei', '', os.path.join(FONT_DIR, 'simhei.ttf'), uni=True)
        self.add_font('SimHei', 'B', os.path.join(FONT_DIR, 'simhei.ttf'), uni=True)
        self.add_font('NotoEmoji', '', os.path.join(FONT_DIR, 'seguiemj.ttf'), uni=True)


pdf = CardPDF()
pdf.set_auto_page_break(auto=False)

margin_x, margin_y = 12, 12
cols, rows = 3, 4
gap = 5
card_w = (210 - 2 * margin_x - (cols - 1) * gap) / cols
card_h = (297 - 2 * margin_y - (rows - 1) * gap) / rows

# Category color coding
category_colors = {
    "core":  (255, 235, 200),    # warm beige
    "num":   (200, 230, 255),    # light blue
    "act":   (220, 255, 220),    # light green
    "body":  (255, 220, 235),    # light pink
    "animal":(255, 245, 200),    # light yellow
    "util":  (230, 230, 255),    # light lavender
}

word_cats = {
    "rock": "core", "scissors": "core", "paper": "core",
    "one": "num", "two": "num", "three": "num",
    "play": "act",
    "hand": "body", "right": "body", "left": "body",
    "butterfly": "animal", "snail": "animal", "crab": "animal", "lion": "animal",
    "me": "util", "it": "util",
}

for idx, (word, phonetic, meaning) in enumerate(words):
    page_idx = idx % (cols * rows)
    if page_idx == 0:
        pdf.add_page()

    col = page_idx % cols
    row = page_idx // cols
    x = margin_x + col * (card_w + gap)
    y = margin_y + row * (card_h + gap)

    cat = word_cats.get(word, "util")
    bg = category_colors[cat]

    # Card background
    pdf.set_fill_color(*bg)
    pdf.rect(x, y, card_w, card_h, 'F')

    # Card border — rounded corners simulated with slightly darker border
    pdf.set_draw_color(180, 180, 180)
    pdf.set_line_width(0.5)
    pdf.rect(x, y, card_w, card_h, 'D')

    # Top colored strip
    strip_h = 4
    darker = tuple(max(0, c - 40) for c in bg)
    pdf.set_fill_color(*darker)
    pdf.rect(x + 0.3, y + 0.3, card_w - 0.6, strip_h, 'F')

    # Word number (small, top-right)
    pdf.set_font('DejaVu', '', 8)
    pdf.set_text_color(160, 160, 160)
    pdf.set_xy(x + card_w - 12, y + strip_h + 1)
    pdf.cell(10, 5, f'#{idx+1}', align='R')

    # Word (bold, large)
    pdf.set_font('DejaVu', 'B', 22)
    pdf.set_text_color(40, 40, 40)
    pdf.set_xy(x, y + card_h * 0.22)
    pdf.cell(card_w, 12, word, align='C')

    # Phonetic
    pdf.set_font('DejaVu', '', 12)
    pdf.set_text_color(120, 120, 120)
    pdf.set_xy(x, y + card_h * 0.48)
    pdf.cell(card_w, 8, phonetic, align='C')

    # Meaning
    pdf.set_font('SimHei', '', 15)
    pdf.set_text_color(60, 60, 60)
    pdf.set_xy(x, y + card_h * 0.68)
    pdf.cell(card_w, 10, meaning, align='C')

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '单词卡片.pdf')
pdf.output(out_path)
total_pages = (len(words) + cols * rows - 1) // (cols * rows)
print(f'OK: {os.path.basename(out_path)} ({os.path.getsize(out_path)} bytes)')
print(f'{len(words)} cards on {total_pages} page(s).')
