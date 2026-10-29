# syntax=docker/dockerfile:1.7

ARG NODE_IMAGE=node:22.23.1-bookworm-slim
ARG BASE_GUI_IMAGE=jlesage/baseimage-gui:ubuntu-24.04-v4.12.6

FROM ${NODE_IMAGE} AS source-metadata
ARG INFOOS_SOURCE_COMMIT
COPY deploy/docker/scripts/validate-source-revision.sh /tmp/validate-source-revision.sh
RUN /tmp/validate-source-revision.sh "${INFOOS_SOURCE_COMMIT}" > /source-revision

FROM ${NODE_IMAGE} AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${NODE_IMAGE} AS opencli-builder
ARG OPENCLI_VERSION=1.8.6
RUN if [ "${OPENCLI_VERSION}" != "1.8.6" ]; then \
      printf '%s\n' "OpenCLI adapter overrides require 1.8.6; re-audit overrides before upgrading" >&2; \
      exit 1; \
    fi \
    && npm install --global "@jackwener/opencli@${OPENCLI_VERSION}"

# Dependency-only view of pyproject.toml: tool settings and README edits leave
# its bytes unchanged, so the COPY below keeps the Python/CUDA layers cached.
FROM ${BASE_GUI_IMAGE} AS python-deps-manifest
USER 0
RUN /opt/base/bin/add-pkg python3
COPY deploy/docker/scripts/python-deps-manifest.py /tmp/python-deps-manifest.py
COPY pyproject.toml /tmp/pyproject.toml
RUN python3 /tmp/python-deps-manifest.py /tmp/pyproject.toml > /python-deps-pyproject.toml

FROM ${BASE_GUI_IMAGE} AS nginx-config-check
COPY deploy/docker/rootfs/opt/base/etc/nginx/default_site.conf /opt/base/etc/nginx/default_site.conf
RUN export PATH=/opt/base/bin:/opt/base/sbin:$PATH \
        USER_ID=1000 GROUP_ID=1000 APP_NAME=InfoOS \
        SECURE_CONNECTION=1 WEB_AUTHENTICATION=1; \
    for s in 09-var-dirs.sh 10-init-users.sh 10-certs.sh 10-nginx.sh; do \
        sh /etc/cont-init.d/$s || exit 1; \
    done; \
    nginx -t -c /opt/base/etc/nginx/nginx.conf

FROM ${BASE_GUI_IMAGE} AS runtime

ARG GOOGLE_CHROME_VERSION=150.0.7871.114-1
ARG GOOGLE_CHROME_SHA256=0f19e68dca574849632e25229f15853d2beac33fa06498feed43f34628bc2d53
ARG OPENCLI_VERSION=1.8.6
ARG OPENCLI_EXTENSION_ID=ildkmabpimmkaediidaifkhjpohdnifk
ARG OPENCLI_EXTENSION_VERSION=1.0.22
ARG OPENCLI_EXTENSION_SHA256=31e9e7238c68f83a76d30b3fbde2bb0d76106588bb070d3fc5be6576422b9990
ARG MIHOMO_VERSION=1.19.29
ARG MIHOMO_SHA256=5612e698e96c8b8ad15abc4c0a4f098eba9234354b4f248cb97f2528e215b094
ARG INFOOS_WITH_MIHOMO=0
ARG TORCH_CPU_VERSION=2.9.1+cpu
ARG PIP_VERSION=26.2.1
ARG SETUPTOOLS_VERSION=84.0.0
ARG WHEEL_VERSION=0.48.0

LABEL org.opencontainers.image.title="InfoOS Docker Appliance" \
      org.opencontainers.image.description="Single-container InfoOS runtime with Chrome and OpenCLI" \
      org.opencontainers.image.version="0.1.0" \
      io.infoos.chrome.version="${GOOGLE_CHROME_VERSION}" \
      io.infoos.opencli.version="${OPENCLI_VERSION}" \
      io.infoos.opencli-extension.version="${OPENCLI_EXTENSION_VERSION}" \
      io.infoos.mihomo.version="${MIHOMO_VERSION}" \
      io.infoos.mihomo.enabled="${INFOOS_WITH_MIHOMO}"

USER 0

