FROM python:3.12.7-slim
ARG STARROCKS_COMMIT=4a9848edf03f5c936dac664b2d52527f48e72eb0
ARG SERVICE_DEF_SHA256=0266f53186c2353d4a93eec0da9ca59e42a08f2d85885702697b693d85fbb097
RUN python -c "import hashlib,urllib.request; u='https://raw.githubusercontent.com/StarRocks/starrocks/${STARROCKS_COMMIT}/conf/ranger/ranger-servicedef-starrocks.json'; b=urllib.request.urlopen(u, timeout=30).read(); assert hashlib.sha256(b).hexdigest() == '${SERVICE_DEF_SHA256}'; open('/starrocks-service-def.json','wb').write(b)"
COPY docker/ranger/bootstrap.py /bootstrap.py
ENTRYPOINT ["python", "/bootstrap.py"]
