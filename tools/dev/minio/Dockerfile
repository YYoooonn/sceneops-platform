# Local object-storage image: MinIO server + mc client, built from the pinned upstream
# source tags. The upstream registry image and the dl.min.io binaries are no longer
# published, so the release SceneOps develops against is compiled here instead.
#
# Infrastructure-only: the build context is this directory and no SceneOps code is copied.
#
#   docker build -t sceneops-minio:RELEASE.2024-11-07T00-52-20Z tools/dev/minio

FROM --platform=$BUILDPLATFORM golang:1.22.12-alpine3.21@sha256:1699c10032ca2582ec89a24a1312d986a3f094aed3d5c1147b19880afe40e052 AS build

ARG TARGETARCH

ARG MINIO_RELEASE=RELEASE.2024-11-07T00-52-20Z
ARG MINIO_COMMIT=cefc43e4daa4cbb490ef6726ea374e26a93eb85e
ARG MINIO_SOURCE_SHA256=9f6ba4a0a78dad3f19ab99880dbaef9d2bd4a0d5a24ad7200d064bdefa7e6435

ARG MC_RELEASE=RELEASE.2024-11-05T11-29-45Z
ARG MC_COMMIT=6ac18619cf881074fe6edcc79ab62c9c85da60b9
ARG MC_SOURCE_SHA256=aea77436693ffdb684327c465fb9dee44b65890e7016243248f833646ad0a0ed

ENV CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH}

RUN apk add --no-cache ca-certificates curl

# The tag tarball is verified against a pinned digest before it is compiled; the Go
# module graph is verified by the source tree's own go.sum.
WORKDIR /src/minio
RUN curl -fsSL -o source.tgz "https://github.com/minio/minio/archive/refs/tags/${MINIO_RELEASE}.tar.gz" \
    && echo "${MINIO_SOURCE_SHA256}  source.tgz" | sha256sum -c - \
    && tar -xzf source.tgz --strip-components=1 \
    && version="${MINIO_RELEASE#RELEASE.}" \
    && pkg=github.com/minio/minio/cmd \
    && go build -trimpath -tags kqueue -o /out/minio -ldflags "-s -w \
        -X ${pkg}.Version=${version} \
        -X ${pkg}.CopyrightYear=${version%%-*} \
        -X ${pkg}.ReleaseTag=${MINIO_RELEASE} \
        -X ${pkg}.CommitID=${MINIO_COMMIT} \
        -X ${pkg}.ShortCommitID=$(echo ${MINIO_COMMIT} | cut -c1-12)" .

WORKDIR /src/mc
RUN curl -fsSL -o source.tgz "https://github.com/minio/mc/archive/refs/tags/${MC_RELEASE}.tar.gz" \
    && echo "${MC_SOURCE_SHA256}  source.tgz" | sha256sum -c - \
    && tar -xzf source.tgz --strip-components=1 \
    && version="${MC_RELEASE#RELEASE.}" \
    && pkg=github.com/minio/mc/cmd \
    && go build -trimpath -tags kqueue -o /out/mc -ldflags "-s -w \
        -X ${pkg}.Version=${version} \
        -X ${pkg}.CopyrightYear=${version%%-*} \
        -X ${pkg}.ReleaseTag=${MC_RELEASE} \
        -X ${pkg}.CommitID=${MC_COMMIT} \
        -X ${pkg}.ShortCommitID=$(echo ${MC_COMMIT} | cut -c1-12)" .


FROM alpine:3.21.3@sha256:a8560b36e8b8210634f77d9f7f9efd7ffa463e380b75e2e74aff4511df3ef88c

# curl backs the Compose healthcheck; mc backs the bucket bootstrap (tools/dev/minio_init.sh).
RUN apk add --no-cache ca-certificates curl

COPY --from=build /out/minio /out/mc /usr/local/bin/

EXPOSE 9000 9001
ENTRYPOINT ["minio"]