RUN /opt/base/bin/add-pkg \
        ca-certificates \
        curl \
        ffmpeg \
        fonts-noto-cjk \
        gzip \
        lsof \
        procps \
        openssl \
        python3 \
        python3-pip \
        python3-venv \
        socat \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        util-linux \
        wmctrl

RUN mihomo_gz="/tmp/mihomo-linux-amd64-compatible-v${MIHOMO_VERSION}.gz" \
    && case "${INFOOS_WITH_MIHOMO}" in \
         0) exit 0 ;; \
         1) ;; \
         *) echo "INFOOS_WITH_MIHOMO must be 0 or 1" >&2; exit 2 ;; \
       esac \
    && curl -fsSL --connect-timeout 15 --max-time 180 --retry 3 --retry-all-errors \
        "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/mihomo-linux-amd64-compatible-v${MIHOMO_VERSION}.gz" \
        -o "${mihomo_gz}" \
    && printf '%s  %s\n' "${MIHOMO_SHA256}" "${mihomo_gz}" | sha256sum --check --strict \
    && gzip -dc "${mihomo_gz}" > /usr/local/bin/mihomo \
    && chmod 0755 /usr/local/bin/mihomo \
    && rm -f "${mihomo_gz}"

RUN chrome_deb="/tmp/google-chrome-stable_${GOOGLE_CHROME_VERSION}_amd64.deb" \
    && curl -fsSL \
        "https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${GOOGLE_CHROME_VERSION}_amd64.deb" \
        -o "${chrome_deb}" \
    && printf '%s  %s\n' "${GOOGLE_CHROME_SHA256}" "${chrome_deb}" | sha256sum --check --strict \
    && /opt/base/bin/add-pkg "${chrome_deb}" \
    && rm -f "${chrome_deb}"

COPY --from=opencli-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=opencli-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -sf /usr/local/lib/node_modules/@jackwener/opencli/dist/src/main.js /usr/local/bin/opencli

WORKDIR /opt/infoos/app
COPY --from=python-deps-manifest /python-deps-pyproject.toml ./pyproject.toml
COPY deploy/docker/python-constraints-linux-amd64.txt ./

RUN python3 -m venv /opt/infoos/venv \
    && /opt/infoos/venv/bin/pip install --no-cache-dir --upgrade \
         "pip==${PIP_VERSION}" \
         "setuptools==${SETUPTOOLS_VERSION}" \
         "wheel==${WHEEL_VERSION}"

RUN /opt/infoos/venv/bin/pip install --no-cache-dir \
         --constraint python-constraints-linux-amd64.txt \
         --extra-index-url https://download.pytorch.org/whl/cpu \
         "torch==${TORCH_CPU_VERSION}" \
    && mkdir -p backend \
    && touch backend/__init__.py \
    && /opt/infoos/venv/bin/pip install --no-cache-dir \
         --constraint python-constraints-linux-amd64.txt \
         --no-build-isolation \
         '.[web,media]' \
    && rm -rf backend \
    && rm -rf \
         /opt/infoos/venv/lib/python3.12/site-packages/torch/include \
         /opt/infoos/venv/lib/python3.12/site-packages/torch/test

ARG INFOOS_INSTALL_CUDA_ASR=0
ARG INFOOS_CUDA_ASR_PIP_INDEX_URL=https://pypi.org/simple
RUN if [ "${INFOOS_INSTALL_CUDA_ASR}" = "1" ]; then \
         /opt/infoos/venv/bin/pip install --no-cache-dir \
           --index-url "${INFOOS_CUDA_ASR_PIP_INDEX_URL}" \
           --constraint python-constraints-linux-amd64.txt \
           '.[asr-cuda]'; \
       fi \
    && install -d -m 0755 /opt/infoos/capabilities \
    && printf '%s\n' "${INFOOS_INSTALL_CUDA_ASR}" \
         > /opt/infoos/capabilities/cuda-asr

