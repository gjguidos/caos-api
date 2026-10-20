from __future__ import annotations


BLOCKED_HOSTS = {
    # Localhost / loopback aliases
    "localhost",
    "localhost.localdomain",
    "local",
    "loopback",
    "ip6-localhost",
    "ip6-loopback",

    # Docker/container host aliases
    "host.docker.internal",
    "gateway.docker.internal",
    "docker.for.mac.localhost",
    "docker.for.win.localhost",

    # Cloud metadata / credential endpoints
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance.metadata.azure.com",
    "metadata.azure.com",
    "metadata.oraclecloud.com",
    "instance-metadata.oraclecloud.com",
    "metadata.aliyun.com",
    "metadata.digitalocean.com",
    "metadata.openstack.org",
    "openstack-metadata",
    "instance-data",
    "instance-data.ec2.internal",
    "ecs-metadata",
    "ecs-task-metadata",
    "container-metadata",

    # Kubernetes service discovery
    "kubernetes",
    "kubernetes.default",
    "kubernetes.default.svc",
    "kubernetes.default.svc.cluster.local",
    "kube-dns",
    "kube-dns.kube-system",
    "kube-dns.kube-system.svc",
    "kube-dns.kube-system.svc.cluster.local",

    # Common internal services
    "redis",
    "mongo",
    "mongodb",
    "postgres",
    "postgresql",
    "mysql",
    "mariadb",
    "rabbitmq",
    "kafka",
    "zookeeper",
    "elasticsearch",
    "opensearch",
    "prometheus",
    "grafana",
    "jaeger",
    "zipkin",
    "vault",
    "consul",
    "nomad",
    "etcd",
    "minio",

    # Common app aliases
    "api",
    "backend",
    "frontend",
    "web",
    "app",
    "server",
    "admin",
    "internal",
    "intranet",
    "gateway",
    "proxy",
    "nginx",
    "traefik",
    "envoy",

    # Secret/config host aliases
    "secrets",
    "secret",
    "config",
    "configuration",
    "settings",
    "credentials",
}


BLOCKED_HOST_SUFFIXES = {
    ".localhost",
    ".local",
    ".internal",
    ".intranet",
    ".corp",
    ".lan",
    ".home",
    ".svc",
    ".svc.cluster.local",
    ".cluster.local",
}


BLOCKED_IP_NETWORKS = {
    # IPv4 local/special ranges
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",

    # IPv6 local/special ranges
    "::/128",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
}


CLOUD_METADATA_IPS = {
    "169.254.169.254",  # AWS, Azure, GCP-style metadata
    "100.100.100.200", # Alibaba Cloud metadata
}