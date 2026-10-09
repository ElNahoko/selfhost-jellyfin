# Turns picture subtitles (PGS, from Blu-rays) into text .srt files next to each video.
# TVs and phones draw text subtitles themselves; picture subtitles force Jellyfin to re-encode the whole video.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra mkvtoolnix  && rm -rf /var/lib/apt/lists/*  && pip install --no-cache-dir pgsrip && pip uninstall -y opencv-python && pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless
COPY run.py /run.py
ENTRYPOINT ["python", "/run.py"]
