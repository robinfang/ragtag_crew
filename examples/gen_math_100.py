import random
import os
from fpdf import FPDF

out_dir = os.path.dirname(os.path.abspath(__file__))

for i in range(1, 21):
    random.seed(i + 100)
    pdf = FPDF('P', 'mm', 'A4')
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    pdf.set_margins(15, 10, 15)
    pdf.set_font('Helvetica', '', 16)

    col_w = 180.0 / 5
    row_h = 277.0 / 10

    for row in range(10):
        for col in range(5):
            x = 15 + col * col_w
            y = 10 + row * row_h
            op = random.choice(['+', '-'])

            if op == '+':
                # 不带进位加法: 个位之和 < 10, 总和 <= 100
                for _ in range(500):
                    a = random.randint(1, 99)
                    b = random.randint(1, 99)
                    if a + b > 100:
                        continue
                    if (a % 10) + (b % 10) >= 10:
                        continue
                    break
            else:
                # 不带借位减法: 被减数个位 >= 减数个位, 减数 >= 10, 差 > 0
                for _ in range(500):
                    a = random.randint(11, 99)
                    b = random.randint(10, 99)
                    if a <= b:
                        continue
                    if (a % 10) < (b % 10):
                        continue
                    break

            problem = f'{a} {op} {b} ='
            pdf.set_xy(x, y)
            pdf.cell(col_w, 8, problem, align='C')

    path = os.path.join(out_dir, f'math100_practice_{i}.pdf')
    pdf.output(path)
    print(f'OK: math100_practice_{i}.pdf ({os.path.getsize(path)} bytes)')

print('Done: 20 files generated.')
