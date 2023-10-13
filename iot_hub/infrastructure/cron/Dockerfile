FROM alpine

ADD crontab.txt /crontab.txt
ADD scripts/* /scripts/

COPY entry.sh /entry.sh

RUN chmod  755 /entry.sh
RUN chmod -R 755 /scripts
RUN /usr/bin/crontab /crontab.txt

CMD ["/entry.sh"]