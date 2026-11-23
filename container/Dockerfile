FROM ghcr.io/microsoft/garnet:2.2.13@sha256:a5b11ca8292400a6239437c34c96aaa515a55ab3613ef2e41a33914405f89442
USER root
RUN install -d -m 0700 -o app -g app /opt/garnet-runtime
COPY start-garnet.sh wait-garnet.sh /usr/local/bin/
USER app
WORKDIR /opt/garnet-runtime
EXPOSE 6379
ENTRYPOINT ["/bin/sh", "/usr/local/bin/start-garnet.sh"]
