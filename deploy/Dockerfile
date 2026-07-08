# deploy 镜像：装 docker cli + compose plugin，COPY 各目标 compose + 部署脚本。
# 目标机 docker run 它（挂 docker.sock + secrets）完成无源码部署。
FROM docker.1ms.run/docker:24-cli
RUN sed -i 's|dl-cdn.alpinelinux.org|mirrors.aliyun.com|g; s|dl-4.alpinelinux.org|mirrors.aliyun.com|g' /etc/apk/repositories \
 && apk add --no-cache docker-cli-compose bash
WORKDIR /deploy
COPY deploy-remote.sh /deploy/deploy-remote.sh
COPY compose-*.yml /deploy/
# 租户 env 权威模板：随镜像发到无源码机，deploy-remote.sh 每次部署落盘到
# 目标机 secrets/example.env，供运维参照最新完整变量清单编辑 <tenant>.env。
COPY tenant.env.example /deploy/tenant.env.example
RUN chmod +x /deploy/deploy-remote.sh
ENTRYPOINT ["/deploy/deploy-remote.sh"]
