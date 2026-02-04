# ShopStream (Learning Project) — Docker + Docker Swarm Stack

This is a **learning project** to practice:
- Building Docker images for multiple services
- Running a **Docker Swarm** cluster (manager + workers)
- Deploying a full stack with `docker stack deploy`
- Using **overlay networks**, **volumes**, **secrets/configs**, and **rolling updates**

---
A mini production-style e-commerce platform setup (microservices style):

- **Traefik** (ingress / reverse proxy)
- **Frontend** (SPA served by Nginx)
- **API Gateway**
- **Auth / Product / Order / Notification** services
- **MariaDB, Redis, RabbitMQ, Elasticsearch, MinIO**
- **Monitoring**: Prometheus, Grafana, Loki, Alertmanager + exporters

---

## Requirements

- Docker installed (Docker Engine)
- (Optional) 3 machines/VMs for real Swarm: **1 manager + 2 workers**
- Or run Swarm on one machine (for practice)

Check Docker:
```bash
docker version
docker info
````

---

## Quick Start (Swarm)

### 1) Initialize Swarm (on manager)

```bash
docker swarm init
```

### 2) Join worker nodes (run on workers)

Get the join command from manager:

```bash
docker swarm join-token worker
```

Then run the printed command on each worker.

Verify cluster:

```bash
docker node ls
```

---

## Deploy the Stack

From the manager node, inside the repo:



```bash
docker stack deploy -c docker-stack.yml shopstream
```


Check services:

```bash
docker stack services shopstream
docker stack ps shopstream
docker service ls
```

---

## View Logs

Service logs:

```bash
docker service logs -f shopstream_api-gateway
```

All services (list first):

```bash
docker stack services shopstream
```

---

