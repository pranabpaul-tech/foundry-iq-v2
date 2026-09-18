FROM python:3.12-slim

WORKDIR /app

COPY . user_agent/
WORKDIR /app/user_agent

# msodbcsql18 (ODBC Driver 18 for SQL Server) -- needed by pyodbc to connect
# to the Fabric Lakehouse SQL analytics endpoint. python:3.12-slim is
# Debian 12 (bookworm)-based.
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
