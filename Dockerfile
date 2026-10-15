# Backend container for Cloudflare Containers. Runs the same FastAPI app as
# local dev, see apis/api_server.py.
FROM --platform=linux/amd64 python:3.13-slim

# libgomp is needed by the CBC solver binary that ships inside pulp
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# the geospatial/scientific stack is most of this image and none of it is
# optional, but the bundled test suites and pulp's solver binaries for
# platforms we'll never run on are ~200MB of pure ballast.
RUN pip install --no-cache-dir -r requirements.txt \
    && find /usr/local/lib/python3.13/site-packages \
        -type d -name tests -prune -exec rm -rf {} + \
    && find /usr/local/lib/python3.13/site-packages/pulp/solverdir/cbc \
        -mindepth 1 -maxdepth 1 ! -name linux -exec rm -rf {} +

COPY . .

EXPOSE 8000
CMD ["uvicorn", "apis.api_server:app", "--host", "0.0.0.0", "--port", "8000"]