ARG LARK_CLI_VERSION=1.0.98
ARG LARK_CLI_SHA256=4a12dcb7a0aab25986d8bcd046cb45b08278531a9aac3a12b53cf3d94738fe80
RUN curl -fsSL --connect-timeout 15 --max-time 180 --retry 3 --retry-all-errors \
      "https://github.com/larksuite/cli/releases/download/v${LARK_CLI_VERSION}/lark-cli-${LARK_CLI_VERSION}-linux-amd64.tar.gz" \
      -o /tmp/lark-cli.tar.gz \
    && printf '%s  %s\n' "${LARK_CLI_SHA256}" /tmp/lark-cli.tar.gz | sha256sum --check --strict \
    && mkdir /tmp/lark-cli-install \
    && tar -xzf /tmp/lark-cli.tar.gz -C /tmp/lark-cli-install \
    && install -o root -g root -m 0755 /tmp/lark-cli-install/lark-cli /usr/local/bin/lark-cli \
    && rm -rf /tmp/lark-cli-install /tmp/lark-cli.tar.gz

LABEL io.infoos.lark-cli.version="${LARK_CLI_VERSION}"

LABEL io.infoos.asr.cuda-runtime="${INFOOS_INSTALL_CUDA_ASR}"

COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist/
COPY --from=source-metadata /source-revision /opt/infoos/source-revision

COPY deploy/docker/opencli-adapters/_infoos/ /usr/local/lib/node_modules/@jackwener/opencli/clis/_infoos/
COPY deploy/docker/opencli-adapters/feishu/ /usr/local/lib/node_modules/@jackwener/opencli/clis/feishu/
COPY deploy/docker/opencli-adapters/zsxq/ /usr/local/lib/node_modules/@jackwener/opencli/clis/zsxq/
COPY deploy/docker/opencli-adapters/douyin/ /usr/local/lib/node_modules/@jackwener/opencli/clis/douyin/
COPY deploy/docker/opencli-adapters/xiaohongshu/ /usr/local/lib/node_modules/@jackwener/opencli/clis/xiaohongshu/
COPY deploy/docker/opencli-adapters/twitter/ /usr/local/lib/node_modules/@jackwener/opencli/clis/twitter/
COPY deploy/docker/scripts/rebuild-opencli-manifest.mjs /tmp/rebuild-opencli-manifest.mjs
RUN node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/feishu/document-script.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/feishu/utils.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/zsxq/article-script.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/zsxq/document-hydration.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/zsxq/utils.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/zsxq/search.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/zsxq/request-control.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/zsxq/probe.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/douyin/full-profile.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/xiaohongshu/xhs.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/xiaohongshu/search-filters.infoos.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/douyin/search-filters.infoos.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/twitter/download.infoos.test.js \
    && node --test /usr/local/lib/node_modules/@jackwener/opencli/clis/twitter/tweets.infoos.test.js \
    && node /tmp/rebuild-opencli-manifest.mjs /usr/local/lib/node_modules/@jackwener/opencli \
    && rm -f /tmp/rebuild-opencli-manifest.mjs

RUN chmod 0755 \
      /opt/infoos/app/scripts/opencli-infoos-chrome \
      /opt/infoos/app/scripts/infoos-browser-binding.py

COPY deploy/docker/vendor/opencli-${OPENCLI_EXTENSION_VERSION}.crx /opt/infoos/extensions/opencli-${OPENCLI_EXTENSION_VERSION}.crx
RUN extension_crx="/opt/infoos/extensions/opencli-${OPENCLI_EXTENSION_VERSION}.crx" \
    && mkdir -p /opt/infoos/extensions /opt/google/chrome/extensions \
    && printf '%s  %s\n' "${OPENCLI_EXTENSION_SHA256}" "${extension_crx}" | sha256sum --check --strict \
    && printf '{"external_crx":"%s","external_version":"%s"}\n' \
        "${extension_crx}" "${OPENCLI_EXTENSION_VERSION}" \
        > "/opt/google/chrome/extensions/${OPENCLI_EXTENSION_ID}.json" \
    && chmod 0644 "${extension_crx}" "/opt/google/chrome/extensions/${OPENCLI_EXTENSION_ID}.json"

COPY deploy/docker/rootfs/ /
COPY --from=nginx-config-check /opt/base/etc/nginx/default_site.conf /opt/base/etc/nginx/default_site.conf

