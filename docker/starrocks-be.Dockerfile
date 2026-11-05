# StarRocks BE with the Nova Catalog JDBC drivers (phase 2).
#
# A JDBC catalog scans on every BE, which loads the driver from the catalog's
# `driver_url`. Nova points that at file:///opt/starrocks/drivers/<jar>, the same
# path as in the FE image, so nothing is downloaded when a catalog is created or
# queried. The jars come from the pinned upstream FE image, byte for byte the
# drivers the FE uses (PostgreSQL 42.7.12, MariaDB Connector/J 3.3.2 for MySQL).
ARG STARROCKS_VERSION=4.1.4
FROM starrocks/fe-ubuntu:${STARROCKS_VERSION} AS drivers

FROM starrocks/be-ubuntu:${STARROCKS_VERSION}
LABEL org.opencontainers.image.title="Nova StarRocks BE"
LABEL org.opencontainers.image.version="4.1.4-jdbc-drivers"
COPY --from=drivers /opt/starrocks/fe/lib/postgresql-42.7.12.jar \
     /opt/starrocks/fe/lib/mariadb-java-client-3.3.2.jar /opt/starrocks/drivers/
