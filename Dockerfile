FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Piper voice model, baked into the image so a run never depends on fetching it
# at start-up. Placed before `COPY . .` so editing code does not re-download it.
ENV PIPER_VOICE_DIR=/app/voices
ADD https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx /app/voices/en_US-ryan-medium.onnx
ADD https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json /app/voices/en_US-ryan-medium.onnx.json
RUN chmod 644 /app/voices/*

COPY . .

CMD ["python", "app.py"]
