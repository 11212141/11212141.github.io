from pathlib import Path

from PIL import Image, ImageDraw


pages = sorted(
    Path("tmp/rendered_final").glob("page-*.png"),
    key=lambda path: int(path.stem.split("-")[1]),
)[:17]
tiles = []
for path in pages:
    page = Image.open(path).convert("RGB")
    page.thumbnail((260, 340))
    tile = Image.new("RGB", (280, 380), "white")
    tile.paste(page, ((280 - page.width) // 2, 10))
    ImageDraw.Draw(tile).text((10, 355), path.stem, fill="black")
    tiles.append(tile)

sheet = Image.new("RGB", (1120, 1900), "white")
for index, tile in enumerate(tiles):
    sheet.paste(tile, ((index % 4) * 280, (index // 4) * 380))
sheet.save("tmp/rendered_final/contact-1-17.png")