RUN rm -f /usr/local/bin/opencli \
    && install -m 0755 /opt/infoos/bin/opencli /usr/local/bin/opencli \
    && chmod 0755 \
        /startapp.sh \
        /etc/cont-init.d/10-webauth.sh \
        /etc/cont-init.d/20-infoos-runtime.sh \
        /etc/services.d/infoos-web/run \
        /etc/services.d/infoos-web/is_ready \
        /etc/services.d/infoos-proxy/run \
        /etc/services.d/infoos-proxy/is_ready \
        /etc/services.d/infoos-worker-*/run \
        /etc/services.d/infoos-feishu-inbox/run \
        /etc/services.d/infoos-scheduler/run \
        /etc/services.d/opencli_daemon/run \
        /etc/services.d/opencli_daemon/is_ready \
        /opt/infoos/bin/infoos-healthcheck \
        /opt/infoos/bin/infoos-verify-runtime-capabilities \
        /opt/infoos/bin/infoos-prepare-embedded-proxy-config \
        /opt/infoos/bin/infoos-run-service \
        /opt/infoos/bin/infoos-scheduler-loop

ENV APP_NAME="InfoOS Browser" \
    KEEP_APP_RUNNING=1 \
    ENABLE_CJK_FONT=0 \
    DISPLAY_WIDTH=1920 \
    DISPLAY_HEIGHT=1080 \
    INFOOS_STATE_HOME=/var/lib/infoos/state \
    INFOOS_DATA_HOME=/var/lib/infoos/data \
    INFOOS_CONFIG_PATH=/var/lib/infoos/state/config.yaml \
    INFOOS_CHROME_PROFILE_DIR=/var/lib/infoos/state/chrome-profile \
    INFOOS_BROWSER_SESSION_JSON=/var/lib/infoos/state/browser-session.json \
    INFOOS_BROWSER_SESSION_LOCK=/var/lib/infoos/state/browser-session.json.lock \
    INFOOS_OPENCLI_LOCK_PATH=/var/lib/infoos/state/opencli-browser-session.lock \
    INFOOS_OPENCLI_LOG_DIR=/var/lib/infoos/state/logs/opencli \
    INFOOS_BROWSER_LAUNCHER=/opt/infoos/app/scripts/opencli-infoos-chrome \
    INFOOS_BROWSER_BINDING_HELPER=/opt/infoos/app/scripts/infoos-browser-binding.py \
    INFOOS_OPENCLI_EXECUTABLE=/usr/local/bin/opencli \
    INFOOS_PYTHON_EXECUTABLE=/opt/infoos/venv/bin/python \
    INFOOS_CHROME_BIN=/usr/bin/google-chrome-stable \
    INFOOS_WEB_HOST=127.0.0.1 \
    INFOOS_WEB_PORT=8000 \
    INFOOS_BROWSER_CONSOLE_URL=/browser-console/ \
    INFOOS_EMBEDDED_PROXY_ENABLED=0 \
    INFOOS_EMBEDDED_PROXY_HOME=/var/lib/infoos/state/proxy \
    INFOOS_EMBEDDED_PROXY_CONFIG=/var/lib/infoos/state/proxy/source.yaml \
    INFOOS_EMBEDDED_PROXY_RUNTIME_DIR=/tmp/infoos-proxy \
    INFOOS_EMBEDDED_PROXY_MIXED_PORT=17897 \
    INFOOS_EMBEDDED_PROXY_CONTROL_PORT=19090 \
    INFOOS_SCHEDULER_INTERVAL_SECONDS=900 \
    DISPLAY=:0 \
    TZ=Asia/Shanghai \
    LANG=zh_CN.UTF-8

WORKDIR /opt/infoos/app
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD /opt/infoos/bin/infoos-healthcheck

ARG INFOOS_SOURCE_COMMIT
# Adapter versions live here, after every heavy layer: bumping one must not
# invalidate the Python/CUDA dependency layers above.
LABEL org.opencontainers.image.revision="${INFOOS_SOURCE_COMMIT}" \
      io.infoos.opencli-adapter.zsxq.version="1.0.5" \
      io.infoos.opencli-adapter.feishu-document.version="1.0.4" \
      io.infoos.opencli-adapter.douyin-full-creator.version="1.0.2" \
      io.infoos.opencli-adapter.xiaohongshu-interactions.version="1.0.0" \
      io.infoos.opencli-adapter.douyin-interactions.version="0.3.0" \
      io.infoos.opencli-adapter.twitter-tweets.version="1.0.0"
