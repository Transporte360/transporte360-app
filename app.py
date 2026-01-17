from flask import (
    Flask, request, redirect, url_for, render_template_string,
    session, send_from_directory, abort, Response
)
import sqlite3
import os
from datetime import date, datetime, timedelta

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
    except:
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

    def try_alter(sql):
        try:
            cur.execute(sql)
            conn.commit()
        except:
            pass

    try_alter("ALTER TABLE users ADD COLUMN conductor_id INTEGER")
    try_alter("ALTER TABLE viajes ADD COLUMN tipo_tramo TEXT NOT NULL DEFAULT 'CARGADO'")
    conn.close()

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

    ensure_user("Admin", "9999", "manager", conductor_id=None)
    ensure_user("Mohsin", "1111", "driver", conductor_id=None)


def month_key(date_str):
    return (date_str or "")[:7]


def calc_distancia(km_inicio, km_fin):
    try:
        return max(0.0, float(km_fin) - float(km_inicio))
    except:
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
# UI (estilo tipo Rocket)
# -------------------------
def nav():
    u = current_user()

    if not u:
        return ""

    links = [
        ("Inicio", "/"),
        ("Viajes", "/viajes"),
        ("Repostajes", "/repostajes"),
        ("Tacógrafo", "/tacografo"),
    ]
    if u["role"] == "manager":
        links += [
            ("Dashboard", "/dashboard"),
            ("Camiones", "/camiones"),
            ("Conductores", "/conductores"),
            ("Ajustes", "/ajustes"),
        ]

    link_html = ""
    path = request.path
    for label, href in links:
        active = "nav-active" if path == href else ""
        link_html += f"<a class='nav-link {active}' href='{href}'>{label}</a>"

    return f"""
    <div class="topbar">
      <div class="brand">
        <div class="brand-dot"></div>
        <div>
          <div class="brand-title">Transporte360</div>
          <div class="brand-sub">Sistema de gestión</div>
        </div>
      </div>

      <div class="nav">{link_html}</div>

      <div class="userbox">
        <div class="userpill">👤 {u['username']} · <span class="muted">{u['role']}</span></div>
        <a class="btn btn-ghost" href="/logout">Salir</a>
      </div>
    </div>
    """


