from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "CAMBIA-ESTO-POR-UNA-CLAVE-LARGA-UNICA"

USERS = {
    "manager": {
        "pin": "1234",
        "role": "manager",
        "name": "Carlos Rodríguez"
    },
    "driver1": {
        "pin": "5678",
        "role": "driver",
        "name": "Miguel Fernández"
    },
    "driver2": {
        "pin": "9012",
        "role": "driver",
        "name": "Ana Martínez"
    },
}

def current_user():
    return session.get("user")

@app.get("/login")
def login_get():
    if current_user():
        return redirect(url_for("home"))
    return render_template("pages/login.html", error=None)

@app.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    pin = (request.form.get("pin") or "").strip()

    u = USERS.get(username)
    if not u or u["pin"] != pin:
        return render_template("pages/login.html", error="Usuario o PIN incorrectos")

    session["user"] = {
        "username": username,
        "role": u["role"],
        "name": u["name"]
    }
    return redirect(url_for("home"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_get"))

@app.get("/")
def home():
    if not current_user():
        return redirect(url_for("login_get"))

    user = current_user()
    return render_template("pages/index.html", user=user)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
