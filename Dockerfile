FROM python:3.13

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN echo "test"

COPY main.py .

EXPOSE 8080

CMD ["python", "main.py"]
