FROM python:3.8-slim

USER root

WORKDIR /usr/src/app

RUN apt-get update
RUN apt-get install gcc -y
RUN apt-get install pkg-config -y
RUN apt-get install libmariadb-dev -y

COPY . .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get install libmariadb-dev

ENTRYPOINT [ "streamlit", "run" , "./dashboard.py" ]
