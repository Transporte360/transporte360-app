import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, abort

app = Flask(__name__)
app.secret_key = "CAMBIA-ESTO-POR-UNA-CLAVE-LARGA-UNICA"

DB_PATH = "transporte.db"
UPLOAD_ROOT = "uploads"
UPLOAD_CMR = os.path.join(UPLOAD_ROOT, "cmr")
os.makedirs(UPLOAD_CMR, exist_ok=True)

USERS = {
    "manager": {"pin": "1234", "role": "manager", "name": "Carlos Rodríguez"},
    "driver1": {"pin": "5678", "role": "driver", "name": "Miguel Fernández"},
    "driver2": {"pin": "9012", "role": "driver", "name": "Ana Martínez"},
}

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS viajes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL,
      created_by TEXT NOT NULL,
      created_role TEXT NOT NULL,
      fecha TEXT NOT NULL,
      origen TEXT NOT NULL,
      destino TEXT NOT NULL,
      camion_matricula TEXT NOT NULL,
      km_inicio REAL NOT NULL,
      km_fin REAL NOT NULL,
      km_total REAL NOT NULL,
      peso_kg REAL NOT NULL DEFAULT 0,
      cmr_path TEXT
    )
    """)
    conn.commit()
    conn.close()

def safe_filename(name: str) -> str:
    name = (name or "").replace("\\", "_").replace("/", "_")
    clean = "".join([c for c in name if c.isalnum() or c in "._-"]).strip("._-")
    return clean or "file"

def last_km_fin_for_camion(matricula: str):
    if not matricula:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
      SELECT km_fin FROM viajes
      WHERE camion_matricula = ?
      ORDER BY id DESC
      LIMIT 1
    """, (matricula.strip().upper(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    try:
        return float(row["km_fin"])
    except:
        return None

def current_user():
    return session.get("user")

def require_login():
    return bool(current_user())

@app.get("/uploads/<path:subpath>")
def serve_upload(subpath):
    if not require_login():
        return redirect(url_for("login_get"))
    full = os.path.normpath(os.path.join(UPLOAD_ROOT, subpath))
    if not os.path.abspath(full).startswith(os.path.abspath(UPLOAD_ROOT)):
        abort(403)
    folder = os.path.dirname(full)
    filename = os.path.basename(full)
    return send_from_directory(folder, filename, as_attachment=False)

@app.get("/login")
def login_get():
    if current_user():
        return redirect(url_for("dashboard"))
    return render_template("pages/login.html", error=None)

@app.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    pin = (request.form.get("pin") or "").strip()

    u = USERS.get(username)
    if not u or u["pin"] != pin:
        return render_template("pages/login.html", error="Usuario o PIN incorrectos")

    session["user"] = {"username": username, "role": u["role"], "name": u["name"]}
    return redirect(url_for("dashboard"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_get"))

@app.get("/")
def root():
    return redirect(url_for("dashboard"))

@app.get("/dashboard")
def dashboard():
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    return render_template(
        "pages/dashboard.html",
        user=user,
        title="Dashboard - Transporte360",
        page_title="Panel de Gestión",
        page_subtitle="Resumen mensual de operaciones",
        active_page="dashboard",
    )

@app.route("/viajes", methods=["GET", "POST"])
def viajes():
    if not require_login():
        return redirect(url_for("login_get"))

    user = current_user()
    error = None

    if request.method == "POST":
        fecha = (request.form.get("fecha") or "").strip()
        origen = (request.form.get("origen") or "").strip()
        destino = (request.form.get("destino") or "").strip()
        camion = (request.form.get("camion_matricula") or "").strip().upper()

        km_inicio_raw = (request.form.get("km_inicio") or "").strip()
        km_fin_raw = (request.form.get("km_fin") or "").strip()
        peso_raw = (request.form.get("peso_kg") or "0").strip()

        km_inicio = None
        if km_inicio_raw:
            try:
                km_inicio = float(km_inicio_raw)
            except:
                km_inicio = None
        if km_inicio is None:
            last = last_km_fin_for_camion(camion)
            km_inicio = last if last is not None else 0.0

        try:
            km_fin = float(km_fin_raw)
        except:
            km_fin = None

        try:
            peso_kg = float(peso_raw or 0)
        except:
            peso_kg = 0.0

        cmr_path = None
        f = request.files.get("cmr_file")
        if f and f.filename:
            fname = safe_filename(f.filename)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved = f"{stamp}_{fname}"
            full = os.path.join(UPLOAD_CMR, saved)
            f.save(full)
            cmr_path = os.path.join("cmr", saved)

        if not (fecha and origen and destino and camion):
            error = "Faltan campos: fecha, origen, destino y matrícula."
        elif km_fin is None:
            error = "KM fin es obligatorio y debe ser un número."
        elif km_fin < km_inicio:
            error = "KM fin no puede ser menor que KM inicio."
        else:
            km_total = km_fin - km_inicio
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
              INSERT INTO viajes (
                created_at, created_by, created_role,
                fecha, origen, destino,
                camion_matricula,
                km_inicio, km_fin, km_total,
                peso_kg, cmr_path
              ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                datetime.now().isoformat(timespec="seconds"),
                user["username"], user["role"],
                fecha, origen, destino,
                camion,
                km_inicio, km_fin, km_total,
                peso_kg, cmr_path
            ))
            conn.commit()
            conn.close()
            return redirect(url_for("viajes"))

    conn = get_conn()
    cur = conn.cursor()
    if user["role"] == "driver":
        cur.execute("SELECT * FROM viajes WHERE created_by=? ORDER BY id DESC LIMIT 200", (user["username"],))
    else:
        cur.execute("SELECT * FROM viajes ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "pages/viajes.html",
        user=user,
        title="Viajes - Transporte360",
        page_title="Viajes",
        page_subtitle="Registro operativo (odómetro + CMR)",
        active_page="viajes",
        rows=rows,
        error=error,
    )

# placeholders para que no rompa el menú (luego los hacemos)
@app.get("/repostajes")
def repostajes():
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    return render_template("pages/dashboard.html", user=user, title="Repostajes", page_title="Repostajes", page_subtitle="En construcción", active_page="repostajes")

@app.get("/tacografo")
def tacografo():
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    return render_template("pages/dashboard.html", user=user, title="Tacógrafo", page_title="Tacógrafo", page_subtitle="En construcción", active_page="tacografo")

@app.get("/camiones")
def camiones():
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    if user["role"] != "manager":
        return ("Forbidden", 403)
    return render_template("pages/dashboard.html", user=user, title="Camiones", page_title="Camiones", page_subtitle="En construcción", active_page="camiones")

@app.get("/conductores")
def conductores():
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    if user["role"] != "manager":
        return ("Forbidden", 403)
    return render_template("pages/dashboard.html", user=user, title="Conductores", page_title="Conductores", page_subtitle="En construcción", active_page="conductores")

@app.get("/ajustes")
def ajustes():
    if not require_login():
        return redirect(url_for("login_get"))
    user = current_user()
    if user["role"] != "manager":
        return ("Forbidden", 403)
    return render_template("pages/dashboard.html", user=user, title="Ajustes", page_title="Ajustes", page_subtitle="En construcción", active_page="ajustes")

if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)


