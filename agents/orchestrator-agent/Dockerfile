FROM python:3.12-slim-bookworm

WORKDIR /app

COPY . user_agent/
WORKDIR /app/user_agent

# msodbcsql18 (ODBC Driver 18 for SQL Server) -- needed by pyodbc for the
# in-process lakehouse_agent tool's direct query against the Fabric
# Lakehouse SQL analytics endpoint. Pinned to -bookworm (not the floating
# python:3.12-slim tag, which has moved to Debian 13/trixie): trixie's apt
# uses the newer "sqv" signature verifier, which rejects Microsoft's
# debian/12 repo signing key over an old SHA1 self-signature ("Policy
# rejected non-revocation signature ... SHA1 is not considered secure since
# 2026-02-01") -- bookworm's classic gpgv verifier accepts it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg unixodbc-dev \
    && curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb \
    && rm packages-microsoft-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

RUN if [ -f requirements.txt ]; then \
        pip install --index-url https://packagefeedproxy.microsoft.io/pypi/simple/ -r requirements.txt; \
    else \
        echo "No requirements.txt found"; \
    fi

EXPOSE 8088

CMD ["python", "main.py"]
