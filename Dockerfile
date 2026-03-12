FROM openclaw:local

USER root

# Install pip and Python packages needed by workspace skills
# tavily-python: tavily skill (web search)
# requests:      n8n skill (workflow API), also a tavily-python dep
RUN python3 -m ensurepip 2>/dev/null || \
    (curl -sS https://bootstrap.pypa.io/get-pip.py | python3 - --break-system-packages) && \
    pip install --break-system-packages --no-cache-dir \
      tavily-python \
      requests && \
    rm -rf /root/.cache/pip /tmp/*

USER node
