import os
from PIL import Image, ImageDraw


def create_gradient_canvas(size):
    img = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        ratio = y / size
        r = int(45 + (100 - 45) * ratio)
        g = int(35 + (30 - 35) * ratio)
        b = int(140 + (220 - 140) * ratio)
        draw.line([(0, y), (size, y)], fill=(r, g, b, 255))
    return img


def draw_stylized_b(img, size):
    draw = ImageDraw.Draw(img)
    scale = size / 256.0

    def s(val):
        return int(val * scale)

    draw.rounded_rectangle([s(70), s(55), s(105), s(201)], radius=s(6), fill=(255, 255, 255, 255))
    draw.chord([s(85), s(55), s(175), s(130)], start=270, end=90, fill=(255, 255, 255, 255))
    draw.chord([s(105), s(70), s(155), s(115)], start=270, end=90, fill=(72, 32, 180, 255))
    draw.chord([s(85), s(115), s(185), s(201)], start=270, end=90, fill=(255, 255, 255, 255))
    draw.chord([s(105), s(130), s(165), s(186)], start=270, end=90, fill=(72, 32, 195, 255))
    draw.rectangle([s(100), s(55), s(115), s(70)], fill=(255, 255, 255, 255))
    draw.rectangle([s(100), s(115), s(115), s(130)], fill=(255, 255, 255, 255))
    draw.rectangle([s(100), s(186), s(115), s(201)], fill=(255, 255, 255, 255))
    return img


def generate_ico():
    sizes = [16, 32, 48, 64, 128, 256]
    images = []
    for size in sizes:
        canvas = create_gradient_canvas(size)
        icon_img = draw_stylized_b(canvas, size)
        images.append(icon_img)
    output_path = os.path.join(os.path.dirname(__file__), "icon.ico")
    images[-1].save(output_path, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[:-1])
    print(f"Icon successfully generated at: {output_path}")


if __name__ == "__main__":
    generate_ico()
