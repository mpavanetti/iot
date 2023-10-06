FROM python:3.8

USER root

WORKDIR /python-docker

RUN apt-get update -y && apt-get install -y iputils-ping

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY . .

CMD [ "python3", "-m" , "flask", "run", "--host=0.0.0.0","--port=80"]