FROM python:3.11-slim

# Один поток везде: на Render Free всего 0.1 CPU. YOLO_AUTOINSTALL=False - никаких pip install "на лету".
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    YOLO_CONFIG_DIR=/tmp/Ultralytics \
    YOLO_AUTOINSTALL=False \
    YOLO_VERBOSE=False \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    PORT=10000

# ffmpeg - для H.264-видео с рамками; libgl1 и libglib2.0-0 - для opencv-python.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

# Сначала CPU-сборка PyTorch той же версии, что в requirements.txt (сборка с PyPI тянет CUDA, ~3 ГБ).
# Строка torch==2.9.1 в requirements.txt остаётся удовлетворённой: 2.9.1+cpu подходит под ==2.9.1.
RUN pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

# Веса кладём в образ при сборке: холодный старт не качает их заново.
RUN mkdir -p models && python -c "from ultralytics import YOLO; YOLO('models/yolov8n.pt')"

COPY . .

EXPOSE 10000
# Один воркер: обработка идёт в отдельном подпроцессе, веб-процесс лёгкий (torch в нём не импортируется).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
