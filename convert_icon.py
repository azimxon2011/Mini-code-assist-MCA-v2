from PIL import Image
import os

def convert_to_ico(png_path, ico_path):
    img = Image.open(png_path)
    # Convert to RGBA if not already
    img = img.convert("RGBA")
    # Resize to standard icon sizes
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_path, format="ICO", sizes=sizes)
    print(f"Icon saved to {ico_path}")

if __name__ == "__main__":
    convert_to_ico("python_logo.png", "python_icon.ico")
