# deploy 镜像：装 docker cli + compose plugin，COPY 各目标 compose + 部署脚本。
# 目标机 docker run 它（挂 docker.sock + secrets）完成无源码部署。
FROM docker:24-cli
RUN apk add --no-cache docker-compose bash
WORKDIR /deploy
COPY deploy-remote.sh /deploy/deploy-remote.sh
COPY compose-*.yml /deploy/
RUN chmod +x /deploy/deploy-remote.sh
ENTRYPOINT ["/deploy/deploy-remote.sh"]
