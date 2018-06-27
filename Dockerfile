FROM python:3.6.4-slim-jessie

WORKDIR /stage/nsp-visionary
COPY * ./
ENTRYPOINT ["/stage/nsp-visionary/visionary.py"]

RUN pip install pipenv && pipenv install --system
RUN apt update && apt install wget -fy
RUN wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
RUN dpkg -i google-chrome-stable_current_amd64.deb; apt-get -fy install
COPY chromedriver /usr/bin/
