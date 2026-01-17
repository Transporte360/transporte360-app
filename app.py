from flask import (
    Flask, request, redirect, url_for, render_template,
    session, send_from_directory, abort, Response
)
import sqlite3
import os
from datetime import date, datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = "CAMBIA_ESTA_CLAVE_LARGA_Y_ALEATORIA"

DB_PATH = "transporte.db"
UPLOAD_ROOT = "uploads"
UPLOAD_CMR = os.path.join(UPLOAD_ROOT, "cmr")
UPLOAD_TICKETS = os.path.join(UPLOAD_ROOT, "tickets")

os.makedirs(UPLOAD_CMR, exist_ok=True)
os.makedirs(UPLOAD_TICKETS, exist_ok=True)

# -------------------------
# DB helpers
# -------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def safe_filename(name: str) -> str:
    name = (name or "").replace("\\", "_").replace("/", "_")
    return "".join([c for c in name if c.isalnum() or c in "._-"]).strip("._-") or "file"


def set_default(key, value):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
    exists = cur.fetchone()
    if not exists:
        cur.execute("INSERT INTO settings(key, value) VALUES(?,?)", (key, str(value)))
        conn.commit()
    conn.close()


def set_setting(key, value):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO settings(key, value) VALUES(?,?)
    ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))
    conn.commit()
    conn.close()


def get_setting_float(key, default=0.0):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return float(default)
    try:
        return float(row["value"])
    except Exception:
        return float(default)


