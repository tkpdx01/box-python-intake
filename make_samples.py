"""Generate synthetic sample JPGs with EXIF for demo runs."""
from PIL import Image
import piexif
from datetime import datetime, timedelta
import os

SHOOTS = [
    ("2026-04-20", "hincapie_spring_ride",   "Canon EOS R5"),
    ("2026-04-22", "hincapie_merino_sprint", "Sony ILCE-7M4"),
    ("2026-04-23", "hincapie_merino_sprint", "Sony ILCE-7M4"),
]

def main():
    os.makedirs("samples", exist_ok=True)
    for i, (d, shoot, cam) in enumerate(SHOOTS, 1):
        for j in range(1, 4):
            img = Image.new("RGB", (600, 400), (50 + i * 40, 90 + j * 30, 140))
            dt = datetime.fromisoformat(d) + timedelta(hours=10, minutes=j * 5 + i)
            exif = {
                "0th": {
                    piexif.ImageIFD.Make: (b"Canon" if "Canon" in cam else b"SONY"),
                    piexif.ImageIFD.Model: cam.encode(),
                    piexif.ImageIFD.DateTime: dt.strftime("%Y:%m:%d %H:%M:%S").encode(),
                    piexif.ImageIFD.Artist: b"Hincapie Photography",
                },
                "Exif": {
                    piexif.ExifIFD.DateTimeOriginal: dt.strftime("%Y:%m:%d %H:%M:%S").encode(),
                    piexif.ExifIFD.DateTimeDigitized: dt.strftime("%Y:%m:%d %H:%M:%S").encode(),
                    piexif.ExifIFD.FNumber: (28, 10),
                    piexif.ExifIFD.ISOSpeedRatings: 400,
                },
            }
            exif_bytes = piexif.dump(exif)
            fname = f"samples/IMG_{1000 + i * 10 + j:04d}.jpg"
            img.save(fname, "jpeg", exif=exif_bytes, quality=70)
            print(f"wrote {fname}  date={dt}  cam={cam}  shoot={shoot}")

if __name__ == "__main__":
    main()
