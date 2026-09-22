FROM python:3.12.7-slim

WORKDIR /opt/nova-ranger-proxy
COPY docker/ranger/policy_proxy.py ./policy_proxy.py

EXPOSE 6081
USER nobody
CMD ["python", "policy_proxy.py"]
