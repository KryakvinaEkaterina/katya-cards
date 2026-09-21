import os
import sqlite3
from datetime import datetime, timedelta
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, send_file)
from openpyxl import Workbook
from io import BytesIO

app = Flask(__name__)
app.secret_key = "katya-secret-2026"

# ============ НАСТРОЙКИ ЦЕН ============
PRODUCTS = {
    "sber":   {"name": "Сбер",         "rate": 1000},
    "tbank":  {"name": "Т-Банк",       "rate": 1500},
    "yandex": {"name": "Яндекс Сплит", "rate": 1500},
}
AGENT_RATE = 250
LOGIN_USER = "katya"
LOGIN_PASS = "katya2026"
PLAN_DEFAULT = 5

# ============ БАЗА ДАННЫХ ============
DB_PATH = "katya_cards.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        who TEXT NOT NULL,
        product TEXT NOT NULL,
        qty INTEGER NOT NULL,
        date TEXT NOT NULL,
        amount REAL NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS advances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        who TEXT NOT NULL,
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        note TEXT
    )""")
    conn.commit()
    conn.close()

init_db()

# ============ ВСПОМОГАТЕЛЬНЫЕ ============
def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def calc_amount(who, product, qty):
    if who == "agent":
        return qty * AGENT_RATE
    return qty * PRODUCTS[product]["rate"]

def week_range(ref_date=None):
    if ref_date is None:
        ref_date = datetime.now()
    monday = ref_date - timedelta(days=ref_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")

def fmt_money(val):
    return f"{int(val):,} \u20bd".replace(",", " ")

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ============ МАРШРУТЫ ============

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == LOGIN_USER and p == LOGIN_PASS:
            session["logged_in"] = True
            return redirect(url_for("index"))
        flash("Неверный логин или пароль")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    today = today_str()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT SUM(qty) AS q, SUM(amount) AS a FROM issues WHERE who='katya' AND date=?", (today,))
    mine = c.fetchone()
    c.execute("SELECT SUM(qty) AS q, SUM(amount) AS a FROM issues WHERE who='agent' AND date=?", (today,))
    ag = c.fetchone()
    c.execute("SELECT SUM(qty) AS q, SUM(amount) AS a FROM issues WHERE date=?", (today,))
    total = c.fetchone()
    w_start, w_end = week_range()
    c.execute("SELECT SUM(qty) AS q FROM issues WHERE date BETWEEN ? AND ?", (w_start, w_end))
    week_qty = c.fetchone()["q"] or 0
    week_plan = PLAN_DEFAULT * 5
    conn.close()
    return render_template("index.html",
        today=today, my_qty=mine["q"] or 0, my_amt=mine["a"] or 0,
        ag_qty=ag["q"] or 0, ag_amt=ag["a"] or 0,
        total_qty=total["q"] or 0, total_amt=total["a"] or 0,
        plan=PLAN_DEFAULT, progress=min(mine["q"] or 0, PLAN_DEFAULT),
        week_qty=week_qty, week_plan=week_plan, fmt=fmt_money)

@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        who = request.form.get("who")
        product = request.form.get("product")
        qty = int(request.form.get("qty", 0))
        date = request.form.get("date", today_str())
        if who not in ("katya", "agent") or product not in PRODUCTS or qty < 1:
            flash("Проверьте данные — что-то заполнено неверно")
            return redirect(url_for("add"))
        amount = calc_amount(who, product, qty)
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO issues (who, product, qty, date, amount) VALUES (?,?,?,?,?)",
                  (who, product, qty, date, amount))
        conn.commit()
        conn.close()
        flash("Выдача добавлена!")
        return redirect(url_for("index"))
    return render_template("add.html", products=PRODUCTS, today=today_str())

@app.route("/edit/<int:rid>", methods=["GET", "POST"])
@login_required
def edit(rid):
    conn = get_db()
    c = conn.cursor()
    if request.method == "POST":
        who = request.form.get("who")
        product = request.form.get("product")
        qty = int(request.form.get("qty", 0))
        date = request.form.get("date", today_str())
        amount = calc_amount(who, product, qty)
        c.execute("UPDATE issues SET who=?, product=?, qty=?, date=?, amount=? WHERE id=?",
                  (who, product, qty, date, amount, rid))
        conn.commit()
        conn.close()
        flash("Запись обновлена!")
        return redirect(url_for("history"))
    c.execute("SELECT * FROM issues WHERE id=?", (rid,))
    row = c.fetchone()
    conn.close()
    if not row:
        flash("Запись не найдена")
        return redirect(url_for("history"))
    return render_template("edit.html", row=row, products=PRODUCTS)

@app.route("/delete/<int:rid>", methods=["POST"])
@login_required
def delete(rid):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM issues WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    flash("Запись удалена")
    return redirect(url_for("history"))

@app.route("/history")
@login_required
def history():
    q = request.args.get("q", "")
    conn = get_db()
    c = conn.cursor()
    if q:
        like = f"%{q}%"
        c.execute("""SELECT * FROM issues
                     WHERE date LIKE ? OR product LIKE ? OR who LIKE ?
                     ORDER BY date DESC, id DESC""", (like, like, like))
    else:
        c.execute("SELECT * FROM issues ORDER BY date DESC, id DESC")
    rows = c.fetchall()
    conn.close()
    return render_template("history.html", issues=rows, q=q, products=PRODUCTS)

@app.route("/stats")
@login_required
def stats():
    period = request.args.get("period", "week")
    today = datetime.now()
    if period == "today":
        d_start = d_end = today_str()
    elif period == "week":
        d_start, d_end = week_range()
    elif period == "month":
        d_start = today.replace(day=1).strftime("%Y-%m-%d")
        d_end = today_str()
    else:
        d_start = request.args.get("d_start", today_str())
        d_end = request.args.get("d_end", today_str())
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT date, SUM(qty) AS q, SUM(amount) AS a
                 FROM issues WHERE date BETWEEN ? AND ?
                 GROUP BY date ORDER BY date""", (d_start, d_end))
    by_day = c.fetchall()
    c.execute("""SELECT product, SUM(qty) AS q, SUM(amount) AS a
                 FROM issues WHERE date BETWEEN ? AND ?
                 GROUP BY product""", (d_start, d_end))
    by_product = c.fetchall()
    c.execute("""SELECT who, SUM(qty) AS q, SUM(amount) AS a
                 FROM issues WHERE date BETWEEN ? AND ?
                 GROUP BY who""", (d_start, d_end))
    by_who = c.fetchall()
    conn.close()
    days_labels = [r["date"] for r in by_day]
    days_qty = [r["q"] for r in by_day]
    days_amt = [r["a"] for r in by_day]
    prod_labels = [PRODUCTS[r["product"]]["name"] if r["product"] in PRODUCTS else r["product"] for r in by_product]
    prod_qty = [r["q"] for r in by_product]
    prod_amt = [r["a"] for r in by_product]
    return render_template("stats.html",
        period=period, d_start=d_start, d_end=d_end,
        by_day=by_day, by_product=by_product, by_who=by_who,
        days_labels=days_labels, days_qty=days_qty, days_amt=days_amt,
        prod_labels=prod_labels, prod_qty=prod_qty, prod_amt=prod_amt,
        products=PRODUCTS, fmt=fmt_money)