def ensure_user(username, pin, role, conductor_id=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users(username,pin,role,active,conductor_id) VALUES(?,?,?,1,?)",
            (username, pin, role, conductor_id)
        )
        conn.commit()
    conn.close()


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL UNIQUE,
      pin TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('manager','driver')),
      active INTEGER NOT NULL DEFAULT 1,
      conductor_id INTEGER,
      FOREIGN KEY (conductor_id) REFERENCES conductores(id)
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS viajes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      tipo_tramo TEXT NOT NULL DEFAULT 'CARGADO' CHECK(tipo_tramo IN ('CARGADO','VACIO')),
      fecha_salida TEXT NOT NULL,
      fecha_llegada TEXT NOT NULL,
      origen TEXT NOT NULL,
      destino TEXT NOT NULL,
      peso_kg REAL NOT NULL DEFAULT 0,
      ingreso REAL NOT NULL DEFAULT 0,

      km_inicio REAL NOT NULL,
      km_fin REAL NOT NULL,

      peajes REAL NOT NULL DEFAULT 0,
      parking REAL NOT NULL DEFAULT 0,

      camion_id INTEGER,
      conductor_id INTEGER,

      cmr_path TEXT,
      created_by_user_id INTEGER,

      FOREIGN KEY (camion_id) REFERENCES camiones(id),
      FOREIGN KEY (conductor_id) REFERENCES conductores(id),
      FOREIGN KEY (created_by_user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS repostajes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      fecha TEXT NOT NULL,
      camion_id INTEGER,
      conductor_id INTEGER,
      litros REAL NOT NULL DEFAULT 0,
      precio_litro REAL NOT NULL DEFAULT 0,
      importe REAL NOT NULL DEFAULT 0,
      km_odometro REAL,
      estacion TEXT,
      ticket_path TEXT NOT NULL,
      created_by_user_id INTEGER,
      FOREIGN KEY (camion_id) REFERENCES camiones(id),
      FOREIGN KEY (conductor_id) REFERENCES conductores(id),
      FOREIGN KEY (created_by_user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tacografo (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      conductor_id INTEGER NOT NULL,
      fecha TEXT NOT NULL,
      horas_conduccion REAL NOT NULL DEFAULT 0,
      horas_disponibilidad REAL NOT NULL DEFAULT 0,
      horas_descanso REAL NOT NULL DEFAULT 11,
      comentario TEXT,
      created_by_user_id INTEGER,
      UNIQUE(conductor_id, fecha),
      FOREIGN KEY (conductor_id) REFERENCES conductores(id),
      FOREIGN KEY (created_by_user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """)

    conn.commit()

    # Defaults
    set_default("salario_chofer_mes", "3100")
    set_default("km_objetivo_mes", "12000")
    set_default("alquiler_camion_mes", "1650")
    set_default("gestoria_mes", "250")
    set_default("autonomo_mes", "300")
    set_default("domiciliacion_mes", "30")
    set_default("seguro_mercancias_anual", "1200")
    set_default("tarifa_km", "0.95")
    set_default("consumo_l_100", "30")
    set_default("precio_gasoil_est", "1.09")

    # Usuarios demo
    ensure_user("Admin", "9999", "manager", conductor_id=None)
    ensure_user("Mohsin", "1111", "driver", conductor_id=None)

    conn.close()


def month_key(date_str):
    return (date_str or "")[:7]


def calc_distancia(km_inicio, km_fin):
    try:
        return max(0.0, float(km_fin) - float(km_inicio))
    except Exception:
        return 0.0


def coste_fijo_total_mes():
    salario = get_setting_float("salario_chofer_mes", 3100)
    alquiler = get_setting_float("alquiler_camion_mes", 1650)
    gestoria = get_setting_float("gestoria_mes", 250)
    autonomo = get_setting_float("autonomo_mes", 300)
    domiciliacion = get_setting_float("domiciliacion_mes", 30)
    seguro_merc_anual = get_setting_float("seguro_mercancias_anual", 1200)
    seguro_merc_mes = seguro_merc_anual / 12.0
    return salario + alquiler + gestoria + autonomo + domiciliacion + seguro_merc_mes


def coste_fijo_por_km():
    km_obj = get_setting_float("km_objetivo_mes", 12000)
    if km_obj <= 0:
        return 0.0
    return coste_fijo_total_mes() / km_obj


def gasoil_estimado_mes():
    km_obj = get_setting_float("km_objetivo_mes", 12000)
    consumo = get_setting_float("consumo_l_100", 30)
    precio = get_setting_float("precio_gasoil_est", 1.09)
    litros = km_obj * consumo / 100.0
    return litros * precio


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


def jinja_user(u):
    if not u:
        return None
    return {"name": u["username"], "role": u["role"], "id": u["id"], "conductor_id": u["conductor_id"]}


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
        if u["role"] != "manager":
            return abort(403)
        return fn(*args, **kwargs)
    return wrapper


# -------------------------
# Uploads
# -------------------------
@app.route("/uploads/<path:subpath>")
@login_required
def serve_upload(subpath):
    full = os.path.normpath(os.path.join(UPLOAD_ROOT, subpath))
    if not os.path.abspath(full).startswith(os.path.abspath(UPLOAD_ROOT)):
        abort(403)
    folder = os.path.dirname(full)
    filename = os.path.basename(full)
    return send_from_directory(folder, filename, as_attachment=False)


# -------------------------
# Auth routes
# -------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
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
        err = "Usuario o PIN incorrecto."

    return render_template(
        "pages/login.html",
        title="Transporte360",
        page_title="Acceso",
        page_subtitle="Entrar con usuario y PIN",
        active_page="login",
        hide_layout=True,
        user=None,
        error=err
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------------
# Home (redirige a dashboard)
# -------------------------
@app.route("/")
@login_required
def index():
    return redirect(url_for("dashboard"))


# -------------------------
# Dashboard
# -------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    hoy = date.today().isoformat()
    mes = month_key(hoy)

    tarifa = get_setting_float("tarifa_km", 0.95)
    fijo_km = coste_fijo_por_km()
    gas_est = gasoil_estimado_mes()

    conn = get_conn()
    cur = conn.cursor()

    # Si es driver, filtramos por su conductor_id
    where_driver = ""
    params = [mes]
    if u["role"] == "driver" and u["conductor_id"]:
        where_driver = " AND conductor_id=?"
        params.append(u["conductor_id"])

    cur.execute(f"SELECT * FROM viajes WHERE substr(fecha_salida,1,7)=?{where_driver}", tuple(params))
    vrows = cur.fetchall()

    km_total = 0.0
    km_vacios = 0.0
    ingresos = 0.0
    coste_var = 0.0
    for r in vrows:
        dist = calc_distancia(r["km_inicio"], r["km_fin"])
        km_total += dist
        if (r["tipo_tramo"] or "CARGADO") == "VACIO":
            km_vacios += dist
        ingresos += float(r["ingreso"] or 0)
        coste_var += float(r["peajes"] or 0) + float(r["parking"] or 0)

    # Repostajes mes
    where_driver_r = ""
    params_r = [mes]
    if u["role"] == "driver" and u["conductor_id"]:
        where_driver_r = " AND conductor_id=?"
        params_r.append(u["conductor_id"])

    cur.execute(f"SELECT SUM(importe) AS gasoil FROM repostajes WHERE substr(fecha,1,7)=?{where_driver_r}", tuple(params_r))
    gas = cur.fetchone()
    gasoil_real = float(gas["gasoil"]) if gas and gas["gasoil"] is not None else 0.0

    # Tacógrafo mes
    where_driver_t = ""
    params_t = [mes]
    if u["role"] == "driver" and u["conductor_id"]:
        where_driver_t = " AND conductor_id=?"
        params_t.append(u["conductor_id"])

    cur.execute(f"""
      SELECT
        SUM(horas_conduccion) AS hc,
        SUM(horas_disponibilidad) AS hd
      FROM tacografo
      WHERE substr(fecha,1,7)=?{where_driver_t}
    """, tuple(params_t))
    t = cur.fetchone()
    horas_conduccion = float(t["hc"]) if t and t["hc"] is not None else 0.0
    horas_disp = float(t["hd"]) if t and t["hd"] is not None else 0.0

    # Actividad reciente
    cur.execute("""
      SELECT v.*, c.matricula AS camion_label, d.nombre AS conductor_label
      FROM viajes v
      LEFT JOIN camiones c ON c.id=v.camion_id
      LEFT JOIN conductores d ON d.id=v.conductor_id
      ORDER BY v.id DESC
      LIMIT 6
    """)
    recientes = cur.fetchall()

    conn.close()

    fijo_imput = km_total * fijo_km
    beneficio = ingresos - (coste_var + gasoil_real + fijo_imput)
    pct_vacios = (km_vacios / km_total * 100.0) if km_total > 0 else 0.0
    pct_cargados = 100.0 - pct_vacios if km_total > 0 else 0.0

    return render_template(
        "pages/dashboard.html",
        title="Transporte360",
        page_title="Dashboard",
        page_subtitle=f"Resumen del mes {mes}",
        active_page="dashboard",
        hide_layout=False,
        user=jinja_user(u),

        mes=mes,
        tarifa=tarifa,
        fijo_km=fijo_km,
        gas_est=gas_est,

        km_total=km_total,
        km_vacios=km_vacios,
        ingresos=ingresos,
        coste_var=coste_var,
        gasoil_real=gasoil_real,
        beneficio=beneficio,
        pct_vacios=pct_vacios,
        pct_cargados=pct_cargados,

        horas_conduccion=horas_conduccion,
        horas_disp=horas_disp,
        recientes=recientes
    )


# -------------------------
# Ajustes (manager)
# -------------------------
@app.route("/ajustes", methods=["GET", "POST"])
@manager_required
def ajustes():
    if request.method == "POST":
        set_setting("salario_chofer_mes", request.form.get("salario_chofer_mes", "3100"))
        set_setting("km_objetivo_mes", request.form.get("km_objetivo_mes", "12000"))
        set_setting("alquiler_camion_mes", request.form.get("alquiler_camion_mes", "1650"))
        set_setting("gestoria_mes", request.form.get("gestoria_mes", "250"))
        set_setting("autonomo_mes", request.form.get("autonomo_mes", "300"))
        set_setting("domiciliacion_mes", request.form.get("domiciliacion_mes", "30"))
        set_setting("seguro_mercancias_anual", request.form.get("seguro_mercancias_anual", "1200"))
        set_setting("tarifa_km", request.form.get("tarifa_km", "0.95"))
        set_setting("consumo_l_100", request.form.get("consumo_l_100", "30"))
        set_setting("precio_gasoil_est", request.form.get("precio_gasoil_est", "1.09"))
        return redirect(url_for("ajustes"))

    u = current_user()

    return render_template(
        "pages/ajustes.html",
        title="Transporte360",
        page_title="Ajustes",
        page_subtitle="Costes + parámetros",
        active_page="ajustes",
        hide_layout=False,
        user=jinja_user(u),

        salario=get_setting_float("salario_chofer_mes", 3100),
        km_obj=get_setting_float("km_objetivo_mes", 12000),
        alquiler=get_setting_float("alquiler_camion_mes", 1650),
        gestoria=get_setting_float("gestoria_mes", 250),
        autonomo=get_setting_float("autonomo_mes", 300),
        domiciliacion=get_setting_float("domiciliacion_mes", 30),
        seguro_anual=get_setting_float("seguro_mercancias_anual", 1200),
        tarifa_km=get_setting_float("tarifa_km", 0.95),
        consumo=get_setting_float("consumo_l_100", 30),
        precio_gas=get_setting_float("precio_gasoil_est", 1.09),
        gas_est=gasoil_estimado_mes(),
        fijo_total=coste_fijo_total_mes(),
        fijo_km=coste_fijo_por_km()
    )


# -------------------------
# Camiones (manager)
# -------------------------
@app.route("/camiones", methods=["GET", "POST"])
@manager_required
def camiones():
    u = current_user()
    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        matricula = (request.form.get("matricula") or "").strip()
        descripcion = (request.form.get("descripcion") or "").strip()
        if matricula:
            try:
                cur.execute("INSERT INTO camiones(matricula, descripcion) VALUES(?,?)", (matricula, descripcion))
                conn.commit()
            except sqlite3.IntegrityError:
                pass
        return redirect(url_for("camiones"))

    cur.execute("SELECT id, matricula, descripcion FROM camiones ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "pages/camiones.html",
        title="Transporte360",
        page_title="Camiones",
        page_subtitle="Gestión de flota",
        active_page="camiones",
        hide_layout=False,
        user=jinja_user(u),
        camiones=rows
    )


# -------------------------
# Conductores (manager)
# -------------------------
@app.route("/conductores", methods=["GET", "POST"])
@manager_required
def conductores():
    u = current_user()
    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        nombre = (request.form.get("nombre") or "").strip()
        dni = (request.form.get("dni") or "").strip()
        telefono = (request.form.get("telefono") or "").strip()

        crear_usuario = (request.form.get("crear_usuario") or "") == "1"
        user_pin = (request.form.get("pin") or "").strip()

        if nombre:
            cur.execute("INSERT INTO conductores(nombre, dni, telefono) VALUES(?,?,?)", (nombre, dni, telefono))
            conn.commit()
            conductor_id = cur.lastrowid

            if crear_usuario and user_pin:
                try:
                    cur.execute(
                        "INSERT INTO users(username,pin,role,active,conductor_id) VALUES(?,?,?,1,?)",
                        (nombre, user_pin, "driver", conductor_id)
                    )
                    conn.commit()
                except sqlite3.IntegrityError:
                    pass

        return redirect(url_for("conductores"))

    cur.execute("SELECT * FROM conductores ORDER BY id DESC")
    rows = cur.fetchall()

    cur.execute("""
      SELECT u.username, u.conductor_id, u.active
      FROM users u
      WHERE u.role='driver'
      ORDER BY u.username ASC
    """)
    urows = cur.fetchall()

    conn.close()

    return render_template(
        "pages/conductores.html",
        title="Transporte360",
        page_title="Conductores",
        page_subtitle="Alta + usuarios driver",
        active_page="conductores",
        hide_layout=False,
        user=jinja_user(u),
        conductores=rows,
        usuarios_driver=urows
    )


# -------------------------
# Helpers driver
# -------------------------
def driver_conductor_id(u):
    if not u or u["role"] != "driver":
        return None
    if u["conductor_id"]:
        return str(u["conductor_id"])
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM conductores ORDER BY id ASC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return str(row["id"]) if row else None


def last_km_fin_for_camion(camion_id):
    if not camion_id:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT km_fin FROM viajes WHERE camion_id=? ORDER BY id DESC LIMIT 1", (camion_id,))
    row = cur.fetchone()
    conn.close()
    if row and row["km_fin"] is not None:
        try:
            return float(row["km_fin"])
        except Exception:
            return None
    return None


# -------------------------
# Viajes
# -------------------------
@app.route("/viajes", methods=["GET", "POST"])
@login_required
def viajes():
    u = current_user()
    tarifa = get_setting_float("tarifa_km", 0.95)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, matricula FROM camiones ORDER BY matricula ASC")
    camiones_list = cur.fetchall()

    cur.execute("SELECT id, nombre FROM conductores ORDER BY nombre ASC")
    conductores_list = cur.fetchall()

    msg = ""

    if request.method == "POST":
        tipo = (request.form.get("tipo_tramo") or "CARGADO").strip().upper()
        if tipo not in ("CARGADO", "VACIO"):
            tipo = "CARGADO"

        fecha_salida = (request.form.get("fecha_salida") or "").strip()
        fecha_llegada = (request.form.get("fecha_llegada") or "").strip()
        origen = (request.form.get("origen") or "").strip()
        destino = (request.form.get("destino") or "").strip()

        camion_id = request.form.get("camion_id") or None

        if u["role"] == "driver":
            conductor_id = driver_conductor_id(u)
        else:
            conductor_id = request.form.get("conductor_id") or None

        km_inicio = (request.form.get("km_inicio") or "").strip()
        km_fin = (request.form.get("km_fin") or "").strip()

        km_inicio_val = float(km_inicio or 0)
        if u["role"] == "driver" and (not km_inicio) and camion_id:
            last = last_km_fin_for_camion(camion_id)
            if last is not None:
                km_inicio_val = last

        km_fin_val = float(km_fin or 0)

        if tipo == "VACIO":
            peso_kg = 0.0
            peajes = 0.0
            parking = 0.0
            cmr_path = None
        else:
            peso_kg = float(request.form.get("peso_kg", "0") or 0)
            peajes = float(request.form.get("peajes", "0") or 0)
            parking = float(request.form.get("parking", "0") or 0)
            cmr_path = None

            f = request.files.get("cmr_file")
            if f and f.filename:
                fname = safe_filename(f.filename)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                saved = f"{stamp}_{fname}"
                full = os.path.join(UPLOAD_CMR, saved)
                f.save(full)
                cmr_path = os.path.join("cmr", saved)

        if not (fecha_salida and fecha_llegada and origen and destino):
            msg = "Falta fecha/origen/destino."
        elif km_fin_val < km_inicio_val:
            msg = "km_fin no puede ser menor que km_inicio."
        else:
            dist = calc_distancia(km_inicio_val, km_fin_val)

            # Manager puede forzar ingreso
            if u["role"] == "manager":
                ingreso_str = (request.form.get("ingreso") or "").strip()
                ingreso_val = float(ingreso_str) if ingreso_str else (dist * tarifa if tipo == "CARGADO" else 0.0)
            else:
                ingreso_val = dist * tarifa if tipo == "CARGADO" else 0.0

            cur.execute("""
              INSERT INTO viajes(
                tipo_tramo, fecha_salida, fecha_llegada, origen, destino, peso_kg, ingreso,
                km_inicio, km_fin, peajes, parking,
                camion_id, conductor_id, cmr_path, created_by_user_id
              )
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                tipo, fecha_salida, fecha_llegada, origen, destino, peso_kg, ingreso_val,
                km_inicio_val, km_fin_val, peajes, parking,
                camion_id, conductor_id, cmr_path, u["id"]
            ))
            conn.commit()
            return redirect(url_for("viajes"))

    # Listado
    if u["role"] == "driver":
        cid = driver_conductor_id(u)
        cur.execute("""
          SELECT v.*, c.matricula AS camion_label, d.nombre AS conductor_label
          FROM viajes v
          LEFT JOIN camiones c ON c.id = v.camion_id
          LEFT JOIN conductores d ON d.id = v.conductor_id
          WHERE v.conductor_id = ?
          ORDER BY v.id DESC
          LIMIT 200
        """, (cid,))
    else:
        cur.execute("""
          SELECT v.*, c.matricula AS camion_label, d.nombre AS conductor_label
          FROM viajes v
          LEFT JOIN camiones c ON c.id = v.camion_id
          LEFT JOIN conductores d ON d.id = v.conductor_id
          ORDER BY v.id DESC
          LIMIT 200
        """)
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "pages/viajes.html",
        title="Transporte360",
        page_title="Viajes",
        page_subtitle="Registro operativo",
        active_page="viajes",
        hide_layout=False,
        user=jinja_user(u),

        error=msg,
        tarifa=tarifa,
        camiones=camiones_list,
        conductores=conductores_list,
        viajes=rows
    )


# -------------------------
# Repostajes
# -------------------------
@app.route("/repostajes", methods=["GET", "POST"])
@login_required
def repostajes():
    u = current_user()
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, matricula FROM camiones ORDER BY matricula ASC")
    camiones_list = cur.fetchall()

    cur.execute("SELECT id, nombre FROM conductores ORDER BY nombre ASC")
    conductores_list = cur.fetchall()

    msg = ""
    if request.method == "POST":
        fecha = (request.form.get("fecha") or "").strip()
        camion_id = request.form.get("camion_id") or None

        if u["role"] == "driver":
            conductor_id = driver_conductor_id(u)
        else:
            conductor_id = request.form.get("conductor_id") or None

        litros = float(request.form.get("litros", "0") or 0)
        precio_litro = float(request.form.get("precio_litro", "0") or 0)
        km_od = request.form.get("km_odometro") or None
        estacion = (request.form.get("estacion") or "").strip()
        km_od_val = float(km_od) if km_od not in (None, "") else None

        f = request.files.get("ticket_file")
        if not fecha or litros <= 0 or precio_litro <= 0 or not (f and f.filename):
            msg = "Ticket obligatorio + fecha + litros/precio válidos."
        else:
            importe = litros * precio_litro
            fname = safe_filename(f.filename)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            saved = f"{stamp}_{fname}"
            full = os.path.join(UPLOAD_TICKETS, saved)
            f.save(full)
            ticket_path = os.path.join("tickets", saved)

            cur.execute("""
              INSERT INTO repostajes(
                fecha, camion_id, conductor_id, litros, precio_litro, importe,
                km_odometro, estacion, ticket_path, created_by_user_id
              ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                fecha, camion_id, conductor_id, litros, precio_litro, importe,
                km_od_val, estacion, ticket_path, u["id"]
            ))
            conn.commit()
            return redirect(url_for("repostajes"))

    if u["role"] == "driver":
        cid = driver_conductor_id(u)
        cur.execute("""
          SELECT r.*, c.matricula AS camion_label, d.nombre AS conductor_label
          FROM repostajes r
          LEFT JOIN camiones c ON c.id=r.camion_id
          LEFT JOIN conductores d ON d.id=r.conductor_id
          WHERE r.conductor_id = ?
          ORDER BY r.id DESC
          LIMIT 300
        """, (cid,))
    else:
        cur.execute("""
          SELECT r.*, c.matricula AS camion_label, d.nombre AS conductor_label
          FROM repostajes r
          LEFT JOIN camiones c ON c.id=r.camion_id
          LEFT JOIN conductores d ON d.id=r.conductor_id
          ORDER BY r.id DESC
          LIMIT 300
        """)
    rows = cur.fetchall()
    conn.close()

    return render_template(
        "pages/repostajes.html",
        title="Transporte360",
        page_title="Repostajes",
        page_subtitle="Registro de combustible",
        active_page="repostajes",
        hide_layout=False,
        user=jinja_user(u),

        error=msg,
        camiones=camiones_list,
        conductores=conductores_list,
        repostajes=rows
    )


# -------------------------
# Tacógrafo
# -------------------------
@app.route("/tacografo", methods=["GET", "POST"])
@login_required
def tacografo():
    u = current_user()
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, nombre FROM conductores ORDER BY nombre ASC")
    conductores_list = cur.fetchall()
    if not conductores_list:
        conn.close()
        return render_template(
            "pages/tacografo.html",
            title="Transporte360",
            page_title="Tacógrafo",
            page_subtitle="Horas",
            active_page="tacografo",
            hide_layout=False,
            user=jinja_user(u),
            error="No hay conductores.",
            semana=[],
            conductor_id=None
        )

    hoy = date.today().isoformat()

    if u["role"] == "driver":
        conductor_id = driver_conductor_id(u)
    else:
        conductor_id = request.form.get("conductor_id") if request.method == "POST" else str(conductores_list[0]["id"])

    fecha_sel = request.form.get("fecha") if request.method == "POST" else hoy
    msg = ""

    if request.method == "POST":
        horas_cond = float(request.form.get("horas_conduccion", "0") or 0)
        horas_disp = float(request.form.get("horas_disponibilidad", "0") or 0)
        horas_desc = float(request.form.get("horas_descanso", "11") or 11)
        comentario = (request.form.get("comentario") or "").strip()

        cur.execute("""
          INSERT INTO tacografo(conductor_id, fecha, horas_conduccion, horas_disponibilidad, horas_descanso, comentario, created_by_user_id)
          VALUES (?,?,?,?,?,?,?)
          ON CONFLICT(conductor_id, fecha) DO UPDATE SET
            horas_conduccion=excluded.horas_conduccion,
            horas_disponibilidad=excluded.horas_disponibilidad,
            horas_descanso=excluded.horas_descanso,
            comentario=excluded.comentario
        """, (conductor_id, fecha_sel, horas_cond, horas_disp, horas_desc, comentario, u["id"]))
        conn.commit()
        msg = "Guardado."

    fecha_dt = datetime.fromisoformat(fecha_sel).date()
    inicio = fecha_dt - timedelta(days=6)

    cur.execute("""
      SELECT * FROM tacografo
      WHERE conductor_id=? AND fecha BETWEEN ? AND ?
      ORDER BY fecha ASC
    """, (conductor_id, inicio.isoformat(), fecha_dt.isoformat()))
    semana = cur.fetchall()

    conn.close()

    return render_template(
        "pages/tacografo.html",
        title="Transporte360",
        page_title="Tacógrafo",
        page_subtitle="Conducción · Disponibilidad · Descanso",
        active_page="tacografo",
        hide_layout=False,
        user=jinja_user(u),

        ok=msg,
        conductores=conductores_list,
        conductor_id=conductor_id,
        fecha_sel=fecha_sel,
        semana=semana
    )


# -------------------------
# Export CSV (manager)
# -------------------------
@app.route("/export_viajes.csv")
@manager_required
def export_viajes_csv():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
      SELECT v.tipo_tramo, v.fecha_salida, v.fecha_llegada, v.origen, v.destino, v.peso_kg, v.ingreso,
             v.km_inicio, v.km_fin, v.peajes, v.parking,
             c.matricula AS camion, d.nombre AS conductor, v.cmr_path
      FROM viajes v
      LEFT JOIN camiones c ON c.id=v.camion_id
      LEFT JOIN conductores d ON d.id=v.conductor_id
      ORDER BY v.id DESC
    """)
    rows = cur.fetchall()
    conn.close()

    def gen():
        yield "tipo_tramo,fecha_salida,fecha_llegada,origen,destino,peso_kg,ingreso,km_inicio,km_fin,peajes,parking,camion,conductor,cmr_path\n"
        for r in rows:
            yield (
                f"{r['tipo_tramo']},{r['fecha_salida']},{r['fecha_llegada']},{r['origen']},{r['destino']},"
                f"{r['peso_kg']},{r['ingreso']},{r['km_inicio']},{r['km_fin']},"
                f"{r['peajes']},{r['parking']},{r['camion'] or ''},{r['conductor'] or ''},{r['cmr_path'] or ''}\n"
            )

    return Response(gen(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=viajes.csv"})


if __name__ == "__main__":
    init_db()
    app.run(debug=True)



