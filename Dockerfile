# IoT Center Python services: the Lite edition, or the platform's gateway, web app and
# Streamlit analytics. EXTRAS picks the dependencies (see pyproject.toml):
#   docker build --build-arg EXTRAS=lite .                 Lite edition (default)
#   docker build --build-arg EXTRAS=lite,camera .          Lite with a USB webcam (OpenCV)
#   docker build --build-arg EXTRAS=platform,analytics .   platform services
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
ARG EXTRAS=lite

# 1) Dependencies in their own layer, so editing code doesn't reinstall them: install the
#    project from a placeholder package, then remove just the placeholder.
COPY pyproject.toml README.md ./
RUN mkdir -p src/iotcenter \
 && touch src/iotcenter/__init__.py \
 && pip install ".[${EXTRAS}]" \
 && pip uninstall -y iotcenter \
 && rm -rf src

# The camera's microphone is read with arecord (alsa-utils): only in images with the camera.
RUN case ",${EXTRAS}," in *,camera,*) \
      apt-get update && apt-get install -y --no-install-recommends alsa-utils \
      && rm -rf /var/lib/apt/lists/* ;; \
    esac

# 2) The code itself.
COPY src ./src
RUN pip install --no-deps . && rm -rf build

# Run as an unprivileged user; /data holds the Lite SQLite database (mount a volume there).
# dialout opens a Pico W on USB, video a webcam and audio its microphone (the same group ids
# as on Debian and Ubuntu hosts).
RUN useradd --create-home --uid 1000 --groups dialout,video,audio iot \
 && mkdir -p /data && chown iot:iot /data
USER iot
ENV IOT_DB_PATH=/data/iot-lite.db

EXPOSE 8000 1500
CMD ["iotcenter", "lite"]