@app.route("/analysis")
@login_required
def analysis():
    conn = get_db()
    c = conn.cursor()
    today = datetime.now()
    eight_weeks_ago = today - timedelta(weeks=8)
    c.execute("""SELECT date, who, qty FROM issues
                 WHERE date >= ? ORDER BY date""", (eight_weeks_ago.strftime("%Y-%m-%d"),))
    rows = c.fetchall()
    weekday_stats = {0:[],1:[],2:[],3:[],4:[],5:[],6:[]}
    for r in rows:
        if r["who"] == "agent":
            dt = datetime.strptime(r["date"], "%Y-%m-%d")
            weekday_stats[dt.weekday()].append(r["qty"])
    weekday_names = ["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота","Воскресенье"]
    avg_by_day = []
    for i in range(7):
        vals = weekday_stats[i]
        avg = sum(vals) / len(vals) if vals else 0
        avg_by_day.append({"day": weekday_names[i], "avg": round(avg,1), "count": len(vals)})
    w_start, w_end = week_range()
    prev_monday = datetime.strptime(w_start, "%Y-%m-%d") - timedelta(days=7)
    prev_sunday = prev_monday + timedelta(days=6)
    c.execute("SELECT SUM(qty) AS q, SUM(amount) AS a FROM issues WHERE date BETWEEN ? AND ?", (w_start, w_end))
    cur_week = c.fetchone()
    c.execute("SELECT SUM(qty) AS q, SUM(amount) AS a FROM issues WHERE date BETWEEN ? AND ?",
              (prev_monday.strftime("%Y-%m-%d"), prev_sunday.strftime("%Y-%m-%d")))
    prev_week = c.fetchone()
    conn.close()
    rec_plan = {}
    for i in range(5):
        vals = weekday_stats[i]
        if vals:
            rec_plan[weekday_names[i][:2]] = round(sum(vals)/len(vals))
        else:
            rec_plan[weekday_names[i][:2]] = PLAN_DEFAULT
    return render_template("analysis.html",
        avg_by_day=avg_by_day,
        cur_qty=cur_week["q"] or 0, cur_amt=cur_week["a"] or 0,
        prev_qty=prev_week["q"] or 0, prev_amt=prev_week["a"] or 0,
        rec_plan=rec_plan, fmt=fmt_money)

