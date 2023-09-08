FROM python:3.8

WORKDIR /usr/src/app

COPY scripts/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/listen_tcp.py .

ENTRYPOINT [ "python", "./listen_tcp.py" ]