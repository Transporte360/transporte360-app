from flask import Flask, request, redirect, url_for, render_template, session, abort, send_from_directory
import sqlite3
import os
from datetime import date

app = Flask(__name__)
app.secret_key = "CAMBIA_ESTA_CLAVE_LARGA_Y_ALEATORIA"

DB_PATH = "transporte.db"


# -------------------------
# DB helpers
# -------------------------
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
      peso_kg REAL NOT NULL DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS repostajes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      fecha TEXT NOT NULL,
      litros REAL NOT NULL DEFAULT 0,
      precio_litro REAL NOT NULL DEFAULT 0,
      importe REAL NOT NULL DEFAULT 0,
      km_odometro REAL,
      estacion TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tacografo (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      fecha TEXT NOT NULL,
      horas_conduccion REAL NOT NULL DEFAULT 0,
      horas_disponibilidad REAL NOT NULL DEFAULT 0,
      horas_descanso REAL NOT NULL DEFAULT 11,
      comentario TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS camiones (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      matricula TEXT NOT NULL UNIQUE,
      descripcion TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS conductores (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      nombre TEXT NOT NULL,
      dni TEXT,
      telefono TEXT
    )
    """)

    conn.commit()

    # usuarios demo
    cur.execute("SELECT 1 FROM users WHERE username='Admin'")
    if not cur.fetchone():
        cur.execute("INSERT INTO users(username,pin,role,active) VALUES(?,?,?,1)", ("Admin", "9999", "manager"))
    cur.execute("SELECT 1 FROM users WHERE username='Mohsin'")
    if not cur.fetchone():
        cur.execute("INSERT INTO users(username,pin,role,active) VALUES(?,?,?,1)", ("Mohsin", "1111", "driver"))

    conn.commit()
    conn.close()


# -------------------------
# Auth helpers
# -------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,))
    u = cur.fetchone()
    conn.close()
    return u


def login_required(fn):
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


def manager_required(fn):
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if u["role"] != "manager":
            return abort(403)
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


# -------------------------
# Login / Logout
# -------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        pin = (request.form.get("pin") or "").strip()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=? AND pin=? AND active=1", (username, pin))
        u = cur.fetchone()
        conn.close()

        if u:
            session["user_id"] = u["id"]
            return redirect(url_for("dashboard"))
        error = "Usuario o PIN incorrecto."

    return render_template(
        "pages/login.html",
        hide_layout=True,
        body_class="login-v2",
        error=error
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------------
# Dashboard
# -------------------------
@app.route("/")
@login_required
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()

    # KPIs demo basados en DB real (simple)
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS n FROM viajes")
    total_viajes = int(cur.fetchone()["n"])

    cur.execute("SELECT IFNULL(SUM(km_fin-km_inicio),0) AS km FROM viajes")
    km_total = float(cur.fetchone()["km"] or 0)

    cur.execute("SELECT IFNULL(SUM(importe),0) AS gasoil FROM repostajes")
    gasoil_total = float(cur.fetchone()["gasoil"] or 0)

    cur.execute("SELECT IFNULL(SUM(horas_conduccion),0) AS h FROM tacografo")
    horas = float(cur.fetchone()["h"] or 0)

    conn.close()

    return render_template(
        "pages/dashboard.html",
        user=u,
        active_page="dashboard",
        page_title="Panel de Gestión",
        page_subtitle=f"Resumen general · {date.today().isoformat()}",
        total_viajes=total_viajes,
        km_total=km_total,
        gasoil_total=gasoil_total,
        horas_conduccion=horas,
    )


# -------------------------
# Viajes
# -------------------------
@app.route("/viajes", methods=["GET", "POST"])
@login_required
def viajes():
    u = current_user()
    error = ""

    if request.method == "POST":
        fecha = (request.form.get("fecha") or "").strip()
        origen = (request.form.get("origen") or "").strip()
        destino = (request.form.get("destino") or "").strip()

        def fnum(x, default=0.0):
            try:
                return float(x) if x not in (None, "") else float(default)
            except:
                return float(default)

        km_inicio = fnum(request.form.get("km_inicio"), 0)
        km_fin = fnum(request.form.get("km_fin"), 0)
        peso_kg = fnum(request.form.get("peso_kg"), 0)

        if not fecha or not origen or not destino:
            error = "Falta fecha/origen/destino."
        elif km_fin < km_inicio:
            error = "km_fin no puede ser menor que km_inicio."
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO viajes(fecha,origen,destino,km_inicio,km_fin,peso_kg) VALUES(?,?,?,?,?,?)",
                (fecha, origen, destino, km_inicio, km_fin, peso_kg)
            )
            conn.commit()
            conn.close()
            return redirect(url_for("viajes"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
  SELECT
    v.*,
    (v.km_fin - v.km_inicio) AS km_total
  FROM viajes v
  ORDER BY v.id DESC
  LIMIT 200
""")
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "pages/viajes.html",
        user=u,
        active_page="viajes",
        page_title="Viajes",
        page_subtitle="Registro operativo",
        rows=rows,
        error=error
    )