@app.route("/money", methods=["GET", "POST"])
@login_required
def money():
    conn = get_db()
    c = conn.cursor()
    if request.method == "POST":
        who = request.form.get("advance_who")
        amount = float(request.form.get("advance_amount", 0))
        date = request.form.get("advance_date", today_str())
        note = request.form.get("advance_note", "")
        if who and amount > 0:
            c.execute("INSERT INTO advances (who, amount, date, note) VALUES (?,?,?,?)",
                      (who, amount, date, note))
            conn.commit()
            flash("Аванс добавлен!")
        else:
            flash("Проверьте данные аванса")
        conn.close()
        return redirect(url_for("money"))
    w_start, w_end = week_range()
    c.execute("SELECT SUM(amount) AS a FROM issues WHERE who='katya' AND date BETWEEN ? AND ?", (w_start, w_end))
    katya_direct = c.fetchone()["a"] or 0
    c.execute("SELECT SUM(amount) AS a FROM issues WHERE who='agent' AND date BETWEEN ? AND ?", (w_start, w_end))
    passive = c.fetchone()["a"] or 0
    c.execute("""SELECT product, SUM(qty) AS q, SUM(amount) AS a
                 FROM issues WHERE who='katya' AND date BETWEEN ? AND ?
                 GROUP BY product""", (w_start, w_end))
    katya_by_product = c.fetchall()
    c.execute("""SELECT product, SUM(qty) AS q
                 FROM issues WHERE who='agent' AND date BETWEEN ? AND ?
                 GROUP BY product""", (w_start, w_end))
    agent_by_product = c.fetchall()
    c.execute("SELECT * FROM advances WHERE date BETWEEN ? AND ? ORDER BY date DESC", (w_start, w_end))
    advances = c.fetchall()
    c.execute("SELECT SUM(amount) AS a FROM advances WHERE who='katya' AND date BETWEEN ? AND ?", (w_start, w_end))
    adv_katya = c.fetchone()["a"] or 0
    c.execute("SELECT SUM(amount) AS a FROM advances WHERE who='agent' AND date BETWEEN ? AND ?", (w_start, w_end))
    adv_agent = c.fetchone()["a"] or 0
    conn.close()
    total_income = katya_direct + passive
    to_pay_katya = total_income - adv_katya
    return render_template("money.html",
        w_start=w_start, w_end=w_end,
        katya_direct=katya_direct, passive=passive,
        total_income=total_income,
        katya_by_product=katya_by_product,
        agent_by_product=agent_by_product,
        advances=advances, adv_katya=adv_katya, adv_agent=adv_agent,
        to_pay_katya=max(0, to_pay_katya), to_pay_agent=0,
        products=PRODUCTS, fmt=fmt_money)

@app.route("/advance/delete/<int:rid>", methods=["POST"])
@login_required
def del_advance(rid):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM advances WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    flash("Аванс удалён")
    return redirect(url_for("money"))

@app.route("/export")
@login_required
def export():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM issues ORDER BY date DESC, id DESC")
    issues = c.fetchall()
    c.execute("SELECT * FROM advances ORDER BY date DESC, id DESC")
    advances = c.fetchall()
    conn.close()
    wb = Workbook()
    ws = wb.active
    ws.title = "Выдачи"
    ws.append(["ID", "Дата", "Кто", "Продукт", "Кол-во", "Сумма, руб"])
    for r in issues:
        who_label = "Катя" if r["who"] == "katya" else "Стажёрка"
        prod_label = PRODUCTS.get(r["product"], {}).get("name", r["product"])
        ws.append([r["id"], r["date"], who_label, prod_label, r["qty"], r["amount"]])
    ws2 = wb.create_sheet("Авансы")
    ws2.append(["ID", "Дата", "Кто", "Сумма, руб", "Заметка"])
    for r in advances:
        who_label = "Катя" if r["who"] == "katya" else "Стажёрка"
        ws2.append([r["id"], r["date"], who_label, r["amount"], r["note"] or ""])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="katya_cards_export.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
