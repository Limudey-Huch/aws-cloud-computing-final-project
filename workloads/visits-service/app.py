import os
import socket
import datetime

import psycopg2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# The frontend lives on a CloudFront domain, this service lives behind an
# ALB on a different domain — that's a cross-origin request, so CORS is
# required here (unlike same-ALB setups where the browser never notices).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("ALLOWED_ORIGIN", "*")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

DB_HOST = os.environ["DB_HOST"]
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]


def get_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
    )


def ensure_table():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS visits (id SERIAL PRIMARY KEY, visited_at TIMESTAMP DEFAULT NOW())"
            )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def startup():
    ensure_table()


@app.get("/health")
def health():
    """Deliberately does not touch the database, so the load balancer
    doesn't mark this instance unhealthy over a transient DB blip.
    Target group health checks hit this directly, bypassing ALB routing
    rules entirely, so it doesn't need the /visits path prefix."""
    return {"status": "ok", "service": "visits-service", "hostname": socket.gethostname()}


@app.post("/visits")
def record_visit():
    """Called by the frontend on every page load — writes one row.
    Path starts with /visits so the ALB's listener rule can route it here."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO visits DEFAULT VALUES")
            cur.execute("SELECT COUNT(*) FROM visits")
            count = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    return {
        "total_visits": count,
        "handled_by_hostname": socket.gethostname(),
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


@app.get("/visits/count")
def read_count():
    """Read-only — does NOT insert a row. This is the endpoint stats-service
    calls internally; if it reused POST /visits instead, every stats
    request would silently inflate the visit counter."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM visits")
            count = cur.fetchone()[0]
    finally:
        conn.close()

    return {"total_visits": count, "handled_by_hostname": socket.gethostname()}
