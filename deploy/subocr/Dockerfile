# Turns picture subtitles (PGS, from Blu-rays) into text .srt files next to each video.
# TVs and phones draw text subtitles themselves; picture subtitles force Jellyfin to re-encode the whole video.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-fra mkvtoolnix \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir pgsrip && pip uninstall -y opencv-python \
 && pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless
# pgsrip glues every subtitle bitmap of a track into composite images up to 31,744 px on each side before OCR.
# For a feature film that is one enormous image and tesseract needs ~900 MB. patch_pgsrip.py caps composites
# at 4,096 px: the same text comes out, read in smaller pieces.
COPY patch_pgsrip.py /patch_pgsrip.py
RUN python /patch_pgsrip.py
COPY stream_ocr.py /stream_ocr.py
COPY run.py /run.py
ENTRYPOINT ["python", "/run.py"]