# -------------------------
# Repostajes
# -------------------------
@app.route("/repostajes", methods=["GET", "POST"])
@login_required
def repostajes():
    u = current_user()
    error = ""

    def fnum(x, default=0.0):
        try:
            return float(x) if x not in (None, "") else float(default)
        except:
            return float(default)

    if request.method == "POST":
        fecha = (request.form.get("fecha") or "").strip()
        estacion = (request.form.get("estacion") or "").strip()

        tipo = (request.form.get("tipo") or "gasoil").strip().lower()
        if tipo not in ("gasoil", "adblue"):
            tipo = "gasoil"

        conductor_id_raw = (request.form.get("conductor_id") or "").strip()
        conductor_id = None
        if conductor_id_raw:
            try:
                conductor_id = int(conductor_id_raw)
            except:
                conductor_id = None

        litros = fnum(request.form.get("litros"), 0)
        precio_litro = fnum(request.form.get("precio_litro"), 0)

        importe_raw = request.form.get("importe")
        km_odometro_raw = request.form.get("km_odometro")

        # normaliza opcionales
        importe_val = fnum(importe_raw, litros * precio_litro)

        km_odo_val = None
        if km_odometro_raw not in (None, ""):
            try:
                km_odo_val = float(km_odometro_raw)
            except:
                km_odo_val = None

        # ticket upload (opcional)
        ticket_path = None
        ticket_file = request.files.get("ticket_file")
        if ticket_file and ticket_file.filename:
            os.makedirs("uploads", exist_ok=True)
            safe_name = ticket_file.filename.replace("/", "_").replace("\\", "_")
            saved_name = f"ticket_{date.today().isoformat()}_{safe_name}"
            full_path = os.path.join("uploads", saved_name)
            ticket_file.save(full_path)
            ticket_path = saved_name

        # validaciones
        if not fecha:
            error = "Falta la fecha."
        elif litros <= 0:
            error = "Litros debe ser mayor que 0."
        elif precio_litro <= 0:
            error = "Precio/L debe ser mayor que 0."
        elif importe_val <= 0:
            error = "Importe debe ser mayor que 0."
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO repostajes(fecha, litros, precio_litro, importe, km_odometro, estacion, tipo, conductor_id, ticket_path)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (fecha, litros, precio_litro, importe_val, km_odo_val, estacion, tipo, conductor_id, ticket_path)
            )
            conn.commit()
            conn.close()
            return redirect(url_for("repostajes"))

    conn = get_conn()
    cur = conn.cursor()

    # para el select de chofer
    cur.execute("SELECT id, username FROM users WHERE active=1 ORDER BY username")
    conductores = cur.fetchall()

    # tabla de repostajes
    cur.execute("""
      SELECT
        r.*,
        CASE WHEN r.litros > 0 THEN (r.importe / r.litros) ELSE 0 END AS precio_calc
      FROM repostajes r
      ORDER BY r.id DESC
      LIMIT 200
    """)
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "pages/repostajes.html",
        user=u,
        active_page="repostajes",
        page_title="Repostajes",
        page_subtitle="Registro de combustible",
        rows=rows,
        conductores=conductores,
        error=error
    )


# -------------------------
# Tacógrafo
# -------------------------
@app.route("/tacografo", methods=["GET", "POST"])
@login_required
def tacografo():
    u = current_user()
    msg = ""

    if request.method == "POST":
        fecha = (request.form.get("fecha") or "").strip()

        def fnum(x, default=0.0):
            try:
                return float(x) if x not in (None, "") else float(default)
            except:
                return float(default)

        horas_conduccion = fnum(request.form.get("horas_conduccion"), 0)
        horas_disponibilidad = fnum(request.form.get("horas_disponibilidad"), 0)
        horas_descanso = fnum(request.form.get("horas_descanso"), 11)
        comentario = (request.form.get("comentario") or "").strip()

        if not fecha:
            msg = "Falta fecha."
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tacografo(fecha,horas_conduccion,horas_disponibilidad,horas_descanso,comentario) VALUES(?,?,?,?,?)",
                (fecha, horas_conduccion, horas_disponibilidad, horas_descanso, comentario)
            )
            conn.commit()
            conn.close()
            return redirect(url_for("tacografo"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tacografo ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "pages/tacografo.html",
        user=u,
        active_page="tacografo",
        page_title="Tacógrafo",
        page_subtitle="Horas manuales",
        rows=rows,
        msg=msg
    )


# -------------------------
# Manager pages (básicas)
# -------------------------
@app.route("/camiones", methods=["GET", "POST"])
@manager_required
def camiones():
    u = current_user()
    error = ""

    if request.method == "POST":
        matricula = (request.form.get("matricula") or "").strip()
        descripcion = (request.form.get("descripcion") or "").strip()
        if not matricula:
            error = "Falta matrícula."
        else:
            conn = get_conn()
            cur = conn.cursor()
            try:
                cur.execute("INSERT INTO camiones(matricula,descripcion) VALUES(?,?)", (matricula, descripcion))
                conn.commit()
            except sqlite3.IntegrityError:
                error = "Esa matrícula ya existe."
            conn.close()
            if not error:
                return redirect(url_for("camiones"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM camiones ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "pages/camiones.html",
        user=u,
        active_page="camiones",
        page_title="Camiones",
        page_subtitle="Gestión de flota",
        rows=rows,
        error=error
    )


@app.route("/conductores", methods=["GET", "POST"])
@manager_required
def conductores():
    u = current_user()
    error = ""

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        dni = (request.form.get("dni") or "").strip()
        telefono = (request.form.get("telefono") or "").strip()
        if not nombre:
            error = "Falta nombre."
        else:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("INSERT INTO conductores(nombre,dni,telefono) VALUES(?,?,?)", (nombre, dni, telefono))
            conn.commit()
            conn.close()
            return redirect(url_for("conductores"))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM conductores ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "pages/conductores.html",
        user=u,
        active_page="conductores",
        page_title="Conductores",
        page_subtitle="Alta y gestión",
        rows=rows,
        error=error
    )


@app.route("/ajustes", methods=["GET"])
@manager_required
def ajustes():
    u = current_user()
    return render_template(
        "pages/ajustes.html",
        user=u,
        active_page="ajustes",
        page_title="Ajustes",
        page_subtitle="Parámetros de la empresa"
    )


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)



