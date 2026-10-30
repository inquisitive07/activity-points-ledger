"""OCR health check: render a small sample point list as an image, run PaddleOCR on it, print what was read.

    docker compose run --rm --no-deps api python scripts/ocr_smoke.py
"""
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LINES = ["RoboWars 2026 - Robotics Club - Activity Points List",
         "USN          NAME              POINTS",
         "1VT22CS001   Aarav Sharma      10",
         "1VT22CS014   Diya Nair         10",
         "1VT22EC008   Ishita Rao        10"]


def make_sample(path: Path):
    font = ImageFont.load_default(size=30)
    img = Image.new("RGB", (1100, 80 + 60 * len(LINES)), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(LINES):
        draw.text((40, 40 + 60 * i), line, fill="black", font=font)
    img.save(path)


def main():
    t0 = time.perf_counter()
    from paddleocr import PaddleOCR          # imported here so the timing shows how slow the import is
    t1 = time.perf_counter()
    ocr = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det",
                    text_recognition_model_name="en_PP-OCRv5_mobile_rec",
                    use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                    # PaddlePaddle 3.3's oneDNN (MKL-DNN) CPU backend fails on these models with
                    # "ConvertPirAttribute2RuntimeAttribute not support"; the plain CPU path works.
                    enable_mkldnn=False)
    t2 = time.perf_counter()
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "sample.png"
        make_sample(img)
        result = ocr.predict(str(img))
    t3 = time.perf_counter()
    page = result[0]
    texts, scores = page["rec_texts"], page["rec_scores"]
    print("\n--- OCR output ---")
    for text, score in zip(texts, scores):
        print(f"{score:.3f}  {text}")
    print(f"\nimport {t1 - t0:.1f}s · model load {t2 - t1:.1f}s · predict {t3 - t2:.1f}s")
    found = sum(any(u in t for t in texts) for u in ("1VT22CS001", "1VT22CS014", "1VT22EC008"))
    print(f"USNs recognised exactly: {found}/3")
    sys.exit(0 if found else 1)


if __name__ == "__main__":
    main()
