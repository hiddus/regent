FROM python:3.12.10-slim
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY main.py /opt/resolver/main.py
USER 65532:65532
ENTRYPOINT ["python", "/opt/resolver/main.py"]