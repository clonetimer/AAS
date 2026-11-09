"""Generate the AAS application icon in PNG, ICO and editable SVG formats."""
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "web" / "visualization"
SIZE = 1024
NAVY = "#10243A"
TEAL = "#35D7C6"
WHITE = "#F4FAFF"
AMBER = "#FFB35A"


def draw_png() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, 984, 984), radius=206, fill=NAVY)

    # Solar panels and their two short links form a clear satellite silhouette.
    draw.rounded_rectangle((170, 416, 350, 608), radius=25, fill=TEAL)
    draw.rounded_rectangle((674, 416, 854, 608), radius=25, fill=TEAL)
    draw.rectangle((350, 492, 408, 532), fill=WHITE)
    draw.rectangle((616, 492, 674, 532), fill=WHITE)
    for left in (170, 674):
        draw.line((left + 90, 432, left + 90, 592), fill=NAVY, width=12)
        draw.line((left + 16, 512, left + 164, 512), fill=NAVY, width=12)

    draw.rounded_rectangle((394, 388, 630, 636), radius=51, fill=WHITE)
    points = [(420, 516), (457, 516), (480, 472), (512, 559),
              (545, 492), (566, 516), (604, 516)]
    draw.line(points, fill=NAVY, width=21, joint="curve")
    for point in (points[0], points[-1]):
        x, y = point
        draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill=NAVY)

    # The amber dot is the fault signal, separate from the measurement trace.
    draw.ellipse((600, 268, 742, 410), fill=AMBER)
    draw.rounded_rectangle((662, 297, 680, 352), radius=9, fill=NAVY)
    draw.ellipse((662, 367, 680, 385), fill=NAVY)
    return image


def write_svg() -> None:
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" aria-labelledby="title desc">
<title id="title">AAS 卫星遥测故障检测</title>
<desc id="desc">深蓝色方形图标，青色卫星太阳翼、白色遥测波形主体与琥珀色异常提示点。</desc>
<rect x="40" y="40" width="944" height="944" rx="206" fill="{NAVY}"/>
<rect x="170" y="416" width="180" height="192" rx="25" fill="{TEAL}"/>
<rect x="674" y="416" width="180" height="192" rx="25" fill="{TEAL}"/>
<path d="M350 492h58v40h-58zm266 0h58v40h-58z" fill="{WHITE}"/>
<path d="M260 432v160m-74-80h148m430-80v160m-74-80h148" stroke="{NAVY}" stroke-width="12"/>
<rect x="394" y="388" width="236" height="248" rx="51" fill="{WHITE}"/>
<path d="M420 516h37l23-44 32 87 33-67 21 24h38" fill="none" stroke="{NAVY}" stroke-width="21" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="671" cy="339" r="71" fill="{AMBER}"/>
<rect x="662" y="297" width="18" height="55" rx="9" fill="{NAVY}"/>
<circle cx="671" cy="376" r="9" fill="{NAVY}"/>
</svg>'''
    (DEST / "aas-icon.svg").write_text(svg, encoding="utf-8")


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    image = draw_png()
    image.resize((512, 512), Image.Resampling.LANCZOS).save(DEST / "aas-icon.png")
    image.save(DEST / "favicon.ico", format="ICO",
               sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                      (64, 64), (128, 128), (256, 256)])
    write_svg()


if __name__ == "__main__":
    main()
