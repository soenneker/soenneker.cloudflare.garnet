FROM ghcr.io/microsoft/garnet:2.2.14@sha256:9ea4c7f379e0366e5647c94ddbe17710d3aba434a92d974658dd90e4a3efc4ed
USER root
RUN install -d -m 0700 -o app -g app /opt/garnet-runtime
COPY start-garnet.sh wait-garnet.sh /usr/local/bin/
USER app
WORKDIR /opt/garnet-runtime
EXPOSE 6379
ENTRYPOINT ["/bin/sh", "/usr/local/bin/start-garnet.sh"]
