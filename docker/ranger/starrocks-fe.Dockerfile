ARG STARROCKS_VERSION=4.1.4
FROM starrocks/fe-ubuntu:${STARROCKS_VERSION} AS upstream

FROM eclipse-temurin:17.0.12_7-jdk-jammy AS patcher
ARG STARROCKS_COMMIT=4a9848edf03f5c936dac664b2d52527f48e72eb0
ARG NASHORN_VERSION=15.4
ARG NASHORN_SHA256=6f816e84dfd63a81d4eaa7829c08337bbaff3ec683ff3bf6bbd90d017a00dc6f
ARG ASM_COMMONS_VERSION=9.4
ARG ASM_COMMONS_SHA256=0c128a9ec3f33c98959272f6d16cf14247b508f58951574bcdbd2b56d6326364
# Keep the Snowflake driver pin equal to docker/starrocks-be.Dockerfile: FE and BE load the same file.
ARG SNOWFLAKE_JDBC_VERSION=3.28.0
ARG SNOWFLAKE_JDBC_SHA256=41e3e99fde1aa90516448306885b345df7600db650258e7dfd0ea79c02f6cb49
ARG JUNIT_CONSOLE_VERSION=1.11.4
ARG JUNIT_CONSOLE_SHA256=b016ef6b1c3454d6d7c2c88ce081dabf289699686af6622d6e4e2e1b54b4a2fc
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*
RUN git init /src \
    && git -C /src remote add origin https://github.com/StarRocks/starrocks.git \
    && git -C /src sparse-checkout init --no-cone \
    && git -C /src sparse-checkout set \
       /conf/ranger/ranger-starrocks-security.xml \
       /fe/fe-core/src/main/java/com/starrocks/authorization/ranger/RangerAccessController.java \
       /fe/fe-core/src/main/java/com/starrocks/authorization/ranger/starrocks/RangerStarRocksAccessController.java \
       /fe/fe-core/src/main/java/com/starrocks/authorization/ranger/RangerStarRocksAccessRequest.java \
       /fe/fe-core/src/test/java/com/starrocks/authorization/ranger/RangerInterfaceTest.java \
       /fe/fe-core/src/test/resources/ranger-starrocks-security.xml \
    && git -C /src fetch --filter=blob:none --depth 1 origin ${STARROCKS_COMMIT} \
    && git -C /src checkout FETCH_HEAD
COPY patches/starrocks/4.1.4-ranger-active-role.patch /tmp/ranger-active-role.patch
RUN git -C /src apply --check /tmp/ranger-active-role.patch \
    && git -C /src apply /tmp/ranger-active-role.patch
COPY patches/starrocks/4.1.4-ranger-rpc-context.patch /tmp/ranger-rpc-context.patch
RUN git -C /src apply --check /tmp/ranger-rpc-context.patch \
    && git -C /src apply /tmp/ranger-rpc-context.patch
RUN git -C /src sparse-checkout add /fe/fe-core/src/main/java/com/starrocks/scheduler/TaskRun.java \
    /fe/fe-core/src/main/java/com/starrocks/scheduler/TaskManager.java
COPY patches/starrocks/4.1.4-task-active-role.patch /tmp/task-active-role.patch
RUN git -C /src apply --check /tmp/task-active-role.patch \
    && git -C /src apply /tmp/task-active-role.patch
RUN git -C /src sparse-checkout add /fe/fe-core/src/main/java/com/starrocks/connector/jdbc/JDBCMetadata.java \
    /fe/fe-core/src/main/java/com/starrocks/connector/jdbc/JDBCDriverClassLoaders.java \
    /fe/fe-core/src/main/java/com/starrocks/connector/jdbc/SnowflakeSchemaResolver.java \
    /fe/fe-core/src/main/java/com/starrocks/planner/JDBCScanNode.java \
    /fe/fe-core/src/test/java/com/starrocks/connector/jdbc/SnowflakeSchemaResolverTest.java
COPY patches/starrocks/4.1.4-jdbc-snowflake.patch /tmp/jdbc-snowflake.patch
RUN git -C /src apply --check /tmp/jdbc-snowflake.patch \
    && git -C /src apply /tmp/jdbc-snowflake.patch
COPY --from=upstream /opt/starrocks/fe/lib /opt/starrocks/fe/lib
RUN curl -fsSLo /opt/starrocks/fe/lib/nashorn-core-${NASHORN_VERSION}.jar \
       https://repo1.maven.org/maven2/org/openjdk/nashorn/nashorn-core/${NASHORN_VERSION}/nashorn-core-${NASHORN_VERSION}.jar \
    && echo "${NASHORN_SHA256}  /opt/starrocks/fe/lib/nashorn-core-${NASHORN_VERSION}.jar" | sha256sum -c - \
    && curl -fsSLo /opt/starrocks/fe/lib/asm-commons-${ASM_COMMONS_VERSION}.jar \
       https://repo1.maven.org/maven2/org/ow2/asm/asm-commons/${ASM_COMMONS_VERSION}/asm-commons-${ASM_COMMONS_VERSION}.jar \
    && echo "${ASM_COMMONS_SHA256}  /opt/starrocks/fe/lib/asm-commons-${ASM_COMMONS_VERSION}.jar" | sha256sum -c -
