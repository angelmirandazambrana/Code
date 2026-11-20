FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    BIBLIOTECA_RAIZ=/biblioteca \
    BIBLIOTECA_DATOS=/datos

# bsdtar (libarchive): descomprimir zip, rar y 7z de la carpeta de entrada.
# 7-Zip: lo que bsdtar no sabe abrir (zip con Deflate64, p. ej.).
RUN apt-get update     && apt-get install -y --no-install-recommends libarchive-tools 7zip     && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY docker ./docker
RUN chmod +x docker/*.sh

# Usuario sin privilegios; el UID/GID se ajusta en docker-compose (user:) para que coincida con OMV.
USER 1000:100
EXPOSE 8090
CMD ["uvicorn", "biblioteca_rpg.web:app", "--host", "0.0.0.0", "--port", "8090", "--proxy-headers"]
