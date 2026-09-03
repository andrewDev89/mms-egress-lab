FROM debian:bookworm-slim AS source
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates kannel-dev libxml2-dev libssl-dev libpq-dev libtool automake && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL https://codeload.github.com/fredounnet/mbuni/tar.gz/b8054f9ddfc48a8f2ec911adabd5309472bcf9f4 -o mbuni.tar.gz \
 && echo 'af646c1aba7d1eb62b0681e6ee3a931f888ddf7fdd779385b38c3c976e390223  mbuni.tar.gz' | sha256sum -c - \
 && tar xzf mbuni.tar.gz --strip-components=1
FROM source AS build
# The bundled 2006 helpers cannot identify Linux ARM64 (Apple Silicon's container
# architecture). Use Debian's maintained helpers, installed with automake.
RUN cp /usr/share/misc/config.guess /usr/share/misc/config.sub autotools/
RUN sed -i 's/\bbool\b/boolean_value/g' mmlib/mms_cfg.c
RUN sed -i 's/\buse_global_/conn_use_global_/g' mmlib/mms_util.c
RUN ./configure --prefix=/opt/mbuni --with-kannel-dir=/usr --with-pgsql-dir=/usr --disable-ssl --disable-shared && make -j4 && make install

RUN gcc -shared -fPIC -DHAVE_CONFIG_H -I. -Immlib -I/usr/include/kannel -I/usr/include/libxml2 -I/usr/include/postgresql extras/pgsql-queue/mms_pgsql_queue.c -lpq -o /opt/mbuni/lib/libmms_pgsql_queue.so

# Keep the exact patched source and licence with the demo image.
RUN mkdir -p /opt/mbuni/share/source && tar czf /opt/mbuni/share/source/mbuni-1.6.0-patched.tar.gz --exclude=mbuni.tar.gz . \
 && cp COPYING GnuLICENSE KannelLICENSE /opt/mbuni/share/source/ \
 && mkdir /runtime-libs && ldd /opt/mbuni/bin/mmsbox /opt/mbuni/lib/libmms_pgsql_queue.so | awk '/=> \// { print $3 }' | sort -u | xargs -r cp -L -t /runtime-libs

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends gettext-base curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/mbuni /opt/mbuni
COPY --from=build /runtime-libs/ /usr/local/lib/
RUN ldconfig
COPY mbuni/mmsbox.conf.template /etc/mbuni/mmsbox.conf.template
COPY --chmod=755 mbuni/entrypoint.sh /entrypoint.sh
ENV TZ=UTC
ENTRYPOINT ["/entrypoint.sh"]
