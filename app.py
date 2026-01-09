from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "CAMBIA-ESTO-POR-UNA-CLAVE-LARGA-UNICA"

USERS = {
    "manager": {"pin": "1234", "role": "manager", "name": "Carlos Rodríguez"},
    "driver1": {"pin": "5678", "role": "driver", "name": "Miguel Fernández"},
    "driver2": {"pin": "9012", "role": "driver", "name": "Ana Martínez"},
}

def current_user():
    return session.get("user")

def login_required():
    if not current_user():
        return False
    return True

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
    if not login_required():
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

@app.get("/viajes")
def viajes():
    if not login_required():
        return redirect(url_for("login_get"))
    user = current_user()
    return render_template(
        "pages/viajes.html",
        user=user,
        title="Viajes - Transporte360",
        page_title="Viajes",
        page_subtitle="Registro operativo",
        active_page="viajes",
    )

@app.get("/repostajes")
def repostajes():
    if not login_required():
        return redirect(url_for("login_get"))
    user = current_user()
    return render_template(
        "pages/repostajes.html",
        user=user,
        title="Repostajes - Transporte360",
        page_title="Repostajes",
        page_subtitle="Registro de gasoil",
        active_page="repostajes",
    )

@app.get("/tacografo")
def tacografo():
    if not login_required():
        return redirect(url_for("login_get"))
    user = current_user()
    return render_template(
        "pages/tacografo.html",
        user=user,
        title="Tacógrafo - Transporte360",
        page_title="Tacógrafo",
        page_subtitle="Horas manuales",
        active_page="tacografo",
    )

# Rutas manager (por ahora placeholders)
@app.get("/conductores")
def conductores():
    if not login_required():
        return redirect(url_for("login_get"))
    user = current_user()
    if user["role"] != "manager":
        return ("Forbidden", 403)
    return render_template(
        "pages/conductores.html",
        user=user,
        title="Conductores - Transporte360",
        page_title="Conductores",
        page_subtitle="Alta y gestión",
        active_page="conductores",
    )

@app.get("/camiones")
def camiones():
    if not login_required():
        return redirect(url_for("login_get"))
    user = current_user()
    if user["role"] != "manager":
        return ("Forbidden", 403)
    return render_template(
        "pages/camiones.html",
        user=user,
        title="Camiones - Transporte360",
        page_title="Camiones",
        page_subtitle="Alta de vehículos",
        active_page="camiones",
    )

@app.get("/ajustes")
def ajustes():
    if not login_required():
        return redirect(url_for("login_get"))
    user = current_user()
    if user["role"] != "manager":
        return ("Forbidden", 403)
    return render_template(
        "pages/ajustes.html",
        user=user,
        title="Ajustes - Transporte360",
        page_title="Ajustes",
        page_subtitle="Parámetros del sistema",
        active_page="ajustes",
    )

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

