from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
from datetime import date
from functools import wraps

app = Flask(__name__)
app.secret_key = "CAMBIA-ESTO-POR-UNA-CLAVE-LARGA-UNICA"

DB_PATH = "transporte.db"


# =========================
# DB helpers
# =========================
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            pin TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('manager','driver')),
            active INTEGER NOT NULL DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS viajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            origen TEXT NOT NULL,
            destino TEXT NOT NULL,
            km_inicio REAL NOT NULL DEFAULT 0,
            km_fin REAL NOT NULL DEFAULT 0,
            peso_kg REAL NOT NULL DEFAULT 0,
            cmr_filename TEXT,
            created_by_user_id INTEGER,
            FOREIGN KEY(created_by_user_id) REFERENCES users(id)
        )
    """)

    # Defaults: usuarios demo
    cur.execute("SELECT 1 FROM users WHERE username=?", ("manager",))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, pin, role, active) VALUES (?, ?, ?, ?)",
            ("manager", "1234", "manager", 1)
        )

    cur.execute("SELECT 1 FROM users WHERE username=?", ("driver1",))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (username, pin, role, active) VALUES (?, ?, ?, ?)",
            ("driver1", "5678", "driver", 1)
        )

    conn.commit()
    conn.close()


# =========================
# Auth helpers
# =========================
def current_user():
    return session.get("user")


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def manager_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if u.get("role") != "manager":
            return "Forbidden", 403
        return fn(*args, **kwargs)
    return wrapper


# =========================
# Routes: Auth
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))

    error = None

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        pin = (request.form.get("pin") or "").strip()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, role FROM users WHERE username=? AND pin=? AND active=1",
            (username, pin)
        )
        user = cur.fetchone()
        conn.close()

        if user:
            session["user"] = {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"]
            }
            return redirect(url_for("dashboard"))

        error = "Usuario o PIN incorrectos"

    return render_template("pages/login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================
# Routes: Dashboard
# =========================
@app.route("/")
@login_required
def root():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    today = date.today().isoformat()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM viajes")
    n_viajes = int(cur.fetchone()["n"])
    conn.close()

    # Usa tu templates/pages/index.html como dashboard
    return render_template(
        "pages/index.html",
        user=u,
        today=today,
        n_viajes=n_viajes
    )


# =========================
# Routes: Viajes (v1 simple)
# =========================
@app.route("/viajes", methods=["GET", "POST"])
@login_required
def viajes():
    u = current_user()
    error = None

    if request.method == "POST":
        fecha = (request.form.get("fecha") or "").strip()
        origen = (request.form.get("origen") or "").strip()
        destino = (request.form.get("destino") or "").strip()

        km_inicio = (request.form.get("km_inicio") or "").strip()
        km_fin = (request.form.get("km_fin") or "").strip()

        peso_kg = (request.form.get("peso_kg") or "").strip()

        # parse seguros
        try:
            km_inicio_val = float(km_inicio) if km_inicio else 0.0
        except:
            km_inicio_val = 0.0

        try:
            km_fin_val = float(km_fin) if km_fin else 0.0
        except:
            km_fin_val = 0.0

        try:
            peso_val = float(peso_kg) if peso_kg else 0.0
        except:
            peso_val = 0.0

        if not fecha or not origen or not destino:
            error = "Falta fecha/origen/destino"
        elif km_fin_val < km_inicio_val:
            error = "km_fin no puede ser menor que km_inicio"
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO viajes (fecha, origen, destino, km_inicio, km_fin, peso_kg, cmr_filename, created_by_user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fecha, origen, destino, km_inicio_val, km_fin_val, peso_val, None, u["id"])
            )
            conn.commit()
            conn.close()
            return redirect(url_for("viajes"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT v.*
        FROM viajes v
        ORDER BY v.id DESC
        LIMIT 200
        """
    )
    rows = cur.fetchall()
    conn.close()

    return render_template("pages/viajes.html", user=u, rows=rows, error=error)


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)



