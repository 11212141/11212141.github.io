from pathlib import Path

from PIL import Image


source = Path("tmp/web_screenshots_no_toolbar")
output = Path("tmp/web_activity_crops")
output.mkdir(parents=True, exist_ok=True)

crops = {
    "phonics": (90, 500, 1265, 1900),
    "color-mix": (90, 520, 1265, 1950),
    "this-that": (90, 360, 1265, 1220),
    "can-do": (90, 360, 1265, 1510),
    "card-match": (90, 360, 1265, 1160),
    "random-picker": (90, 360, 1265, 1300),
}

for name, box in crops.items():
    with Image.open(source / f"{name}.png") as image:
        right = min(box[2], image.width)
        bottom = min(box[3], image.height)
        image.crop((box[0], box[1], right, bottom)).save(output / f"{name}.png")
