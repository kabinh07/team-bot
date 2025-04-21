FROM python:3.11-slim

WORKDIR /app

ENV TZ=Asia/Dhaka
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "main.py"]