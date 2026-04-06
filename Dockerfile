FROM python:3.13-slim

WORKDIR /app

# Instalar dependências + tesseract + poppler
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-por \
    poppler-utils \
    libgl1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Garante que o tesseract esteja no PATH
ENV PATH="/usr/bin:${PATH}"

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "10000"]
