ARG STARROCKS_VERSION=4.1.4
FROM starrocks/fe-ubuntu:${STARROCKS_VERSION} AS upstream

FROM eclipse-temurin:17.0.12_7-jdk-jammy AS patcher
ARG STARROCKS_COMMIT=4a9848edf03f5c936dac664b2d52527f48e72eb0
ARG NASHORN_VERSION=15.4
ARG NASHORN_SHA256=6f816e84dfd63a81d4eaa7829c08337bbaff3ec683ff3bf6bbd90d017a00dc6f
ARG ASM_COMMONS_VERSION=9.4
ARG ASM_COMMONS_SHA256=0c128a9ec3f33c98959272f6d16cf14247b508f58951574bcdbd2b56d6326364
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
COPY --from=upstream /opt/starrocks/fe/lib /opt/starrocks/fe/lib
RUN curl -fsSLo /opt/starrocks/fe/lib/nashorn-core-${NASHORN_VERSION}.jar \
       https://repo1.maven.org/maven2/org/openjdk/nashorn/nashorn-core/${NASHORN_VERSION}/nashorn-core-${NASHORN_VERSION}.jar \
    && echo "${NASHORN_SHA256}  /opt/starrocks/fe/lib/nashorn-core-${NASHORN_VERSION}.jar" | sha256sum -c - \
    && curl -fsSLo /opt/starrocks/fe/lib/asm-commons-${ASM_COMMONS_VERSION}.jar \
       https://repo1.maven.org/maven2/org/ow2/asm/asm-commons/${ASM_COMMONS_VERSION}/asm-commons-${ASM_COMMONS_VERSION}.jar \
    && echo "${ASM_COMMONS_SHA256}  /opt/starrocks/fe/lib/asm-commons-${ASM_COMMONS_VERSION}.jar" | sha256sum -c -
RUN mkdir -p /tmp/classes \
    && javac -encoding UTF-8 -cp '/opt/starrocks/fe/lib/*' -d /tmp/classes \
       /src/fe/fe-core/src/main/java/com/starrocks/authorization/ranger/RangerStarRocksAccessRequest.java \
       /src/fe/fe-core/src/main/java/com/starrocks/authorization/ranger/RangerAccessController.java \
       /src/fe/fe-core/src/main/java/com/starrocks/authorization/ranger/starrocks/RangerStarRocksAccessController.java \
       /src/fe/fe-core/src/main/java/com/starrocks/scheduler/TaskRun.java \
       /src/fe/fe-core/src/main/java/com/starrocks/scheduler/TaskManager.java \
    && jar uf /opt/starrocks/fe/lib/fe-core-4.1.4.jar -C /tmp/classes com/starrocks/authorization/ranger \
       -C /tmp/classes com/starrocks/scheduler

FROM starrocks/fe-ubuntu:${STARROCKS_VERSION}
LABEL org.opencontainers.image.title="Nova patched StarRocks FE"
LABEL org.opencontainers.image.version="4.1.4-ranger-active-role"
LABEL org.opencontainers.image.revision="4a9848edf03f5c936dac664b2d52527f48e72eb0"
COPY --from=patcher /opt/starrocks/fe/lib/fe-core-4.1.4.jar /opt/starrocks/fe/lib/fe-core-4.1.4.jar
COPY --from=patcher /opt/starrocks/fe/lib/nashorn-core-15.4.jar /opt/starrocks/fe/lib/nashorn-core-15.4.jar
COPY --from=patcher /opt/starrocks/fe/lib/asm-commons-9.4.jar /opt/starrocks/fe/lib/asm-commons-9.4.jar
