import glob
import os
import re

import pdfplumber
from PIL import Image, ImageDraw


def clean_text(text):
    text = re.sub(r"(.)\1{1,}", r"\1", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("資料圖片引用來自網路公開下載，僅作為公益教學用，不作為營利用途", "")
    return text


files = sorted(glob.glob("textbook/English/*.pdf"))
for path in files:
    with pdfplumber.open(path) as pdf:
        print(f"\n## {os.path.basename(path)} ({len(pdf.pages)} pages)")
        for index, page in enumerate(pdf.pages, 1):
            cleaned = clean_text(page.extract_text() or "")
            print(f"{index:02d}: {cleaned[:170]}")
