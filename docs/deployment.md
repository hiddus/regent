# Deployment

## Current S0 deployment

- Host: `118.31.171.159`
- Install directory: `/opt/regent`
- API container: `regent-api`
- Worker container: `regent-worker`
- Database container: `regent-postgres`
- Docker network: `regent-net`
- Database volume: `regent-postgres-data`
- API port: TCP 8000

PostgreSQL is not published on a host port. Its randomly generated password is
stored on the server in `/opt/regent/.deploy.env` with owner-only permissions.

## Health checks

```text
GET /health/live
GET /health/ready
```

## Cloud firewall

The host firewall allows TCP 8000. Alibaba Cloud Security Group or Cloud Firewall
must also allow inbound TCP 8000 before the API is reachable from the public
internet. Prefer restricting the source CIDR during development.

## Image build

The default package source is PyPI. An alternate mirror can be selected without
editing the Dockerfile:

```text
docker build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  -t regent-core:0.1.0 -f core/Dockerfile .
```
