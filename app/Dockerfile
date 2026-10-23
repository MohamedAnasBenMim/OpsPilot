FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY orders ./orders
COPY loadgen.py ./
RUN useradd --create-home --uid 1000 app
USER app
EXPOSE 8000
CMD ["uvicorn", "orders.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
