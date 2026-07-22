FROM python:3.12.10-slim
RUN pip install --no-cache-dir pytest==8.4.1
COPY main.py /opt/sandbox/main.py
USER 65532:65532
ENTRYPOINT ["python", "/opt/sandbox/main.py"]