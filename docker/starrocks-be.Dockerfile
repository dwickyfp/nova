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
# Snowflake JDBC driver (phase 4). The patched FE loads the same file from the same path in an
# isolated class loader; keep this pin equal to docker/ranger/starrocks-fe.Dockerfile.
ARG SNOWFLAKE_JDBC_VERSION=3.28.0
ARG SNOWFLAKE_JDBC_SHA256=41e3e99fde1aa90516448306885b345df7600db650258e7dfd0ea79c02f6cb49
RUN curl -fsSLo /opt/starrocks/drivers/snowflake-jdbc-${SNOWFLAKE_JDBC_VERSION}.jar \
       https://repo1.maven.org/maven2/net/snowflake/snowflake-jdbc/${SNOWFLAKE_JDBC_VERSION}/snowflake-jdbc-${SNOWFLAKE_JDBC_VERSION}.jar \
    && echo "${SNOWFLAKE_JDBC_SHA256}  /opt/starrocks/drivers/snowflake-jdbc-${SNOWFLAKE_JDBC_VERSION}.jar" | sha256sum -c - \
    && chmod 0644 /opt/starrocks/drivers/snowflake-jdbc-${SNOWFLAKE_JDBC_VERSION}.jar