BASE_HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{{title}}</title>
<style>
  :root{
    --bg0:#f6f7fb;
    --bg1:#ffffff;
    --text:#0f172a;
    --muted:#64748b;
    --line:#e7eaf3;
    --shadow: 0 12px 30px rgba(15, 23, 42, .08);
    --shadow2: 0 8px 20px rgba(15, 23, 42, .08);
    --r:18px;

    --pri:#6d28d9;      /* morado claro */
    --pri2:#7c3aed;
    --pri3:#a78bfa;
    --ok:#16a34a;
    --warn:#f59e0b;
    --bad:#ef4444;
  }

  *{box-sizing:border-box}
  body{
    margin:0;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Apple Color Emoji","Segoe UI Emoji";
    color:var(--text);
    background:
      radial-gradient(1200px 600px at 10% 10%, rgba(167,139,250,.20), transparent 60%),
      radial-gradient(900px 500px at 95% 0%, rgba(109,40,217,.14), transparent 55%),
      linear-gradient(180deg, #f7f7ff 0%, var(--bg0) 35%, var(--bg0) 100%);
    min-height:100vh;
  }

  .wrap{max-width:1180px; margin:0 auto; padding:18px 16px 28px;}
  .topbar{
    background:rgba(255,255,255,.75);
    border:1px solid var(--line);
    backdrop-filter: blur(10px);
    border-radius: 22px;
    padding:12px 14px;
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:12px;
    box-shadow: var(--shadow2);
    position: sticky;
    top: 12px;
    z-index: 10;
  }
  .brand{display:flex; align-items:center; gap:10px; min-width: 200px;}
  .brand-dot{
    width:38px; height:38px; border-radius:14px;
    background: linear-gradient(135deg, var(--pri) 0%, var(--pri3) 100%);
    box-shadow: 0 10px 25px rgba(109,40,217,.25);
  }
  .brand-title{font-weight:900; letter-spacing:-0.02em}
  .brand-sub{font-size:12px; color:var(--muted); margin-top:1px}

  .nav{display:flex; gap:8px; flex-wrap:wrap; justify-content:center}
  .nav-link{
    padding:8px 11px;
    border-radius: 999px;
    text-decoration:none;
    color: #1f2937;
    border: 1px solid transparent;
    font-weight: 700;
    font-size: 13px;
  }
  .nav-link:hover{background: rgba(109,40,217,.08); border-color: rgba(109,40,217,.15);}
  .nav-active{
    background: rgba(109,40,217,.12);
    border-color: rgba(109,40,217,.22);
    color: var(--pri);
  }

  .userbox{display:flex; align-items:center; gap:10px; justify-content:flex-end; min-width: 220px;}
  .userpill{
    font-size: 13px;
    font-weight: 800;
    padding: 8px 12px;
    border:1px solid var(--line);
    background: rgba(255,255,255,.85);
    border-radius: 999px;
  }

  .pagehead{margin:18px 2px 12px;}
  .h1{font-size:26px; font-weight: 950; letter-spacing:-0.03em;}
  .sub{color:var(--muted); margin-top:4px;}

  .grid{display:grid; gap:12px;}
  .kpi-grid{grid-template-columns: repeat(12, 1fr);}
  .col-3{grid-column: span 3;}
  .col-4{grid-column: span 4;}
  .col-5{grid-column: span 5;}
  .col-6{grid-column: span 6;}
  .col-7{grid-column: span 7;}
  .col-8{grid-column: span 8;}
  .col-12{grid-column: span 12;}
  @media (max-width: 980px){
    .col-3,.col-4,.col-5,.col-6,.col-7,.col-8,.col-12{grid-column: span 12;}
    .topbar{position: static;}
    .brand{min-width:unset}
    .userbox{min-width:unset}
  }

  .card{
    background: rgba(255,255,255,.92);
    border: 1px solid var(--line);
    border-radius: var(--r);
    box-shadow: var(--shadow);
    padding: 14px;
  }
  .card h3{margin:0 0 8px; font-size:14px; color:#111827; letter-spacing:-0.01em}
  .muted{color:var(--muted)}
  .pill{
    display:inline-flex;
    align-items:center;
    gap:6px;
    padding: 6px 10px;
    border-radius: 999px;
    border:1px solid var(--line);
    background: rgba(255,255,255,.9);
    font-size: 12px;
    font-weight: 800;
  }
  .pill.ok{color: var(--ok); border-color: rgba(22,163,74,.25); background: rgba(22,163,74,.08);}
  .pill.warn{color: #92400e; border-color: rgba(245,158,11,.30); background: rgba(245,158,11,.10);}
  .pill.bad{color: var(--bad); border-color: rgba(239,68,68,.25); background: rgba(239,68,68,.08);}

  .kpi{
    display:flex;
    align-items:flex-start;
    justify-content:space-between;
    gap:10px;
  }
  .kpi .label{font-size:13px; color: var(--muted); font-weight:800;}
  .kpi .value{font-size:26px; font-weight: 950; letter-spacing:-0.03em; margin-top:4px;}
  .kpi .note{font-size:12px; color: var(--muted); margin-top:6px;}
  .icon{
    width:40px; height:40px; border-radius: 14px;
    display:flex; align-items:center; justify-content:center;
    background: rgba(109,40,217,.10);
    border:1px solid rgba(109,40,217,.18);
  }

  .btn{
    display:inline-flex; align-items:center; justify-content:center;
    gap:8px;
    background: linear-gradient(135deg, var(--pri) 0%, var(--pri2) 100%);
    color:#fff;
    padding: 10px 14px;
    border:none;
    border-radius: 14px;
    cursor:pointer;
    font-weight: 900;
    text-decoration:none;
    box-shadow: 0 10px 22px rgba(109,40,217,.18);
  }
  .btn:hover{filter: brightness(0.98);}
  .btn-ghost{
    background: transparent;
    border:1px solid var(--line);
    color:#111827;
    box-shadow:none;
  }
  .btn-ghost:hover{background: rgba(15,23,42,.04);}

  input, select{
    width:100%;
    padding: 11px 12px;
    border-radius: 14px;
    border: 1px solid var(--line);
    background: rgba(255,255,255,.95);
    outline: none;
    font-weight: 650;
  }
  input:focus, select:focus{
    border-color: rgba(109,40,217,.35);
    box-shadow: 0 0 0 4px rgba(109,40,217,.10);
  }
  label{display:block; font-weight: 900; font-size: 12px; margin: 10px 0 6px; color:#111827;}

  table{
    width:100%;
    border-collapse: collapse;
    background: rgba(255,255,255,.92);
    border: 1px solid var(--line);
    border-radius: var(--r);
    overflow:hidden;
    box-shadow: var(--shadow);
    margin-top: 12px;
  }
  th, td{
    padding: 10px 10px;
    border-bottom: 1px solid var(--line);
    font-size: 13px;
    text-align:left;
    vertical-align: top;
  }
  th{
    background: rgba(109,40,217,.06);
    color:#111827;
    font-weight: 950;
  }
  tr:hover td{background: rgba(15,23,42,.02);}

  .split{display:flex; gap:12px; flex-wrap:wrap;}
  .split > div{flex: 1; min-width: 260px;}

  .login-shell{
    max-width: 980px;
    margin: 0 auto;
    padding: 26px 16px;
    display:flex;
    align-items:center;
    min-height: 100vh;
  }
  .login-grid{
    display:grid;
    grid-template-columns: 1.1fr .9fr;
    gap: 16px;
    width: 100%;
  }
  @media (max-width: 980px){
    .login-grid{grid-template-columns: 1fr;}
  }
  .hero{
    padding: 18px;
    border-radius: 24px;
    border: 1px solid rgba(255,255,255,.40);
    background:
      radial-gradient(900px 500px at 10% 10%, rgba(167,139,250,.35), transparent 55%),
      radial-gradient(700px 420px at 85% 10%, rgba(109,40,217,.22), transparent 55%),
      linear-gradient(135deg, rgba(255,255,255,.75), rgba(255,255,255,.55));
    box-shadow: var(--shadow);
    position: relative;
    overflow:hidden;
  }
  .hero h1{
    margin: 0;
    font-size: 34px;
    line-height: 1.05;
    letter-spacing: -0.04em;
    font-weight: 980;
  }
  .hero p{color: var(--muted); font-weight: 650; margin-top:10px; max-width: 54ch;}
  .hero .badge{
    display:inline-flex; align-items:center; gap:8px;
    margin-top: 14px;
    padding: 8px 12px;
    border-radius: 999px;
    border: 1px solid rgba(109,40,217,.18);
    background: rgba(255,255,255,.75);
    font-weight: 900;
    color: var(--pri);
  }

  .donut{
    width: 120px;
    height: 120px;
    border-radius: 999px;
    background: conic-gradient(var(--pri) 0 var(--p), rgba(100,116,139,.20) var(--p) 100%);
    border: 1px solid var(--line);
    box-shadow: 0 12px 25px rgba(15,23,42,.07);
    position: relative;
  }
  .donut:after{
    content:'';
    position:absolute;
    inset: 14px;
    background: rgba(255,255,255,.95);
    border-radius: 999px;
    border: 1px solid var(--line);
  }
  .donut-center{
    position:absolute;
    inset: 0;
    display:flex;
    align-items:center;
    justify-content:center;
    font-weight: 980;
    z-index: 1;
    font-size: 16px;
    color:#111827;
    flex-direction: column;
  }
  .donut-center span{font-size: 11px; color: var(--muted); font-weight: 800; margin-top: 2px;}

  .mini{
    font-size: 12px;
    color: var(--muted);
  }
</style>
</head>
<body>
{% if nav %}
  <div class="wrap">
    {{nav|safe}}
    <div class="pagehead">
      <div class="h1">{{title}}</div>
      <div class="sub">{{subtitle}}</div>
    </div>
    {{content|safe}}
  </div>
{% else %}
  {{content|safe}}
{% endif %}
</body>
</html>
"""


# -------------------------
# Files
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
            return redirect(url_for("index"))
        err = "Usuario o PIN incorrecto."

    content = f"""
    <div class="login-shell">
      <div class="login-grid">
        <div class="hero">
          <h1>Panel operativo<br/>Transporte360</h1>
          <p>Viajes, repostajes, tacógrafo y control de costes. Todo en un mismo sitio, rápido y claro.</p>
          <div class="badge">⚡ Diseño limpio · Flujo simple · Datos reales</div>
          <div style="margin-top:16px" class="mini">
            Tip: Admin PIN <b>9999</b> · Mohsin PIN <b>1111</b> (cámbialos cuando quieras).
          </div>
        </div>

        <div class="card" style="padding:18px; border-radius:24px;">
          <h3 style="font-size:16px; margin-bottom:6px;">Acceso</h3>
          <div class="muted">Entrar con usuario y PIN</div>
          <form method="POST" style="margin-top:10px;">
            <label>Usuario</label>
            <input name="username" placeholder="Admin / Mohsin" required>
            <label>PIN</label>
            <input name="pin" type="password" placeholder="••••" required>
            <button class="btn" type="submit" style="margin-top:12px; width:100%;">Entrar</button>
          </form>
          {f"<div class='pill bad' style='margin-top:12px;'>❌ {err}</div>" if err else ""}
          <div class="mini" style="margin-top:12px;">
            Si quieres, después lo pasamos a usuarios con contraseña + roles más finos.
          </div>
        </div>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Transporte360", subtitle="Acceso", content=content, nav="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -------------------------
# Home + Dashboard
# -------------------------
@app.route("/")
@login_required
def index():
    u = current_user()
    content = f"""
    <div class="grid kpi-grid">
      <div class="card col-8">
        <h3>Bienvenido</h3>
        <div class="h1" style="font-size:22px;">👋 {u['username']}</div>
        <div class="muted">Usa el menú para registrar operación (viajes, repostajes, tacógrafo) y ver el control.</div>
        <div style="margin-top:12px;" class="split">
          <a class="btn" href="/viajes">➕ Nuevo viaje</a>
          <a class="btn btn-ghost" href="/repostajes">⛽ Nuevo repostaje</a>
          <a class="btn btn-ghost" href="/tacografo">🕒 Tacógrafo</a>
        </div>
      </div>
      <div class="card col-4">
        <h3>Estado</h3>
        <div class="pill ok">✅ Sistema activo</div>
        <div style="margin-top:10px;" class="mini">
          Siguiente paso: cuando te pase el HTML de Rocket completo, clonamos sus secciones (sidebar, tablas, cards) una por una.
        </div>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Panel de Gestión", subtitle="Panel principal", content=content, nav=nav())


@app.route("/dashboard")
@manager_required
def dashboard():
    hoy = date.today().isoformat()
    mes = month_key(hoy)

    tarifa = get_setting_float("tarifa_km", 0.95)
    fijo_km = coste_fijo_por_km()
    gas_est = gasoil_estimado_mes()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM viajes WHERE substr(fecha_salida,1,7)=?", (mes,))
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

    cur.execute("SELECT SUM(importe) AS gasoil FROM repostajes WHERE substr(fecha,1,7)=?", (mes,))
    gas = cur.fetchone()
    gasoil_real = float(gas["gasoil"]) if gas and gas["gasoil"] is not None else 0.0

    cur.execute("""
      SELECT
        SUM(horas_conduccion) AS hc,
        SUM(horas_disponibilidad) AS hd
      FROM tacografo
      WHERE substr(fecha,1,7)=?
    """, (mes,))
    t = cur.fetchone()
    horas_conduccion = float(t["hc"]) if t and t["hc"] is not None else 0.0
    horas_disp = float(t["hd"]) if t and t["hd"] is not None else 0.0

    # actividad reciente (últimos 6 viajes)
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

    # “Donut” CSS variable
    donut_p = f"{pct_cargados:.1f}%"

    # mini “tendencias” (placeholders limpios, no inventamos)
    trend_km = "↗︎" if km_total > 0 else "—"
    trend_ing = "↗︎" if ingresos > 0 else "—"
    trend_ben = "↗︎" if beneficio >= 0 else "↘︎"

    # lista actividad
    act = ""
    for r in recientes:
        dist = calc_distancia(r["km_inicio"], r["km_fin"])
        tipo = r["tipo_tramo"]
        pill = "ok" if tipo == "CARGADO" else "warn"
        act += f"""
        <div class="card" style="box-shadow:none; margin:0; border-radius:16px;">
          <div style="display:flex; justify-content:space-between; gap:10px; align-items:flex-start;">
            <div>
              <div style="font-weight:950;">{r['conductor_label'] or '—'} · <span class="muted">{r['camion_label'] or '—'}</span></div>
              <div class="mini" style="margin-top:6px;">📅 {r['fecha_salida']} · 🧭 {r['origen']} → {r['destino']} · 📏 {dist:.0f} km</div>
              <div class="mini" style="margin-top:6px;">💶 Ingreso: <b>{float(r['ingreso'] or 0):.2f} €</b></div>
            </div>
            <div class="pill {pill}">{'Cargado' if tipo=='CARGADO' else 'Vacío'}</div>
          </div>
        </div>
        """

    content = f"""
    <div class="grid kpi-grid">
      <div class="card col-12">
        <div style="display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:flex-end;">
          <div>
            <div class="pill">📅 Mes: <b>{mes}</b></div>
            <div class="muted" style="margin-top:8px;">
              Resumen mensual de operaciones · Tarifa <b>{tarifa:.2f} €/km</b> · Fijo/km <b>{fijo_km:.3f}</b>
            </div>
          </div>
          <a class="btn btn-ghost" href="/ajustes">⚙️ Ajustes</a>
        </div>
      </div>

      <div class="card col-3">
        <div class="kpi">
          <div>
            <div class="label">Kilómetros Totales {trend_km}</div>
            <div class="value">{km_total:,.0f}</div>
            <div class="note">KM vacíos: <b>{km_vacios:,.0f}</b> ({pct_vacios:.1f}%)</div>
          </div>
          <div class="icon">🚛</div>
        </div>
      </div>

      <div class="card col-3">
        <div class="kpi">
          <div>
            <div class="label">Kilómetros Vacíos</div>
            <div class="value">{km_vacios:,.0f}</div>
            <div class="note">Objetivo: bajar el % vacío</div>
          </div>
          <div class="icon">🛣️</div>
        </div>
      </div>

      <div class="card col-3">
        <div class="kpi">
          <div>
            <div class="label">Ingresos Totales {trend_ing}</div>
            <div class="value">€{ingresos:,.2f}</div>
            <div class="note">Ruta fija por tarifa/km</div>
          </div>
          <div class="icon">💶</div>
        </div>
      </div>

      <div class="card col-3">
        <div class="kpi">
          <div>
            <div class="label">Beneficio Neto {trend_ben}</div>
            <div class="value">€{beneficio:,.2f}</div>
            <div class="note">Incluye fijo imput + gasoil real</div>
          </div>
          <div class="icon">📈</div>
        </div>
      </div>

      <div class="card col-6">
        <h3>Costes Variables</h3>
        <div class="split">
          <div>
            <div class="muted">Peajes + Parking</div>
            <div style="font-size:22px; font-weight:980;">€{coste_var:,.2f}</div>
            <div class="mini" style="margin-top:6px;">(desde Viajes)</div>
          </div>
          <div>
            <div class="muted">Combustible Real</div>
            <div style="font-size:22px; font-weight:980;">€{gasoil_real:,.2f}</div>
            <div class="mini" style="margin-top:6px;">(desde Repostajes)</div>
          </div>
          <div>
            <div class="muted">Gasoil Estimado</div>
            <div style="font-size:22px; font-weight:980;">€{gas_est:,.2f}</div>
            <div class="mini" style="margin-top:6px;">(km_obj · consumo · €/L)</div>
          </div>
        </div>
      </div>

      <div class="card col-6">
        <h3>Distribución de Viajes (por KM)</h3>
        <div style="display:flex; gap:14px; align-items:center; flex-wrap:wrap;">
          <div class="donut" style="--p:{donut_p};">
            <div class="donut-center">{pct_cargados:.0f}%<span>Cargados</span></div>
          </div>
          <div>
            <div class="pill ok">Cargados: <b>{pct_cargados:.1f}%</b></div>
            <div class="pill warn" style="margin-left:8px;">Vacíos: <b>{pct_vacios:.1f}%</b></div>
            <div class="mini" style="margin-top:10px;">
              Total KM: <b>{km_total:,.0f}</b> · Vacíos: <b>{km_vacios:,.0f}</b>
            </div>
            <div class="mini" style="margin-top:6px;">
              🕒 Tacógrafo: conducción <b>{horas_conduccion:.2f} h</b> · disponibilidad <b>{horas_disp:.2f} h</b>
            </div>
          </div>
        </div>
      </div>

      <div class="card col-12">
        <h3>Actividad Reciente</h3>
        <div class="grid" style="grid-template-columns: repeat(12, 1fr); gap:10px;">
          <div class="col-12" style="display:grid; gap:10px;">
            {act if act else "<div class='muted'>Sin viajes todavía.</div>"}
          </div>
        </div>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Dashboard", subtitle="KPIs del mes", content=content, nav=nav())


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

    salario = get_setting_float("salario_chofer_mes", 3100)
    km_obj = get_setting_float("km_objetivo_mes", 12000)
    alquiler = get_setting_float("alquiler_camion_mes", 1650)
    gestoria = get_setting_float("gestoria_mes", 250)
    autonomo = get_setting_float("autonomo_mes", 300)
    domiciliacion = get_setting_float("domiciliacion_mes", 30)
    seguro_anual = get_setting_float("seguro_mercancias_anual", 1200)
    tarifa_km = get_setting_float("tarifa_km", 0.95)

    consumo = get_setting_float("consumo_l_100", 30)
    precio_gas = get_setting_float("precio_gasoil_est", 1.09)
    gas_est = gasoil_estimado_mes()

    content = f"""
    <div class="grid kpi-grid">
      <div class="card col-12">
        <h3>Estructura de costes y parámetros</h3>
        <div class="muted">Estos valores alimentan el dashboard y los cálculos por km.</div>
      </div>

      <div class="card col-12">
        <form method="POST">
          <div class="grid kpi-grid">
            <div class="col-4">
              <label>Tarifa €/km (ruta fija)</label>
              <input name="tarifa_km" type="number" step="0.01" value="{tarifa_km}">
              <label>KM objetivo mes</label>
              <input name="km_objetivo_mes" type="number" step="1" value="{km_obj}">
            </div>

            <div class="col-4">
              <label>Coste empresa chófer / mes (€)</label>
              <input name="salario_chofer_mes" type="number" step="0.01" value="{salario}">
              <label>Alquiler camión / mes (€)</label>
              <input name="alquiler_camion_mes" type="number" step="0.01" value="{alquiler}">
            </div>

            <div class="col-4">
              <label>Gestoría / mes (€)</label>
              <input name="gestoria_mes" type="number" step="0.01" value="{gestoria}">
              <label>Cuota autónomo / mes (€)</label>
              <input name="autonomo_mes" type="number" step="0.01" value="{autonomo}">
            </div>

            <div class="col-4">
              <label>Domiciliación / mes (€)</label>
              <input name="domiciliacion_mes" type="number" step="0.01" value="{domiciliacion}">
              <label>Seguro mercancías / año (€)</label>
              <input name="seguro_mercancias_anual" type="number" step="0.01" value="{seguro_anual}">
            </div>

            <div class="col-4">
              <label>Consumo estimado (L/100 km)</label>
              <input name="consumo_l_100" type="number" step="0.1" value="{consumo}">
              <div class="mini">Ejemplo: 30 L/100km</div>
            </div>

            <div class="col-4">
              <label>Precio gasoil estimado (€/L)</label>
              <input name="precio_gasoil_est" type="number" step="0.01" value="{precio_gas}">
              <div class="mini">Ejemplo: 1.09 €/L</div>
              <label>Gasoil estimado mes (auto)</label>
              <input disabled value="{gas_est:.2f} €">
            </div>
          </div>

          <button class="btn" type="submit" style="margin-top:12px;">Guardar ajustes</button>
        </form>
      </div>

      <div class="card col-12">
        <div class="pill">Coste fijo total mes: <b>{coste_fijo_total_mes():.2f} €</b></div>
        <div class="pill" style="margin-left:8px;">Fijo por km: <b>{coste_fijo_por_km():.3f} €/km</b></div>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Ajustes", subtitle="Estructura + previsión de combustible", content=content, nav=nav())


# -------------------------
# Camiones (manager)
# -------------------------
@app.route("/camiones", methods=["GET", "POST"])
@manager_required
def camiones():
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

    cur.execute("SELECT id, matricula AS label, descripcion FROM camiones ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()

    rows_html = "".join([f"<tr><td>{r['id']}</td><td><b>{r['label']}</b></td><td>{r['descripcion'] or ''}</td></tr>" for r in rows])

    content = f"""
    <div class="grid kpi-grid">
      <div class="card col-12">
        <h3>Alta de vehículos</h3>
        <form method="POST" class="grid kpi-grid">
          <div class="col-4">
            <label>Matrícula</label>
            <input name="matricula" placeholder="1234ABC" required>
          </div>
          <div class="col-8">
            <label>Descripción</label>
            <input name="descripcion" placeholder="MAN TGX / Renault T...">
          </div>
          <div class="col-12">
            <button class="btn" type="submit">Añadir camión</button>
          </div>
        </form>
      </div>

      <div class="col-12">
        <table>
          <tr><th>ID</th><th>Matrícula</th><th>Descripción</th></tr>
          {rows_html if rows_html else "<tr><td colspan='3' class='muted'>Sin camiones aún</td></tr>"}
        </table>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Camiones", subtitle="Gestión de flota", content=content, nav=nav())


# -------------------------
# Conductores + Crear usuario+PIN (manager)
# -------------------------
@app.route("/conductores", methods=["GET", "POST"])
@manager_required
def conductores():
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

    rows_html = "".join([f"<tr><td>{r['id']}</td><td><b>{r['nombre']}</b></td><td>{r['dni'] or ''}</td><td>{r['telefono'] or ''}</td></tr>" for r in rows])

    u_html = ""
    for ur in urows:
        u_html += f"<tr><td><b>{ur['username']}</b></td><td>{ur['conductor_id'] or '-'}</td><td>{'Sí' if ur['active'] else 'No'}</td></tr>"

    content = f"""
    <div class="grid kpi-grid">
      <div class="card col-12">
        <h3>Alta de conductor</h3>
        <form method="POST" class="grid kpi-grid">
          <div class="col-4">
            <label>Nombre</label>
            <input name="nombre" placeholder="Mohsin" required>
            <label>DNI (opcional)</label>
            <input name="dni" placeholder="12345678X">
          </div>

          <div class="col-4">
            <label>Teléfono (opcional)</label>
            <input name="telefono" placeholder="600000000">
            <label>Crear usuario driver con PIN</label>
            <select name="crear_usuario">
              <option value="0" selected>No</option>
              <option value="1">Sí</option>
            </select>
          </div>

          <div class="col-4">
            <label>PIN (si creas usuario)</label>
            <input name="pin" placeholder="1111" type="password">
            <div class="mini">El usuario se llamará igual que el conductor (nombre).</div>
          </div>

          <div class="col-12">
            <button class="btn" type="submit">Guardar</button>
          </div>
        </form>
      </div>

      <div class="col-6">
        <table>
          <tr><th>ID</th><th>Nombre</th><th>DNI</th><th>Teléfono</th></tr>
          {rows_html if rows_html else "<tr><td colspan='4' class='muted'>Sin conductores aún</td></tr>"}
        </table>
      </div>

      <div class="col-6">
        <table>
          <tr><th>Usuario</th><th>Conductor ID</th><th>Activo</th></tr>
          {u_html if u_html else "<tr><td colspan='3' class='muted'>Sin usuarios driver</td></tr>"}
        </table>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Conductores", subtitle="Alta + creación de usuario driver", content=content, nav=nav())


# -------------------------
# Helpers: conductor_id del usuario driver
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
        except:
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
    fijo_km = coste_fijo_por_km()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, matricula AS label FROM camiones ORDER BY matricula ASC")
    camiones_list = cur.fetchall()
    cur.execute("SELECT id, nombre AS label FROM conductores ORDER BY nombre ASC")
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

    total_km = 0.0
    for r in rows:
        total_km += calc_distancia(r["km_inicio"], r["km_fin"])

    options_camiones = '<option value="">--</option>' + "".join([f'<option value="{c["id"]}">{c["label"]}</option>' for c in camiones_list])

    options_conductores = ""
    if u["role"] == "manager":
        options_conductores = '<option value="">--</option>' + "".join([f'<option value="{d["id"]}">{d["label"]}</option>' for d in conductores_list])

    ingreso_field = ""
    if u["role"] == "manager":
        ingreso_field = """
          <label>Ingreso (opcional)</label>
          <input name="ingreso" type="number" step="0.01" placeholder="si vacío: auto por tarifa/km (solo cargado)">
        """

    conductor_field = ""
    if u["role"] == "manager":
        conductor_field = f"""
          <label>Conductor</label>
          <select name="conductor_id">{options_conductores}</select>
        """

    table_rows = ""
    if u["role"] == "manager":
        table_header = """
          <th>Tipo</th><th>Salida</th><th>Origen</th><th>Destino</th><th>Peso</th>
          <th>Camión</th><th>Conductor</th><th>KM</th>
          <th>Ingreso</th><th>Coste var</th><th>Fijo imput</th><th>Beneficio</th><th>CMR</th>
        """
        total_ing = total_var = total_fijo = total_ben = 0.0
        for r in rows:
            dist = calc_distancia(r["km_inicio"], r["km_fin"])
            var = float(r["peajes"] or 0) + float(r["parking"] or 0)
            fijo = dist * fijo_km
            ben = float(r["ingreso"] or 0) - (var + fijo)
            total_ing += float(r["ingreso"] or 0)
            total_var += var
            total_fijo += fijo
            total_ben += ben

            cmr_link = "-"
            if r["cmr_path"]:
                cmr_link = f"<a class='btn btn-ghost' style='padding:6px 10px; border-radius:12px;' href='/uploads/{r['cmr_path']}' target='_blank'>Ver</a>"

            table_rows += f"""
            <tr>
              <td><b>{r['tipo_tramo']}</b></td>
              <td>{r['fecha_salida']}</td>
              <td>{r['origen']}</td>
              <td>{r['destino']}</td>
              <td>{float(r['peso_kg'] or 0):.0f} kg</td>
              <td>{r['camion_label'] or '-'}</td>
              <td>{r['conductor_label'] or '-'}</td>
              <td>{dist:.0f}</td>
              <td>€{float(r['ingreso'] or 0):.2f}</td>
              <td>€{var:.2f}</td>
              <td>€{fijo:.2f}</td>
              <td><b>€{ben:.2f}</b></td>
              <td>{cmr_link}</td>
            </tr>
            """

        totales_html = f"""
        <div class="card" style="margin-top:12px;">
          <div class="pill">Viajes: <b>{len(rows)}</b></div>
          <div class="pill" style="margin-left:8px;">Km: <b>{total_km:.0f}</b></div>
          <div class="pill" style="margin-left:8px;">Ingresos: <b>€{total_ing:.2f}</b></div>
          <div class="pill" style="margin-left:8px;">Var: <b>€{total_var:.2f}</b></div>
          <div class="pill" style="margin-left:8px;">Fijo imput: <b>€{total_fijo:.2f}</b></div>
          <div class="pill" style="margin-left:8px;">Beneficio: <b>€{total_ben:.2f}</b></div>
          <div class="mini" style="margin-top:8px;">* Beneficio aquí NO incluye gasoil (va por Repostajes).</div>
          <div style="margin-top:10px;">
            <a class="btn" href="/export_viajes.csv">Exportar viajes CSV</a>
          </div>
        </div>
        """
    else:
        table_header = """
          <th>Tipo</th><th>Salida</th><th>Origen</th><th>Destino</th><th>Peso</th>
          <th>Camión</th><th>KM</th><th>CMR</th>
        """
        for r in rows:
            dist = calc_distancia(r["km_inicio"], r["km_fin"])
            cmr_link = "-"
            if r["cmr_path"]:
                cmr_link = f"<a class='btn btn-ghost' style='padding:6px 10px; border-radius:12px;' href='/uploads/{r['cmr_path']}' target='_blank'>Ver</a>"
            peso_txt = f"{float(r['peso_kg'] or 0):.0f} kg" if r["tipo_tramo"] == "CARGADO" else "-"
            table_rows += f"""
            <tr>
              <td><b>{r['tipo_tramo']}</b></td>
              <td>{r['fecha_salida']}</td>
              <td>{r['origen']}</td>
              <td>{r['destino']}</td>
              <td>{peso_txt}</td>
              <td>{r['camion_label'] or '-'}</td>
              <td>{dist:.0f}</td>
              <td>{cmr_link if r['tipo_tramo']=='CARGADO' else '-'}</td>
            </tr>
            """
        totales_html = f"""
        <div class="card" style="margin-top:12px;">
          <div class="pill">Viajes: <b>{len(rows)}</b></div>
          <div class="pill" style="margin-left:8px;">Km: <b>{total_km:.0f}</b></div>
        </div>
        """

    js = """
    <script>
      function toggleTramo(){
        const t = document.getElementById('tipo_tramo').value;
        const cargado = (t === 'CARGADO');
        document.querySelectorAll('.only-cargado').forEach(el => el.style.display = cargado ? 'block' : 'none');
      }
      document.addEventListener('DOMContentLoaded', toggleTramo);
    </script>
    """

    content = f"""
    <div class="grid kpi-grid">
      <div class="card col-12">
        <h3>Registro de viaje</h3>
        {f"<div class='pill bad' style='margin-bottom:10px;'>❌ {msg}</div>" if msg else ""}
        <form method="POST" enctype="multipart/form-data" class="grid kpi-grid">
          <div class="col-4">
            <label>Tipo de tramo</label>
            <select id="tipo_tramo" name="tipo_tramo" onchange="toggleTramo()">
              <option value="CARGADO" selected>CARGADO</option>
              <option value="VACIO">VACÍO</option>
            </select>

            <label>Fecha salida</label>
            <input type="date" name="fecha_salida" required>

            <label>Fecha llegada</label>
            <input type="date" name="fecha_llegada" required>

            <label>Origen</label>
            <input name="origen" value="Barcelona" required>

            <label>Destino</label>
            <input name="destino" value="Vitoria" required>

            <div class="only-cargado">
              <label>Peso (kg)</label>
              <input name="peso_kg" type="number" step="1" value="0">
              {ingreso_field}
            </div>
          </div>

          <div class="col-4">
            <label>Camión</label>
            <select name="camion_id">{options_camiones}</select>

            {conductor_field}

            <label>KM inicio (odómetro)</label>
            <input name="km_inicio" type="number" step="1">
            <div class="mini">Si lo dejas vacío, se usa el último km_fin del camión (driver).</div>

            <label>KM fin (odómetro)</label>
            <input name="km_fin" type="number" step="1" required>

            <div class="only-cargado">
              <label>Adjuntar CMR</label>
              <input name="cmr_file" type="file" accept=".pdf,.jpg,.jpeg,.png">
            </div>
          </div>

          <div class="col-4">
            <div class="only-cargado">
              <label>Peajes (€)</label>
              <input name="peajes" type="number" step="0.01" value="0">

              <label>Parking (€)</label>
              <input name="parking" type="number" step="0.01" value="0">
            </div>

            <button class="btn" type="submit" style="margin-top:14px; width:100%;">Guardar tramo</button>
          </div>
        </form>
      </div>

      <div class="col-12">
        {totales_html}
      </div>

      <div class="col-12">
        <table>
          <tr>{table_header}</tr>
          {table_rows if table_rows else "<tr><td colspan='12' class='muted'>Sin registros aún</td></tr>"}
        </table>
      </div>
    </div>

    {js}
    """
    return render_template_string(BASE_HTML, title="Viajes", subtitle="Registro operativo", content=content, nav=nav())


# -------------------------
# Repostajes (ticket obligatorio)
# -------------------------
@app.route("/repostajes", methods=["GET", "POST"])
@login_required
def repostajes():
    u = current_user()
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, matricula AS label FROM camiones ORDER BY matricula ASC")
    camiones_list = cur.fetchall()
    cur.execute("SELECT id, nombre AS label FROM conductores ORDER BY nombre ASC")
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

    options_camiones = '<option value="">--</option>' + "".join([f'<option value="{c["id"]}">{c["label"]}</option>' for c in camiones_list])

    conductor_field = ""
    if u["role"] == "manager":
        options_conductores = '<option value="">--</option>' + "".join([f'<option value="{d["id"]}">{d["label"]}</option>' for d in conductores_list])
        conductor_field = f"""
          <label>Conductor</label>
          <select name="conductor_id">{options_conductores}</select>
        """

    table_rows = ""
    for r in rows:
        tlink = f"<a class='btn btn-ghost' style='padding:6px 10px; border-radius:12px;' href='/uploads/{r['ticket_path']}' target='_blank'>Ver</a>"
        table_rows += f"""
        <tr>
          <td>{r['fecha']}</td>
          <td>{r['camion_label'] or '-'}</td>
          <td>{r['conductor_label'] or '-'}</td>
          <td>{float(r['litros'] or 0):.1f} L</td>
          <td>{float(r['precio_litro'] or 0):.3f} €</td>
          <td><b>€{float(r['importe'] or 0):.2f}</b></td>
          <td>{(r['km_odometro'] if r['km_odometro'] is not None else '-') }</td>
          <td>{r['estacion'] or ''}</td>
          <td>{tlink}</td>
        </tr>
        """

    content = f"""
    <div class="grid kpi-grid">
      <div class="card col-12">
        <h3>Registro de repostaje</h3>
        {f"<div class='pill bad' style='margin-bottom:10px;'>❌ {msg}</div>" if msg else ""}
        <form method="POST" enctype="multipart/form-data" class="grid kpi-grid">
          <div class="col-4">
            <label>Fecha</label>
            <input type="date" name="fecha" required>
            <label>Camión</label>
            <select name="camion_id">{options_camiones}</select>
            {conductor_field}
          </div>
          <div class="col-4">
            <label>Litros</label>
            <input name="litros" type="number" step="0.01" required>
            <label>Precio por litro (€)</label>
            <input name="precio_litro" type="number" step="0.001" required>
            <label>KM odómetro (opcional)</label>
            <input name="km_odometro" type="number" step="1">
          </div>
          <div class="col-4">
            <label>Estación (opcional)</label>
            <input name="estacion" placeholder="Repsol / Shell...">
            <label>Adjuntar ticket (obligatorio)</label>
            <input name="ticket_file" type="file" accept=".pdf,.jpg,.jpeg,.png" required>
            <button class="btn" type="submit" style="margin-top:14px; width:100%;">Guardar repostaje</button>
          </div>
        </form>
      </div>

      <div class="col-12">
        <table>
          <tr><th>Fecha</th><th>Camión</th><th>Conductor</th><th>Litros</th><th>€/L</th><th>Importe</th><th>KM</th><th>Estación</th><th>Ticket</th></tr>
          {table_rows if table_rows else "<tr><td colspan='9' class='muted'>Sin registros aún</td></tr>"}
        </table>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Repostajes", subtitle="Registro de gasoil", content=content, nav=nav())


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
        content = "<div class='card'><div class='pill bad'>❌ No hay conductores.</div></div>"
        return render_template_string(BASE_HTML, title="Tacógrafo", subtitle="Horas manuales", content=content, nav=nav())

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

    cur.execute("SELECT * FROM tacografo WHERE conductor_id=? AND fecha=?", (conductor_id, fecha_sel))
    row = cur.fetchone()
    horas_cond_dia = float(row["horas_conduccion"]) if row else 0.0
    horas_disp_dia = float(row["horas_disponibilidad"]) if row else 0.0
    horas_desc_dia = float(row["horas_descanso"]) if row else 11.0
    comentario_dia = row["comentario"] if row else ""

    fecha_dt = datetime.fromisoformat(fecha_sel).date()
    inicio = fecha_dt - timedelta(days=6)
    cur.execute("""
      SELECT * FROM tacografo
      WHERE conductor_id=? AND fecha BETWEEN ? AND ?
      ORDER BY fecha ASC
    """, (conductor_id, inicio.isoformat(), fecha_dt.isoformat()))
    semana = cur.fetchall()

    total_sem_cond = sum([float(x["horas_conduccion"] or 0) for x in semana])
    total_sem_disp = sum([float(x["horas_disponibilidad"] or 0) for x in semana])
    total_sem_desc = sum([float(x["horas_descanso"] or 0) for x in semana])
    conn.close()

    selector = ""
    if u["role"] == "manager":
        options = ""
        for c in conductores_list:
            sel = "selected" if str(c["id"]) == str(conductor_id) else ""
            options += f'<option value="{c["id"]}" {sel}>{c["nombre"]}</option>'
        selector = f"""
          <label>Conductor</label>
          <select name="conductor_id">{options}</select>
        """

    filas = ""
    for s in semana:
        filas += (
            "<tr>"
            f"<td>{s['fecha']}</td>"
            f"<td>{float(s['horas_conduccion'] or 0):.2f}</td>"
            f"<td>{float(s['horas_disponibilidad'] or 0):.2f}</td>"
            f"<td>{float(s['horas_descanso'] or 0):.2f}</td>"
            "</tr>"
        )

    content = f"""
    <div class="grid kpi-grid">
      <div class="card col-12">
        <h3>Horas (manual)</h3>
        {f"<div class='pill ok' style='margin-bottom:10px;'>✅ {msg}</div>" if msg else ""}
        <form method="POST" class="grid kpi-grid">
          <div class="col-4">
            {selector}
            <label>Fecha</label>
            <input type="date" name="fecha" value="{fecha_sel}">
          </div>

          <div class="col-4">
            <label>Horas conducción</label>
            <input type="number" step="0.25" name="horas_conduccion" value="{horas_cond_dia}">
            <label>Horas disponibilidad / espera</label>
            <input type="number" step="0.25" name="horas_disponibilidad" value="{horas_disp_dia}">
          </div>

          <div class="col-4">
            <label>Horas descanso diario</label>
            <input type="number" step="0.25" name="horas_descanso" value="{horas_desc_dia}">
            <div class="mini">Por defecto 11h. Si fue reducido, cámbialo.</div>
            <label>Comentario</label>
            <input name="comentario" value="{comentario_dia}">
            <button class="btn" type="submit" style="margin-top:14px; width:100%;">Guardar</button>
          </div>
        </form>
      </div>

      <div class="card col-12">
        <div class="pill">Semana (7 días) · Conducción: <b>{total_sem_cond:.2f} h</b></div>
        <div class="pill" style="margin-left:8px;">Disponibilidad: <b>{total_sem_disp:.2f} h</b></div>
        <div class="pill" style="margin-left:8px;">Descanso: <b>{total_sem_desc:.2f} h</b></div>
      </div>

      <div class="col-12">
        <table>
          <tr><th>Fecha</th><th>Conducción</th><th>Disponibilidad</th><th>Descanso</th></tr>
          {filas if filas else "<tr><td colspan='4' class='muted'>Sin datos aún</td></tr>"}
        </table>
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, title="Tacógrafo", subtitle="Conducción · Disponibilidad · Descanso", content=content, nav=nav())


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



