import os
import io
import random
from PIL import Image, ImageOps, ImageDraw
from constants import VINYL_SIZE, CENTER_HOLE

def generate_vinyl_image(artwork_bytes):
    try:
        if artwork_bytes:
            input_image = Image.open(io.BytesIO(artwork_bytes)).convert("RGBA")
        else:
            raise ValueError("No artwork provided")
    except Exception:
        try:
            default_path = os.path.join(os.getcwd(), "app_states", "default.png")
            input_image = Image.open(default_path).convert("RGBA")
        except Exception:
            input_image = Image.new("RGBA", (VINYL_SIZE, VINYL_SIZE), (40, 40, 40, 255))
            draw = ImageDraw.Draw(input_image)
            draw.rectangle((0, 0, VINYL_SIZE, VINYL_SIZE), fill=(30, 144, 255, 255))
            draw.ellipse((VINYL_SIZE*0.15, VINYL_SIZE*0.15, VINYL_SIZE*0.85, VINYL_SIZE*0.85), fill=(255, 165, 0, 255))
            draw.ellipse((VINYL_SIZE*0.3, VINYL_SIZE*0.3, VINYL_SIZE*0.7, VINYL_SIZE*0.7), fill=(255, 69, 0, 255))
            draw.ellipse((VINYL_SIZE*0.45, VINYL_SIZE*0.45, VINYL_SIZE*0.55, VINYL_SIZE*0.55), fill=(255, 215, 0, 255))

    sticker_size = int(VINYL_SIZE * 0.75)
    center_f = VINYL_SIZE // 2
    
    final_record = Image.new("RGBA", (VINYL_SIZE, VINYL_SIZE), (0, 0, 0, 0))
    draw_r = ImageDraw.Draw(final_record)
    
    draw_r.ellipse((0, 0, VINYL_SIZE, VINYL_SIZE), fill=(20, 20, 20, 255), outline=(5, 5, 5, 255), width=2)
    
    for r in range(VINYL_SIZE // 2, sticker_size // 2, -10):
        alpha = int(random.uniform(10, 25))
        color = (100, 100, 100, alpha)
        draw_r.ellipse((center_f - r, center_f - r, center_f + r, center_f + r), outline=color, width=1)

    sticker_raw = ImageOps.fit(input_image, (sticker_size, sticker_size), Image.Resampling.LANCZOS)
    sticker_pil = sticker_raw.convert("RGBA")
    
    mask = Image.new('L', (sticker_size, sticker_size), 0)
    draw_m = ImageDraw.Draw(mask)
    draw_m.ellipse((0, 0, sticker_size, sticker_size), fill=255)
    
    sticker_final = ImageOps.fit(sticker_pil, (sticker_size, sticker_size))
    sticker_final.putalpha(mask)
    
    offset = (VINYL_SIZE - sticker_size) // 2
    final_record.paste(sticker_final, (offset, offset), sticker_final)
    
    draw_r.ellipse((center_f - CENTER_HOLE//2, center_f - CENTER_HOLE//2, 
                    center_f + CENTER_HOLE//2, center_f + CENTER_HOLE//2), fill=(10,10,10,255), outline=(0,0,0,255), width=1)
    
    return final_record