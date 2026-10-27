#!/usr/bin/env python3
"""Rebuild the README's decorative GIFs. Optional dependency: Pillow."""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ASSETS = Path(__file__).resolve().parents[1] / "assets"
FONTS = Path("/usr/share/fonts/truetype/dejavu")
BG = "#0c141e"
TEXT = "#edf4fa"
MUTED = "#92acbf"
CYAN = "#64d8cd"
AMBER = "#f1bb68"


def font(size, bold=False, mono=False):
    face = "DejaVuSansMono" if mono else "DejaVuSans"
    if bold:
        face += "-Bold"
    return ImageFont.truetype(str(FONTS / f"{face}.ttf"), size)


def rounded_path(box, radius):
    x0, y0, x1, y1 = box
    corners = [(x1-radius, y0+radius, -90), (x1-radius, y1-radius, 0),
               (x0+radius, y1-radius, 90), (x0+radius, y0+radius, 180)]
    points = []
    for index, (cx, cy, start) in enumerate(corners):
        arc = [(cx+radius*math.cos(math.radians(start+a)),
                cy+radius*math.sin(math.radians(start+a))) for a in range(0, 91, 3)]
        points.extend(arc)
        nx, ny, ns = corners[(index+1) % 4]
        target = (nx+radius*math.cos(math.radians(ns)), ny+radius*math.sin(math.radians(ns)))
        source = arc[-1]
        steps = max(1, int(math.dist(source, target)/3))
        points.extend((source[0]+(target[0]-source[0])*k/steps,
                       source[1]+(target[1]-source[1])*k/steps) for k in range(1, steps+1))
    return points


def scanner(base, path, color, *, count=56, tail_ratio=0.09):
    rgb = tuple(bytes.fromhex(color.lstrip("#")))
    frames = []
    tail = max(15, int(len(path)*tail_ratio))
    for index in range(count):
        head = int(index/count*len(path))
        frame = base.copy().convert("RGBA")
        glow = Image.new("RGBA", base.size)
        draw_glow = ImageDraw.Draw(glow)
        for part in range(12):
            begin, end = int(part*tail/12), int((part+1)*tail/12)
            points = [path[(head-tail+i) % len(path)] for i in range(begin, end+1)]
            strength = (part+1)/12
            draw_glow.line(points, fill=(*rgb, int(210*strength)), width=9)
        frame = Image.alpha_composite(frame, glow.filter(ImageFilter.GaussianBlur(4)))
        draw = ImageDraw.Draw(frame)
        for part in range(12):
            begin, end = int(part*tail/12), int((part+1)*tail/12)
            points = [path[(head-tail+i) % len(path)] for i in range(begin, end+1)]
            strength = (part+1)/12
            shade = tuple(int(20+(channel-20)*strength) for channel in rgb)
            draw.line(points, fill=(*shade, 255), width=3)
        x, y = path[head % len(path)]
        draw.ellipse((x-2, y-2, x+2, y+2), fill="#fff1e9")
        frames.append(frame.convert("RGB"))
    return frames


def save_gif(frames, name):
    # Shared palette keeps the static typography steady between animation frames.
    palette = frames[0].quantize(colors=128)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    indexed[0].save(ASSETS / name, save_all=True, append_images=indexed[1:],
                    loop=0, duration=80, disposal=1, optimize=True)