# The Snowflake driver stays off fe/lib: it bundles unshaded zstd-jni that would shadow the FE copy.
# JDBCMetadata loads it in an isolated class loader from this file:// path, and the BE image has the
# same file at the same path for the JDBC scanner.
RUN mkdir -p /opt/starrocks/drivers \
    && curl -fsSLo /opt/starrocks/drivers/snowflake-jdbc-${SNOWFLAKE_JDBC_VERSION}.jar \
       https://repo1.maven.org/maven2/net/snowflake/snowflake-jdbc/${SNOWFLAKE_JDBC_VERSION}/snowflake-jdbc-${SNOWFLAKE_JDBC_VERSION}.jar \
    && echo "${SNOWFLAKE_JDBC_SHA256}  /opt/starrocks/drivers/snowflake-jdbc-${SNOWFLAKE_JDBC_VERSION}.jar" | sha256sum -c - \
    && curl -fsSLo /tmp/junit-console.jar \
       https://repo1.maven.org/maven2/org/junit/platform/junit-platform-console-standalone/${JUNIT_CONSOLE_VERSION}/junit-platform-console-standalone-${JUNIT_CONSOLE_VERSION}.jar \
    && echo "${JUNIT_CONSOLE_SHA256}  /tmp/junit-console.jar" | sha256sum -c -
RUN mkdir -p /tmp/classes \
    && javac -encoding UTF-8 -cp '/opt/starrocks/fe/lib/*' -d /tmp/classes \
       /src/fe/fe-core/src/main/java/com/starrocks/authorization/ranger/RangerStarRocksAccessRequest.java \
       /src/fe/fe-core/src/main/java/com/starrocks/authorization/ranger/RangerAccessController.java \
       /src/fe/fe-core/src/main/java/com/starrocks/authorization/ranger/starrocks/RangerStarRocksAccessController.java \
       /src/fe/fe-core/src/main/java/com/starrocks/scheduler/TaskRun.java \
       /src/fe/fe-core/src/main/java/com/starrocks/scheduler/TaskManager.java \
       /src/fe/fe-core/src/main/java/com/starrocks/connector/jdbc/JDBCMetadata.java \
       /src/fe/fe-core/src/main/java/com/starrocks/connector/jdbc/JDBCDriverClassLoaders.java \
       /src/fe/fe-core/src/main/java/com/starrocks/connector/jdbc/SnowflakeSchemaResolver.java \
       /src/fe/fe-core/src/main/java/com/starrocks/planner/JDBCScanNode.java
# Run the Snowflake JDBC patch tests against the compiled classes and the runtime FE libraries.
RUN mkdir -p /tmp/test-classes \
    && javac -encoding UTF-8 -cp "/tmp/classes:/opt/starrocks/fe/lib/*:/tmp/junit-console.jar" -d /tmp/test-classes \
       /src/fe/fe-core/src/test/java/com/starrocks/connector/jdbc/SnowflakeSchemaResolverTest.java \
    && java -jar /tmp/junit-console.jar execute --disable-banner --details=summary --fail-if-no-tests \
       -cp "/tmp/test-classes:/tmp/classes:$(ls /opt/starrocks/fe/lib/*.jar | tr '\n' ':')" \
       --select-class com.starrocks.connector.jdbc.SnowflakeSchemaResolverTest
RUN jar uf /opt/starrocks/fe/lib/fe-core-4.1.4.jar -C /tmp/classes com/starrocks/authorization/ranger \
       -C /tmp/classes com/starrocks/scheduler \
       -C /tmp/classes com/starrocks/connector/jdbc \
       -C /tmp/classes com/starrocks/planner

FROM starrocks/fe-ubuntu:${STARROCKS_VERSION}
LABEL org.opencontainers.image.title="Nova patched StarRocks FE"
LABEL org.opencontainers.image.version="4.1.4-ranger-active-role-jdbc-snowflake"
LABEL org.opencontainers.image.revision="4a9848edf03f5c936dac664b2d52527f48e72eb0"
COPY --from=patcher /opt/starrocks/fe/lib/fe-core-4.1.4.jar /opt/starrocks/fe/lib/fe-core-4.1.4.jar
COPY --from=patcher /opt/starrocks/fe/lib/nashorn-core-15.4.jar /opt/starrocks/fe/lib/nashorn-core-15.4.jar
COPY --from=patcher /opt/starrocks/fe/lib/asm-commons-9.4.jar /opt/starrocks/fe/lib/asm-commons-9.4.jar
# Nova Catalog JDBC drivers (phase 2). A JDBC catalog's `driver_url` must name the
# same file:// path on the FE and every BE, and is never downloaded at create
# time. The upstream FE image already ships these two drivers in fe/lib; the
# BE image (docker/starrocks-be.Dockerfile) copies the same files from it.
RUN mkdir -p /opt/starrocks/drivers \
    && cp /opt/starrocks/fe/lib/postgresql-42.7.12.jar \
          /opt/starrocks/fe/lib/mariadb-java-client-3.3.2.jar /opt/starrocks/drivers/
COPY --from=patcher /opt/starrocks/drivers/snowflake-jdbc-3.28.0.jar /opt/starrocks/drivers/snowflake-jdbc-3.28.0.jar