def navigation(name, label, kind, color):
    base = Image.new("RGB", (300, 80), BG)
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((5, 5, 294, 74), radius=16, fill="#132330", outline="#355064", width=2)
    draw.line((58, 22, 58, 57), fill="#355064", width=1)
    if kind == "projects":
        for x, y in ((22, 27), (37, 27), (22, 42), (37, 42)):
            draw.rounded_rectangle((x, y, x+9, y+9), radius=2, outline=color, width=2)
    elif kind == "contributions":
        draw.line((27, 26, 27, 54), fill=color, width=2)
        draw.line((27, 44, 42, 36, 42, 26), fill=color, width=2)
        for x, y in ((27, 26), (42, 26), (27, 54)):
            draw.ellipse((x-3, y-3, x+3, y+3), fill="#132330", outline=color, width=2)
    else:
        draw.arc((21, 26, 47, 53), start=35, end=300, fill=color, width=2)
        draw.polygon(((44, 24), (48, 33), (39, 30)), fill=color)
    text_font = font(16 if len(label)>18 else 19, bold=True)
    box = draw.textbbox((0, 0), label, font=text_font)
    draw.text((72, (80-(box[3]-box[1]))/2-box[1]), label, font=text_font, fill=TEXT)
    save_gif(scanner(base, rounded_path((5, 5, 294, 74), 16), color, tail_ratio=0.20), name)


def badge(draw, x, y, label, color):
    face = font(21, bold=True)
    width = int(draw.textlength(label, font=face))+48
    draw.rounded_rectangle((x, y, x+width, y+42), radius=10, fill="#162735", outline="#344c5e", width=1)
    draw.ellipse((x+13, y+17, x+21, y+25), fill=color)
    draw.text((x+29, y+7), label, font=face, fill=TEXT)
    return width


def workbench():
    base = Image.new("RGB", (1200, 730), BG)
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((7, 7, 1192, 722), radius=22, fill=BG, outline="#4a2935", width=2)
    draw.text((39, 29), "TECH / TOOLKIT", font=font(25, bold=True), fill=TEXT)
    draw.text((890, 36), "BUILD  /  TEST  /  EXPLORE", font=font(14, mono=True), fill=MUTED)
    draw.line((39, 77, 1160, 77), fill="#304556", width=1)
    rows = [
        ("01 / LANGUAGES", CYAN, ["Python", "Java", "JavaScript", "TypeScript", "C#", "Go", "HTML", "CSS", "SQL"]),
        ("02 / FRAMEWORKS", "#91c7ff", ["Spring Boot", "Node.js", "Express", "React", "Three.js"]),
        ("03 / DATA & TOOLS", "#c5a8ff", ["SQLite", "Redis", "JSON Schema", "REST APIs", "Git", "GitHub Actions", "Docker"]),
        ("04 / TESTING & LIBRARIES", "#8fe3a6", ["pytest", "JUnit", "Vitest", "Boto3", "PDFBox", "Apache POI"]),
        ("05 / AWS EXPLORATION", AMBER, ["Lambda", "SQS", "SNS", "EventBridge", "Bedrock", "DynamoDB"]),
    ]
    for index, (label, color, labels) in enumerate(rows):
        y = 97+index*104
        draw.text((40, y), label, font=font(15, mono=True), fill=color)
        x = 40
        for text in labels:
            width = int(draw.textlength(text, font=font(21, bold=True)))+48
            if x+width > 1160:
                raise ValueError(f"Badge row overflows: {label}")
            x += badge(draw, x, y+29, text, color)+9
    draw.line((39, 625, 1160, 625), fill="#304556", width=1)
    draw.text((40, 645), "LEARNING PATH", font=font(14, mono=True), fill=AMBER)
    draw.text((40, 673), "Cloud optimization  /  Serverless development  /  AI token efficiency", font=font(22), fill=TEXT)
    save_gif(scanner(base, rounded_path((7, 7, 1192, 722), 22), "#ff4058", count=72, tail_ratio=0.10), "workbench.gif")


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    navigation("button-projects.gif", "My projects", "projects", CYAN)
    navigation("button-contributions.gif", "Upstream contributions", "contributions", AMBER)
    navigation("button-sync.gif", "Activity sync", "sync", "#a9a0ff")
    workbench()
    for path in sorted(ASSETS.glob("*.gif")):
        with Image.open(path) as image:
            print(f"{path.name}: {image.width}x{image.height}, {image.n_frames} frames, {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
