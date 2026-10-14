import html, os, re, time

from collections import defaultdict

from datetime import date, datetime, time as dtime, timedelta

from decimal import Decimal

from typing import List, Optional

from zoneinfo import ZoneInfo

import psycopg2

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import HTMLResponse, RedirectResponse

from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field

from psycopg2.extras import Json

from .db import platform_tx, tenant_tx

from .security import check_pw, has, hash_pw, sign, verify

BUILT = {"website", "patients", "appointments", "opd_billing", "prescriptions", "reminders", "dashboard", "users", "audit", "emr", "ipd", "ipd_billing"}

app = FastAPI(title="White-label Hospital Platform", version="0.1.0")

# Local development CORS: allow the web frontend on localhost and LAN devices.
# Flutter native apps do not enforce browser CORS; this mainly affects browser clients.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://hospital-demo-phi.vercel.app",
    ],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1|192\.168\.29\.\d{1,3})(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Tenant"],
)

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# ---------------- helpers ----------------

def D(x):

    return Decimal(str(x))

def fy(d):

    y = d.year if d.month >= 4 else d.year - 1

    return f"{y % 100:02d}{(y + 1) % 100:02d}"

def tz(t):

    return ZoneInfo(t["settings"].get("timezone", "Asia/Kolkata"))

def today(t):

    return datetime.now(tz(t)).date()

def next_no(c, tid, key):

    c.execute("insert into counters(tenant_id,key,value) values(%s,%s,1) on conflict (tenant_id,key) "

              "do update set value=counters.value+1 returning value", (tid, key))

    return c.fetchone()["value"]

def audit(c, ctx, action, entity, eid=None, old=None, new=None):

    c.execute("insert into audit_log(tenant_id,user_id,username,action,entity,entity_id,old_value,new_value) values(%s,%s,%s,%s,%s,%s,%s,%s)",

              (ctx.tid, ctx.uid, ctx.username, action, entity, str(eid) if eid else None,

               Json(old, dumps=lambda x: __import__("json").dumps(x, default=str)) if old is not None else None,

               Json(new, dumps=lambda x: __import__("json").dumps(x, default=str)) if new is not None else None))

def queue(c, tid, to, body, after=None):

    c.execute("insert into message_queue(tenant_id,to_addr,body,send_after) values(%s,%s,%s,coalesce(%s,now()))", (tid, to, body, after))

def resolve_tenant(request, code=None):

    code = code or request.headers.get("x-tenant")

    host = (request.headers.get("host") or "").split(":")[0].lower()

    sel = "select t.*, p.modules as plan_modules from tenants t join plans p on p.code=t.plan where "

    with platform_tx() as c:

        if code:

            c.execute(sel + "t.code=%s", (code.lower(),))

        else:

            c.execute(sel + "t.domain=%s or t.code=split_part(%s,'.',1)", (host, host))

        t = c.fetchone()

    if not t:

        raise HTTPException(404, "Hospital not found")

    if t["status"] == "suspended":

        raise HTTPException(403, "Hospital account is suspended")

    t["modules"] = (set(t["plan_modules"]) | set(t["modules_add"])) - set(t["modules_remove"])

    return t

class Ctx:

    def __init__(s, t, p):

        s.t, s.tid, s.uid, s.username, s.role, s.doctor_id = t, t["id"], p["uid"], p["un"], p["role"], p.get("did")

def need(perm=None, module=None):

    def dep(request: Request):

        t = resolve_tenant(request)

        auth = request.headers.get("authorization", "")

        p = verify(auth[7:]) if auth.startswith("Bearer ") else None

        if not p or p.get("plat") or p.get("tid") != str(t["id"]):

            raise HTTPException(401, "Please log in")

        if module and module not in t["modules"]:

            raise HTTPException(403, f"Module '{module}' is not included in this hospital's plan")

        if perm and not has(p["role"], perm):

            raise HTTPException(403, "You do not have permission for this action")

        with tenant_tx(t["id"]) as c:

            c.execute("select active from users where id=%s", (p["uid"],))

            u = c.fetchone()

        if not u or not u["active"]:

            raise HTTPException(401, "User is disabled")

        return Ctx(t, p)

    return dep

def need_plat(*roles):

    def dep(request: Request):

        auth = request.headers.get("authorization", "")

        p = verify(auth[7:]) if auth.startswith("Bearer ") else None

        if not p or not p.get("plat") or (roles and p["role"] not in roles):

            raise HTTPException(401, "Platform login required")

        return p

    return dep

def branding(t):

    b = dict(t["branding"])

    b["primary"] = b["primary"] if HEX.match(b.get("primary", "")) else "#0b6e99"

    b["secondary"] = b["secondary"] if HEX.match(b.get("secondary", "")) else "#e8f4f8"

    if not str(b.get("logo_url", "")).startswith(("https://", "/")):

        b["logo_url"] = ""

    b["name"] = t["name"]

    return b

# ---------------- tenant config & auth ----------------

@app.get("/api/config")

def config(request: Request):

    t = resolve_tenant(request)

    return {"code": t["code"], "branding": branding(t), "modules": sorted(t["modules"]), "built": sorted(BUILT & t["modules"]),

            "plan": t["plan"], "settings": {k: v for k, v in t["settings"].items() if k in ("discount_limit_pct", "timezone")}}

class Login(BaseModel):

    username: str

    password: str

@app.post("/api/auth/login")

def login(b: Login, request: Request):

    t = resolve_tenant(request)

    with tenant_tx(t["id"]) as c:

        c.execute("select * from users where tenant_id=%s and username=%s and active", (t["id"], b.username.strip().lower()))

        u = c.fetchone()

    if not u or not check_pw(b.password, u["password_hash"]):

        raise HTTPException(401, "Wrong username or password")

    tok = sign({"tid": str(t["id"]), "uid": str(u["id"]), "un": u["username"], "role": u["role"], "did": str(u["doctor_id"]) if u["doctor_id"] else None})

    return {"token": tok, "user": {"name": u["name"], "role": u["role"], "doctor_id": u["doctor_id"]}}

# ---------------- patients ----------------

class PatientIn(BaseModel):

    name: str = Field(min_length=2)

    mobile: str = Field(pattern=r"^\d{10}$")

    gender: str = ""

    dob: Optional[date] = None

    address: str = ""

    force: bool = False

@app.post("/api/patients")

def add_patient(b: PatientIn, ctx: Ctx = Depends(need("patients:w", "patients"))):

    with tenant_tx(ctx.tid) as c:

        if not b.force:

            c.execute("select uhid,name,mobile from patients where mobile=%s and lower(name)=lower(%s)", (b.mobile, b.name.strip()))

            dup = c.fetchone()

            if dup:

                raise HTTPException(409, f"Possible duplicate: {dup['name']} ({dup['uhid']}) already has this mobile number")

        n = next_no(c, ctx.tid, "uhid")

        uhid = f"{ctx.t['settings'].get('uhid_prefix', 'PT')}{n:06d}"

        c.execute("insert into patients(tenant_id,uhid,name,mobile,gender,dob,address,created_by) values(%s,%s,%s,%s,%s,%s,%s,%s) returning *",

                  (ctx.tid, uhid, b.name.strip(), b.mobile, b.gender, b.dob, b.address, ctx.uid))

        p = c.fetchone()

        audit(c, ctx, "create", "patient", p["id"], new={"uhid": uhid, "name": p["name"]})

    return p

@app.get("/api/patients")

def find_patients(q: str = "", ctx: Ctx = Depends(need("patients:r", "patients"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select * from patients where name ilike %s or mobile like %s or uhid ilike %s order by created_at desc limit 50",

                  (f"%{q}%", f"%{q}%", f"%{q}%"))

        return c.fetchall()

@app.get("/api/patients/{pid}")

def get_patient(pid: str, ctx: Ctx = Depends(need("patients:r", "patients"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select * from patients where id=%s", (pid,))

        p = c.fetchone()

        if not p:

            raise HTTPException(404, "Patient not found")

        c.execute("select * from prescriptions where patient_id=%s order by created_at desc", (pid,))

        p["prescriptions"] = c.fetchall()

        c.execute("select id,bill_no,total,paid,status,created_at from bills where patient_id=%s order by created_at desc", (pid,))

        p["bills"] = c.fetchall()

    return p

# ---------------- doctors, schedules, slots ----------------

class DoctorIn(BaseModel):

    name: str = Field(min_length=2)

    department: str = ""

    qualification: str = ""

    bio: str = ""

    photo_url: str = ""

    fee_new: float = Field(0, ge=0)

    fee_followup: float = Field(0, ge=0)

    active: bool = True

@app.get("/api/doctors")

def doctors(ctx: Ctx = Depends(need("doctors:r"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select * from doctors order by name")

        rows = c.fetchall()

        c.execute("select * from doctor_schedules order by weekday,start_time")

        sc = c.fetchall()

    for r in rows:

        r["schedules"] = [s for s in sc if s["doctor_id"] == r["id"]]

    return rows

@app.post("/api/doctors")

def add_doctor(b: DoctorIn, ctx: Ctx = Depends(need("doctors:w"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("insert into doctors(tenant_id,name,department,qualification,bio,photo_url,fee_new,fee_followup,active) values(%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",

                  (ctx.tid, b.name, b.department, b.qualification, b.bio, b.photo_url, b.fee_new, b.fee_followup, b.active))

        d = c.fetchone()

        audit(c, ctx, "create", "doctor", d["id"], new={"name": d["name"]})

    return d

class ScheduleIn(BaseModel):

    weekday: int = Field(ge=0, le=6)

    start_time: dtime

    end_time: dtime

    slot_minutes: int = Field(15, ge=5, le=120)

@app.post("/api/doctors/{did}/schedules")

def add_schedule(did: str, b: ScheduleIn, ctx: Ctx = Depends(need("doctors:w"))):

    if b.end_time <= b.start_time:

        raise HTTPException(422, "End time must be after start time")

    with tenant_tx(ctx.tid) as c:

        c.execute("select 1 from doctors where id=%s", (did,))

        if not c.fetchone():

            raise HTTPException(404, "Doctor not found")

        c.execute("insert into doctor_schedules(tenant_id,doctor_id,weekday,start_time,end_time,slot_minutes) values(%s,%s,%s,%s,%s,%s) returning *",

                  (ctx.tid, did, b.weekday, b.start_time, b.end_time, b.slot_minutes))

        s = c.fetchone()

        audit(c, ctx, "create", "schedule", s["id"], new=b.model_dump())

    return s

def free_slots(c, t, did, d):

    c.execute("select start_time,end_time,slot_minutes from doctor_schedules where doctor_id=%s and weekday=%s", (did, d.weekday()))

    sch = c.fetchall()

    c.execute("select appt_time from appointments where doctor_id=%s and appt_date=%s and appt_time is not null and status not in ('cancelled','no_show')", (did, d))

    taken = {r["appt_time"] for r in c.fetchall()}

    now = datetime.now(tz(t))

    out = []

    for s in sch:

        cur = datetime.combine(d, s["start_time"])

        end = datetime.combine(d, s["end_time"])

        while cur + timedelta(minutes=s["slot_minutes"]) <= end:

            ok = cur.time() not in taken and not (d == now.date() and cur.time() <= now.time())

            if ok:

                out.append(cur.strftime("%H:%M"))

            cur += timedelta(minutes=s["slot_minutes"])

    return sorted(set(out))

@app.get("/api/doctors/{did}/slots")

def slots(did: str, day: date, ctx: Ctx = Depends(need("appointments:r", "appointments"))):

    with tenant_tx(ctx.tid) as c:

        return {"slots": free_slots(c, ctx.t, did, day)}

# ---------------- appointments ----------------

class ApptIn(BaseModel):

    patient_id: str

    doctor_id: str

    appt_date: date

    appt_time: dtime

    notes: str = ""

def make_token(c, tid, did, d):

    return next_no(c, tid, f"token:{did}:{d}")

@app.post("/api/appointments")

def book(b: ApptIn, ctx: Ctx = Depends(need("appointments:w", "appointments"))):

    try:

        with tenant_tx(ctx.tid) as c:

            c.execute("select * from patients where id=%s", (b.patient_id,))

            p = c.fetchone()

            c.execute("select * from doctors where id=%s and active", (b.doctor_id,))

            d = c.fetchone()

            if not p or not d:

                raise HTTPException(404, "Patient or doctor not found")

            if b.appt_date < today(ctx.t):

                raise HTTPException(422, "Date is in the past")

            tok = make_token(c, ctx.tid, b.doctor_id, b.appt_date)

            walkin = b.appt_time is None

            c.execute("insert into appointments(tenant_id,patient_id,doctor_id,appt_date,appt_time,token_no,status,source,notes,created_by) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",

                      (ctx.tid, b.patient_id, b.doctor_id, b.appt_date, b.appt_time, tok, "waiting" if walkin else "booked",

                       "walkin" if walkin else "reception", b.notes, ctx.uid))

            a = c.fetchone()

            audit(c, ctx, "create", "appointment", a["id"], new={"doctor": d["name"], "date": b.appt_date, "time": b.appt_time, "token": tok})

            if "reminders" in ctx.t["modules"]:

                when = f"{b.appt_date} {b.appt_time.strftime('%H:%M')}" if b.appt_time else str(b.appt_date)

                queue(c, ctx.tid, p["mobile"], f"{ctx.t['name']}: appointment with {d['name']} on {when}. Token {tok}.")

                if b.appt_time:

                    at = datetime.combine(b.appt_date, b.appt_time, tzinfo=tz(ctx.t)) - timedelta(hours=24)

                    if at > datetime.now(tz(ctx.t)):

                        queue(c, ctx.tid, p["mobile"], f"Reminder from {ctx.t['name']}: appointment with {d['name']} tomorrow at {b.appt_time.strftime('%H:%M')}.", at)

        return a

    except psycopg2.errors.UniqueViolation:

        raise HTTPException(409, "This slot is already booked")

@app.get("/api/appointments")

def list_appts(day: Optional[date] = None, doctor_id: Optional[str] = None, ctx: Ctx = Depends(need("appointments:r", "appointments"))):

    day = day or today(ctx.t)

    if ctx.role == "doctor" and ctx.doctor_id:

        doctor_id = ctx.doctor_id

    with tenant_tx(ctx.tid) as c:

        c.execute("""select a.*, coalesce(p.name,a.guest_name) as patient_name, coalesce(p.mobile,a.guest_mobile) as mobile, p.uhid, d.name as doctor_name

                     from appointments a left join patients p on p.id=a.patient_id join doctors d on d.id=a.doctor_id

                     where a.appt_date=%s and (%s::uuid is null or a.doctor_id=%s::uuid) order by a.token_no""", (day, doctor_id, doctor_id))

        return c.fetchall()

class StatusIn(BaseModel):

    status: str = Field(pattern=r"^(booked|waiting|in_consultation|done|cancelled|no_show)$")

    patient_id: Optional[str] = None

@app.patch("/api/appointments/{aid}")

def set_status(aid: str, b: StatusIn, ctx: Ctx = Depends(need("appointments:w", "appointments"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select * from appointments where id=%s", (aid,))

        a = c.fetchone()

        if not a:

            raise HTTPException(404, "Appointment not found")

        if ctx.role == "doctor" and str(a["doctor_id"]) != str(ctx.doctor_id):

            raise HTTPException(403, "Not your appointment")

        c.execute("update appointments set status=%s, patient_id=coalesce(%s::uuid,patient_id) where id=%s returning *", (b.status, b.patient_id, aid))

        n = c.fetchone()

        audit(c, ctx, "status", "appointment", aid, old={"status": a["status"]}, new={"status": b.status})

    return n

# ---------------- tariff ----------------

class TariffIn(BaseModel):

    code: str = Field(min_length=1, max_length=20)

    name: str = Field(min_length=2)

    category: str = "OPD"

    price: float = Field(ge=0)

    gst_pct: float = Field(0, ge=0, le=100)

    sac_code: str = ""

@app.get("/api/tariff")

def tariff(ctx: Ctx = Depends(need("tariff:r", "opd_billing"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select * from tariff_items where active order by category,name")

        return c.fetchall()

@app.post("/api/tariff")

def add_tariff(b: TariffIn, ctx: Ctx = Depends(need("tariff:w", "opd_billing"))):

    try:

        with tenant_tx(ctx.tid) as c:

            c.execute("insert into tariff_items(tenant_id,code,name,category,price,gst_pct,sac_code) values(%s,%s,%s,%s,%s,%s,%s) returning *", (ctx.tid, b.code.upper(), b.name, b.category, b.price, b.gst_pct, b.sac_code))

            r = c.fetchone()

            audit(c, ctx, "create", "tariff", r["id"], new=b.model_dump())

        return r

    except psycopg2.errors.UniqueViolation:

        raise HTTPException(409, "Tariff code already exists")

# ---------------- billing ----------------

class Item(BaseModel):

    tariff_id: Optional[str] = None

    description: str = ""

    qty: int = Field(1, ge=1, le=999)

    price: Optional[float] = Field(None, ge=0)

class Pay(BaseModel):

    mode: str = Field(pattern=r"^(cash|upi|card)$")

    amount: float = Field(gt=0)

    ref: str = ""

class BillIn(BaseModel):

    patient_id: str

    appointment_id: Optional[str] = None

    items: List[Item] = Field(min_length=1)

    discount: float = Field(0, ge=0)

    payments: List[Pay] = []

@app.post("/api/bills")

def make_bill(b: BillIn, ctx: Ctx = Depends(need("billing:w", "opd_billing"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select 1 from patients where id=%s", (b.patient_id,))

        if not c.fetchone():

            raise HTTPException(404, "Patient not found")

        rows, sub = [], Decimal(0)

        for it in b.items:

            if it.tariff_id:

                c.execute("select name,price from tariff_items where id=%s and active", (it.tariff_id,))

                tf = c.fetchone()

                if not tf:

                    raise HTTPException(404, "Tariff item not found")

                desc, price = tf["name"], tf["price"]     # the server price is the only price used

            else:

                if ctx.role != "admin" or it.price is None or not it.description:

                    raise HTTPException(422, "Free-text items need description and price and can only be added by an admin")

                desc, price = it.description, D(it.price)

            amt = price * it.qty

            rows.append((desc, it.qty, price, amt)); sub += amt

        disc = D(b.discount)

        limit = D(ctx.t["settings"].get("discount_limit_pct", 10))

        if disc > sub:

            raise HTTPException(422, "Discount cannot exceed the bill amount")

        if disc > sub * limit / 100 and ctx.role != "admin":

            raise HTTPException(403, f"Discount above {limit}% needs admin approval")

        total = sub - disc

        paid = sum((D(p.amount) for p in b.payments), Decimal(0))

        if paid > total:

            raise HTTPException(422, "Payments exceed the bill total")

        n = next_no(c, ctx.tid, f"bill:{fy(today(ctx.t))}")

        no = f"{ctx.t['settings'].get('bill_prefix', 'BL')}/{fy(today(ctx.t))}/{n:05d}"

        c.execute("insert into bills(tenant_id,bill_no,patient_id,appointment_id,subtotal,discount,total,paid,status,created_by) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",

                  (ctx.tid, no, b.patient_id, b.appointment_id, sub, disc, total, paid, "paid" if paid == total else "due", ctx.uid))

        bill = c.fetchone()

        for r in rows:

            c.execute("insert into bill_items(tenant_id,bill_id,description,qty,price,amount) values(%s,%s,%s,%s,%s,%s)", (ctx.tid, bill["id"], *r))

        for p in b.payments:

            c.execute("insert into payments(tenant_id,bill_id,mode,amount,ref) values(%s,%s,%s,%s,%s)", (ctx.tid, bill["id"], p.mode, p.amount, p.ref))

        audit(c, ctx, "create", "bill", bill["id"], new={"bill_no": no, "total": total, "discount": disc})

    return bill

@app.get("/api/bills")

def list_bills(day: Optional[date] = None, ctx: Ctx = Depends(need("billing:r", "opd_billing"))):

    day = day or today(ctx.t)

    with tenant_tx(ctx.tid) as c:

        c.execute("select b.*, p.name as patient_name, p.uhid from bills b join patients p on p.id=b.patient_id where (b.created_at at time zone %s)::date=%s order by b.created_at desc",

                  (ctx.t["settings"].get("timezone", "Asia/Kolkata"), day))

        return c.fetchall()

@app.get("/api/bills/{bid}")

def get_bill(bid: str, ctx: Ctx = Depends(need("billing:r", "opd_billing"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select b.*, p.name as patient_name, p.uhid, p.mobile from bills b join patients p on p.id=b.patient_id where b.id=%s", (bid,))

        b = c.fetchone()

        if not b:

            raise HTTPException(404, "Bill not found")

        c.execute("select description,qty,price,amount from bill_items where bill_id=%s", (bid,))

        b["items"] = c.fetchall()

        c.execute("select mode,amount,ref from payments where bill_id=%s", (bid,))

        b["payments"] = c.fetchall()

    b["branding"] = branding(ctx.t)

    return b

@app.get("/api/reports/collection")

def collection(day: Optional[date] = None, ctx: Ctx = Depends(need("reports:r", "opd_billing"))):

    day = day or today(ctx.t)

    with tenant_tx(ctx.tid) as c:

        c.execute("select mode, sum(amount) as amount, count(*) as count from payments where (created_at at time zone %s)::date=%s group by mode order by mode",

                  (ctx.t["settings"].get("timezone", "Asia/Kolkata"), day))

        rows = c.fetchall()

    return {"date": day, "by_mode": rows, "total": sum((r["amount"] for r in rows), Decimal(0))}

# ---------------- prescriptions ----------------

class Rx(BaseModel):

    patient_id: str

    appointment_id: Optional[str] = None

    complaints: str = ""

    diagnosis: str = ""

    advice: str = ""

    items: List[dict] = []

    follow_up_date: Optional[date] = None

@app.post("/api/prescriptions")

def add_rx(b: Rx, ctx: Ctx = Depends(need("prescriptions:w", "prescriptions"))):

    if not ctx.doctor_id:

        raise HTTPException(403, "Only a doctor account linked to a doctor profile can write prescriptions")

    with tenant_tx(ctx.tid) as c:

        c.execute("select 1 from patients where id=%s", (b.patient_id,))

        if not c.fetchone():

            raise HTTPException(404, "Patient not found")

        c.execute("insert into prescriptions(tenant_id,patient_id,doctor_id,appointment_id,complaints,diagnosis,advice,items,follow_up_date) values(%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",

                  (ctx.tid, b.patient_id, ctx.doctor_id, b.appointment_id, b.complaints, b.diagnosis, b.advice, Json(b.items), b.follow_up_date))

        r = c.fetchone()

        audit(c, ctx, "create", "prescription", r["id"], new={"patient": b.patient_id})

    return r

@app.get("/api/prescriptions/{rid}")

def get_rx(rid: str, ctx: Ctx = Depends(need("prescriptions:r", "prescriptions"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select r.*, p.name as patient_name, p.uhid, p.mobile, p.gender, p.dob, d.name as doctor_name, d.qualification, d.department "

                  "from prescriptions r join patients p on p.id=r.patient_id join doctors d on d.id=r.doctor_id where r.id=%s", (rid,))

        r = c.fetchone()

    if not r:

        raise HTTPException(404, "Prescription not found")

    r["branding"] = branding(ctx.t)

    return r

# ---------------- dashboard, users, settings, audit, messages ----------------

@app.get("/api/dashboard")

def dashboard(ctx: Ctx = Depends(need("dashboard:r", "dashboard"))):

    d = today(ctx.t)

    zone = ctx.t["settings"].get("timezone", "Asia/Kolkata")

    with tenant_tx(ctx.tid) as c:

        c.execute("select status,count(*) n from appointments where appt_date=%s group by status", (d,))

        st = {r["status"]: r["n"] for r in c.fetchall()}

        c.execute("select count(*) n from patients where (created_at at time zone %s)::date=%s", (zone, d))

        newp = c.fetchone()["n"]

        c.execute("select coalesce(sum(amount),0) s from payments where (created_at at time zone %s)::date=%s", (zone, d))

        col = c.fetchone()["s"]

    return {"date": d, "appointments_by_status": st, "appointments_total": sum(st.values()), "new_patients": newp, "collection_today": col}

class UserIn(BaseModel):

    username: str = Field(pattern=r"^[a-z0-9._-]{3,30}$")

    name: str

    role: str = Field(pattern=r"^(admin|receptionist|doctor|billing|nurse)$")

    password: str = Field(min_length=8)

    doctor_id: Optional[str] = None

@app.get("/api/users")

def users(ctx: Ctx = Depends(need("users:r", "users"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select id,username,name,role,doctor_id,active from users order by username")

        return c.fetchall()

@app.post("/api/users")

def add_user(b: UserIn, ctx: Ctx = Depends(need("users:w", "users"))):

    try:

        with tenant_tx(ctx.tid) as c:

            c.execute("insert into users(tenant_id,username,name,role,password_hash,doctor_id) values(%s,%s,%s,%s,%s,%s) returning id,username,name,role,doctor_id,active",

                      (ctx.tid, b.username, b.name, b.role, hash_pw(b.password), b.doctor_id))

            u = c.fetchone()

            audit(c, ctx, "create", "user", u["id"], new={"username": b.username, "role": b.role})

        return u

    except psycopg2.errors.UniqueViolation:

        raise HTTPException(409, "Username already exists")

class UserPatch(BaseModel):

    active: Optional[bool] = None

    password: Optional[str] = Field(None, min_length=8)

@app.patch("/api/users/{uid}")

def patch_user(uid: str, b: UserPatch, ctx: Ctx = Depends(need("users:w", "users"))):

    if uid == str(ctx.uid) and b.active is False:

        raise HTTPException(422, "You cannot disable your own account")

    with tenant_tx(ctx.tid) as c:

        if b.active is not None:

            c.execute("update users set active=%s where id=%s", (b.active, uid))

        if b.password:

            c.execute("update users set password_hash=%s where id=%s", (hash_pw(b.password), uid))

        audit(c, ctx, "update", "user", uid, new={"active": b.active, "password_reset": bool(b.password)})

    return {"ok": True}

class SettingsIn(BaseModel):

    tagline: Optional[str] = None

    primary: Optional[str] = None

    secondary: Optional[str] = None

    logo_url: Optional[str] = None

    phone: Optional[str] = None

    email: Optional[str] = None

    address: Optional[str] = None

    footer: Optional[str] = None

    uhid_prefix: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{1,6}$")

    bill_prefix: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{1,6}$")

    discount_limit_pct: Optional[float] = Field(None, ge=0, le=100)

    gstin: Optional[str] = Field(None, pattern=r"^[0-9A-Z]{15}$")

    ipd_checkout_hour: Optional[int] = Field(None, ge=0, le=23)

    adm_prefix: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{1,6}$")

    ipd_bill_prefix: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{1,6}$")

    receipt_prefix: Optional[str] = Field(None, pattern=r"^[A-Z0-9]{1,6}$")

@app.patch("/api/settings")

def save_settings(b: SettingsIn, ctx: Ctx = Depends(need("settings:w"))):

    d = b.model_dump(exclude_none=True)

    for k in ("primary", "secondary"):

        if k in d and not HEX.match(d[k]):

            raise HTTPException(422, f"{k} must look like #0b6e99")

    bk = {"tagline", "primary", "secondary", "logo_url", "phone", "email", "address", "footer"}

    with platform_tx() as c:   # tenants table is a platform table; only this tenant's own row is touched

        c.execute("update tenants set branding=branding||%s::jsonb, settings=settings||%s::jsonb where id=%s",

                  (Json({k: v for k, v in d.items() if k in bk}), Json({k: v for k, v in d.items() if k not in bk}), ctx.tid))

    with tenant_tx(ctx.tid) as c:

        audit(c, ctx, "update", "settings", ctx.tid, new=d)

    return {"ok": True}

@app.get("/api/audit")

def audit_list(limit: int = 100, ctx: Ctx = Depends(need("audit:r", "audit"))):

    with tenant_tx(ctx.tid) as c:

        c.execute("select * from audit_log order by id desc limit %s", (min(limit, 500),))

        return c.fetchall()

class ConsoleProvider:     # adapter: replace with a real SMS / WhatsApp provider per tenant

    @staticmethod

    def send(to, body):

        print(f"[SMS console] to={to} :: {body}")

PROVIDERS = {"console": ConsoleProvider}

def dispatch(tid, settings):

    prov = PROVIDERS.get(settings.get("sms_provider", "console"), ConsoleProvider)

    sent = 0

    with tenant_tx(tid) as c:

        c.execute("select * from message_queue where status='pending' and send_after<=now() order by send_after limit 100")

        for m in c.fetchall():

            try:

                prov.send(m["to_addr"], m["body"])

                c.execute("update message_queue set status='sent', attempts=attempts+1 where id=%s", (m["id"],)); sent += 1

            except Exception as e:

                c.execute("update message_queue set status=case when attempts>=2 then 'failed' else 'pending' end, attempts=attempts+1, last_error=%s where id=%s", (str(e), m["id"]))

    return sent

@app.post("/api/messages/dispatch")

def dispatch_now(ctx: Ctx = Depends(need("settings:w", "reminders"))):

    return {"sent": dispatch(ctx.tid, ctx.t["settings"])}

# ---------------- public website + online booking ----------------

_hits = defaultdict(list)

def pub(request, code):

    t = resolve_tenant(request, code)

    if "website" not in t["modules"]:

        raise HTTPException(403, "Website is not included in this plan")

    return t

@app.get("/api/public/{code}/doctors")

def pub_doctors(code: str, request: Request):

    t = pub(request, code)

    with tenant_tx(t["id"]) as c:

        c.execute("select id,name,department,qualification,bio,photo_url from doctors where active order by name")

        return c.fetchall()

@app.get("/api/public/{code}/slots")

def pub_slots(code: str, doctor_id: str, day: date, request: Request):

    t = pub(request, code)

    with tenant_tx(t["id"]) as c:

        return {"slots": free_slots(c, t, doctor_id, day)}

class PubAppt(BaseModel):

    name: str = Field(min_length=2, max_length=80)

    mobile: str = Field(pattern=r"^\d{10}$")

    doctor_id: str

    appt_date: date

    appt_time: Optional[dtime] = None

@app.post("/api/public/{code}/appointments")

def pub_book(code: str, b: PubAppt, request: Request):

    t = pub(request, code)

    if "appointments" not in t["modules"]:

        raise HTTPException(403, "Online booking is not enabled")

    ip = request.client.host if request.client else "x"

    now = time.time()

    _hits[ip] = [x for x in _hits[ip] if now - x < 3600]

    if len(_hits[ip]) >= 10:

        raise HTTPException(429, "Too many requests, please try later")

    _hits[ip].append(now)

    try:

        with tenant_tx(t["id"]) as c:

            c.execute("select name from doctors where id=%s and active", (b.doctor_id,))

            d = c.fetchone()

            if not d:

                raise HTTPException(404, "Doctor not found")

            if b.appt_date < today(t):

                raise HTTPException(422, "Date is in the past")
            if b.appt_time.strftime("%H:%M") not in free_slots(
                c, t, b.doctor_id, b.appt_date
            ):
                raise HTTPException(
                    status_code=409,
                    detail="This time slot is unavailable. Please select another slot.",
                )

            c.execute("insert into appointments(tenant_id,guest_name,guest_mobile,doctor_id,appt_date,appt_time,status,source) values(%s,%s,%s,%s,%s,%s,'requested','online') returning id",

                      (t["id"], b.name, b.mobile, b.doctor_id, b.appt_date, b.appt_time))

            a = c.fetchone()

            if "reminders" in t["modules"]:

                queue(c, t["id"], b.mobile, f"{t['name']}: we received your appointment request with {d['name']} for {b.appt_date}. Our team will confirm shortly.")

        return {"id": a["id"], "message": "Request received. The hospital will confirm by SMS."}

    except psycopg2.errors.UniqueViolation:

        raise HTTPException(409, "This slot was just taken, please choose another")

def site_html(t):

    """Premium, tenant-branded public site using the existing public appointment APIs."""

    b = branding(t)

    e = html.escape

    code = json_str(t["code"])

    name = e(t["name"])

    tagline = e(b.get("tagline") or "Compassionate care. Advanced medicine. Better outcomes.")

    address = e(b.get("address") or "Lucknow, Uttar Pradesh")

    phone = e(b.get("phone") or "")

    email = e(b.get("email") or "")

    primary = b["primary"]

    secondary = b["secondary"]

    logo = (f'<img class="logo-img" src="{e(b["logo_url"])}" alt="{name} logo">') if b.get("logo_url") else '<span class="brand-icon">✚</span>'

    pw = '<span class="powered">Powered by Hospital Platform</span>' if t["settings"].get("powered_by", True) else ""

    return f"""<!doctype html>

<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">

<title>{name} | Specialist Hospital Care</title><meta name="description" content="{tagline}">

<style>

:root{{--p:{primary};--s:{secondary};--navy:#073763;--teal:#00a5b8;--ink:#183751;--muted:#60778b;--line:#dce7ef}}

*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:#f7fafc;color:var(--ink);font:15px/1.5 Inter,Arial,Helvetica,sans-serif}}a{{color:inherit;text-decoration:none}}button,input,select{{font:inherit}}

.top{{background:#052e56;color:#fff;padding:8px max(22px,calc((100% - 1240px)/2));display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:11px}}

.nav{{height:76px;position:sticky;top:0;z-index:20;background:#fff;display:flex;align-items:center;justify-content:space-between;gap:20px;padding:0 max(22px,calc((100% - 1240px)/2));box-shadow:0 2px 14px #092f4b10}}

.brand{{display:flex;align-items:center;gap:10px;color:var(--navy);font-weight:900;font-size:19px;white-space:nowrap}}.brand-icon{{display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:linear-gradient(140deg,var(--p),var(--teal));color:#fff;font-size:25px}}.logo-img{{max-width:44px;max-height:44px;object-fit:contain}}.brand small{{display:block;font-size:9px;letter-spacing:3px;margin-top:1px}}

.links{{display:flex;gap:19px;font-size:12px;font-weight:750}}.links a:hover{{color:var(--teal)}}.btn{{display:inline-flex;justify-content:center;align-items:center;gap:8px;padding:12px 16px;border:0;border-radius:8px;font-weight:800;cursor:pointer}}.primary{{background:linear-gradient(110deg,var(--teal),#087bb6);color:#fff}}.outline{{background:#fff;border:1px solid #c5d5e1;color:var(--navy)}}

.hero{{min-height:475px;position:relative;display:flex;align-items:center;overflow:hidden;background:#dcebf5 url('https://images.unsplash.com/photo-1587351021759-3e566b6af7cc?auto=format&fit=crop&w=2000&q=88') center 43%/cover no-repeat}}

.hero:before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,#052e56f2 0%,#073d6bdc 34%,#073d6b70 60%,#052b4a15 100%)}}

.wrap{{width:min(1240px,calc(100% - 44px));margin:auto;position:relative;z-index:1}}.hero-content{{padding:62px 0;color:#fff}}.eyebrow{{color:#51d7e4;font-size:11px;font-weight:850;letter-spacing:2px;text-transform:uppercase}}h1{{font-size:clamp(38px,4.5vw,61px);line-height:1.04;letter-spacing:-1.5px;max-width:620px;margin:16px 0}}.hero p{{max-width:465px;color:#e1edf6;font-size:16px;line-height:1.7}}.hero-actions{{display:flex;gap:12px;flex-wrap:wrap;margin-top:23px}}.hero .outline{{border:0}}

.quick{{position:absolute;right:max(22px,calc((100% - 1240px)/2));top:50%;transform:translateY(-50%);z-index:2;width:176px;padding:7px;border:1px solid #fff;border-radius:13px;background:#ffffffed;box-shadow:0 14px 36px #052e5630}}.quick a{{display:flex;align-items:center;gap:9px;padding:12px 7px;border-bottom:1px solid #e9eff4;font-size:11px;font-weight:800}}.quick a:last-child{{border:0}}.quick i{{font-style:normal;display:grid;place-items:center;width:30px;height:30px;border-radius:8px;background:#e4f5f8;color:var(--p);font-size:16px}}

.metrics{{width:min(1240px,calc(100% - 44px));margin:-26px auto 0;position:relative;z-index:3;background:#fff;border:1px solid #e1eaf0;border-radius:13px;box-shadow:0 12px 34px #0a34500d;padding:15px 8px;display:grid;grid-template-columns:repeat(4,1fr)}}.metric{{display:flex;justify-content:center;align-items:center;gap:10px;padding:8px 12px;border-right:1px solid var(--line)}}.metric:last-child{{border:0}}.metric i{{font-style:normal;color:var(--p);font-size:25px}}.metric strong{{display:block;color:var(--navy);font-size:15px}}.metric small{{color:var(--muted);font-size:11px}}

.section{{width:min(1240px,calc(100% - 44px));margin:55px auto}}.head{{display:flex;align-items:end;justify-content:space-between;gap:20px;margin-bottom:20px}}.head .eyebrow{{color:var(--p)}}h2{{font-size:29px;letter-spacing:-.5px;color:var(--navy);margin:7px 0}}.head p{{color:var(--muted);font-size:13px;margin:0}}.textlink{{color:var(--p);font-weight:850;font-size:12px}}

.specs{{display:grid;grid-template-columns:repeat(4,1fr);gap:13px}}.spec{{height:140px;position:relative;overflow:hidden;border-radius:10px;background:#07345d;color:#fff}}.spec img{{width:100%;height:100%;object-fit:cover;transition:transform .3s}}.spec:hover img{{transform:scale(1.05)}}.spec:after{{content:"";position:absolute;inset:0;background:linear-gradient(0deg,#041f3ce8,transparent 78%)}}.spec span{{position:absolute;z-index:1;bottom:12px;left:12px;font-size:13px;font-weight:850}}

.layout{{display:grid;grid-template-columns:1fr 1.6fr;gap:18px}}.booking{{background:linear-gradient(145deg,#063766,#075487);padding:24px;border-radius:13px;color:#fff}}.booking h3{{margin:0 0 5px;font-size:22px}}.booking p{{margin:0 0 14px;color:#d1e5f1;font-size:12px}}.field{{margin:9px 0}}.field input,.field select{{width:100%;padding:12px;border-radius:7px;border:1px solid #d9e5ed;background:#fff;color:#24445d}}.booking .btn{{width:100%;margin-top:7px}}.note{{font-size:10px;color:#c5dfed;margin-top:9px}}

.docs{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.doc{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:15px;display:flex;align-items:center;gap:12px;min-width:0}}.doc-photo{{width:64px;height:70px;flex:none;border-radius:8px;display:grid;place-items:center;background:#e5f3f7;color:var(--p);font-size:26px;overflow:hidden}}.doc-photo img{{width:100%;height:100%;object-fit:cover}}.doc h3{{margin:0 0 3px;font-size:14px;color:var(--navy)}}.doc p{{margin:0;color:var(--muted);font-size:11px}}.doc small{{display:block;color:var(--p);font-size:10px;margin-top:5px}}.empty{{padding:22px;background:#fff;border:1px solid var(--line);border-radius:10px;color:var(--muted);font-size:13px}}

.features{{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}}.feature{{background:#fff;border:1px solid var(--line);border-radius:11px;overflow:hidden}}.feature img{{width:100%;height:160px;object-fit:cover;display:block}}.feature div{{padding:17px}}.feature h3{{margin:0 0 7px;color:var(--navy);font-size:17px}}.feature p{{margin:0;color:var(--muted);font-size:12px;line-height:1.6}}

.footer{{background:#052747;color:#cbdce8;padding:36px max(22px,calc((100% - 1240px)/2));display:grid;grid-template-columns:2fr 1fr 1fr;gap:28px}}.footer h3{{color:#fff;font-size:14px}}.footer p,.footer a{{font-size:12px;line-height:2}}.footer a{{display:block}}.powered{{font-size:10px;opacity:.6}}.msg{{font-size:12px;margin-top:10px;min-height:18px;color:#d7edf5}}

@media(max-width:1000px){{.links{{display:none}}.quick{{display:none}}.layout{{grid-template-columns:1fr}}.hero:before{{background:linear-gradient(90deg,#052e56f5 0%,#073d6bdc 52%,#073d6b40 100%)}}}}

@media(max-width:650px){{.top{{font-size:10px}}.nav{{height:67px;padding:0 14px}}.brand{{font-size:15px}}.brand-icon{{width:32px;height:32px}}.nav .outline{{display:none}}.nav .btn{{font-size:10px;padding:10px}}.hero{{min-height:490px;background-position:58% center}}.wrap{{width:calc(100% - 32px)}}.hero-content{{padding:42px 0}}h1{{font-size:42px;letter-spacing:-1px}}.hero p{{font-size:14px}}.hero-actions .btn{{font-size:12px;padding:12px}}.metrics{{width:calc(100% - 22px);grid-template-columns:repeat(2,1fr);padding:9px;margin-top:-17px}}.metric{{justify-content:flex-start;border:0;padding:10px 7px}}.metric:nth-child(odd){{border-right:1px solid var(--line)}}.metric strong{{font-size:13px}}.metric small{{font-size:10px}}.metric:last-child{{grid-column:span 2;border-top:1px solid var(--line)}}.section{{width:calc(100% - 28px);margin:42px auto}}.head{{align-items:start;flex-direction:column}}h2{{font-size:25px}}.specs{{grid-template-columns:repeat(2,1fr);gap:9px}}.spec{{height:130px}}.docs,.features{{grid-template-columns:1fr}}.feature img{{height:170px}}.footer{{grid-template-columns:1fr;padding:30px 20px}}}}

</style></head><body>

<div class="top"><span>⌖ {address} &nbsp; · &nbsp; ☎ {phone} &nbsp; · &nbsp; ✉ {email}</span><span>Specialist Care &nbsp; | &nbsp; Patient Services &nbsp; | &nbsp; Contact</span></div>

<header class="nav"><a class="brand" href="#home">{logo}<span>{name}<small>HEALTHCARE</small></span></a><nav class="links"><a href="#home">Home</a><a href="#about">About Us</a><a href="#doctors">Doctors</a><a href="#specialties">Specialties</a><a href="#care">Patient Care</a><a href="#contact">Contact</a></nav><a class="btn primary" href="#book">▦ Book Appointment</a></header>

<main>

<section class="hero" id="home"><div class="wrap"><div class="hero-content"><div class="eyebrow">Compassion · Expertise · Advanced Care</div><h1>World-class healthcare<br>for a healthier tomorrow.</h1><p>{tagline}</p><div class="hero-actions"><a class="btn primary" href="#book">Book an Appointment →</a><a class="btn outline" href="#doctors">Find a Doctor →</a></div></div></div><aside class="quick"><a href="tel:112"><i>✚</i><span>Emergency<small style="display:block;font-weight:500;color:#64798b">24-hour assistance</small></span></a><a href="tel:{e(b.get('phone') or '')}"><i>☎</i><span>Call Hospital</span></a><a href="#contact"><i>⌖</i><span>Get Directions</span></a><a href="#book"><i>▦</i><span>Book Appointment</span></a></aside></section>

<div class="metrics"><div class="metric"><i>♙</i><span><strong>Specialist Doctors</strong><small>Expert-led consultations</small></span></div><div class="metric"><i>♡</i><span><strong>Multiple Specialties</strong><small>Coordinated care</small></span></div><div class="metric"><i>✧</i><span><strong>Modern Facilities</strong><small>Patient-first approach</small></span></div><div class="metric"><i>◷</i><span><strong>Patient Support</strong><small>Help when you need it</small></span></div></div>

<section class="section" id="specialties"><div class="head"><div><div class="eyebrow">Our departments</div><h2>Explore our specialties</h2><p>Find a care team that fits your health needs.</p></div><a class="textlink" href="#doctors">Find a specialist →</a></div><div class="specs">

<a class="spec" href="#book"><img loading="lazy" src="https://images.unsplash.com/photo-1628348068343-c6a848d2b6dd?auto=format&fit=crop&w=600&q=80" alt="Cardiology"><span>Cardiology</span></a>

<a class="spec" href="#book"><img loading="lazy" src="https://images.unsplash.com/photo-1559757175-0eb30cd8c063?auto=format&fit=crop&w=600&q=80" alt="Neurology"><span>Neurology</span></a>

<a class="spec" href="#book"><img loading="lazy" src="https://images.unsplash.com/photo-1582719471384-894fbb16e074?auto=format&fit=crop&w=600&q=80" alt="Orthopaedics"><span>Orthopaedics</span></a>

<a class="spec" href="#book"><img loading="lazy" src="https://images.unsplash.com/photo-1579684385127-1ef15d508118?auto=format&fit=crop&w=600&q=80" alt="Oncology"><span>Oncology</span></a>

<a class="spec" href="#book"><img loading="lazy" src="https://images.unsplash.com/photo-1519494026892-80bbd2d6fd0d?auto=format&fit=crop&w=600&q=80" alt="Women's health"><span>Women's Health</span></a>

<a class="spec" href="#book"><img loading="lazy" src="https://images.unsplash.com/photo-1551190822-a9333d879b1f?auto=format&fit=crop&w=600&q=80" alt="Surgery"><span>General Surgery</span></a>

<a class="spec" href="#book"><img loading="lazy" src="https://images.unsplash.com/photo-1584982751601-97dcc096659c?auto=format&fit=crop&w=600&q=80" alt="Pulmonology"><span>Pulmonology</span></a>

<a class="spec" href="#book"><img loading="lazy" src="https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?auto=format&fit=crop&w=600&q=80" alt="Nephrology"><span>Nephrology</span></a>

</div></section>

<section class="section" id="book"><div class="layout"><form class="booking" id="f"><h3>Book an appointment</h3><p>Send a request. The hospital team will confirm availability.</p><div class="field"><input name="name" placeholder="Patient full name" minlength="2" maxlength="80" required></div><div class="field"><input name="mobile" placeholder="10-digit mobile number" inputmode="numeric" pattern="[0-9]{{10}}" required></div><div class="field"><select name="doctor_id" id="doc" required><option value="">Choose a doctor</option></select></div><div class="field"><input type="date" name="appt_date" id="dt" required></div><div class="field"><select name="appt_time" id="tm"><option value="">Any time — hospital will confirm</option></select></div><button class="btn primary" type="submit">Request Appointment →</button><div class="note">Your request is sent to the hospital appointment queue. Do not use this form for emergencies.</div><div class="msg" id="msg" role="status" aria-live="polite"></div></form>

<div><div class="head"><div><div class="eyebrow">Meet your care team</div><h2 id="doctor-heading">Find a doctor</h2><p>Verified profiles configured by the hospital are shown here.</p></div></div><div class="docs" id="docs"><div class="empty">Loading available doctors…</div></div></div></div></section>

<section class="section" id="about"><div class="head"><div><div class="eyebrow">Why choose us</div><h2>Care built around people</h2><p>Clear information, thoughtful service and coordinated care for patients and families.</p></div></div><div class="features"><article class="feature"><img loading="lazy" src="https://images.unsplash.com/photo-1551190822-a9333d879b1f?auto=format&fit=crop&w=900&q=85" alt="Modern operating theatre"><div><h3>Modern facilities</h3><p>Clinical spaces and technology designed to support safe, coordinated treatment.</p></div></article><article class="feature"><img loading="lazy" src="https://images.unsplash.com/photo-1612349317150-e413f6a5b16d?auto=format&fit=crop&w=900&q=85" alt="Doctor consultation"><div><h3>Expert medical teams</h3><p>Explore hospital-configured specialists and request a consultation.</p></div></article><article class="feature"><img loading="lazy" src="https://images.unsplash.com/photo-1519494026892-80bbd2d6fd0d?auto=format&fit=crop&w=900&q=85" alt="Patient room"><div><h3>Patient-centred care</h3><p>Patient services and support information in one convenient place.</p></div></article></div></section>

<section class="section" id="care"><div class="head"><div><div class="eyebrow">Patient support</div><h2>Here when you need us</h2><p>Use the appointment form for non-emergency consultation requests.</p></div></div><div class="specs"><a class="spec" style="height:105px;background:#073766" href="#book"><span>Appointments →</span></a><a class="spec" style="height:105px;background:#0a5a82" href="#doctors"><span>Find a Doctor →</span></a><a class="spec" style="height:105px;background:#087f9c" href="#contact"><span>Patient Services →</span></a><a class="spec" style="height:105px;background:#0a4774" href="tel:{e(b.get('phone') or '')}"><span>Call Hospital →</span></a></div></section>

</main>

<footer class="footer" id="contact"><div><a class="brand" href="#home" style="color:white">{logo}<span style="color:white">{name}<small>HEALTHCARE</small></span></a><p>{tagline}</p><p>{address}<br>{phone}<br>{email}</p><div class="powered">{e(b.get('footer') or '')} {pw}</div></div><div><h3>Explore</h3><a href="#about">About us</a><a href="#specialties">Specialties</a><a href="#doctors">Find a doctor</a><a href="#book">Book appointment</a></div><div><h3>Patient information</h3><a href="#care">Patient services</a><a href="#contact">Insurance / TPA</a><a href="#contact">Contact us</a></div></footer>

<script>

const C={code},q=s=>document.querySelector(s);const today=new Date();q('#dt').min=new Date(today.getTime()-today.getTimezoneOffset()*60000).toISOString().slice(0,10);

function node(tag,cls,text){{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}}

function showMsg(s,ok=false){{q('#msg').textContent=s;q('#msg').style.color=ok?'#b5f6d2':'#ffe0dc'}}

async function loadDoctors(){{try{{const r=await fetch('/api/public/'+encodeURIComponent(C)+'/doctors');if(!r.ok)throw new Error('Unable to load doctors right now.');const data=await r.json();const select=q('#doc');select.replaceChildren(new Option('Choose a doctor',''));const list=q('#docs');list.replaceChildren();for(const d of data){{const opt=new Option((d.name||'Doctor')+' — '+(d.department||'Specialist'),d.id);select.add(opt);const card=node('article','doc');const pic=node('div','doc-photo');if(d.photo_url&&/^https:\\\\/\\\\//i.test(d.photo_url)){{const img=document.createElement('img');img.src=d.photo_url;img.alt='';img.loading='lazy';img.onerror=()=>{{pic.replaceChildren(document.createTextNode('✚'))}};pic.append(img)}}else pic.textContent='✚';card.append(pic);const info=node('div','');info.append(node('h3','',d.name||'Doctor'),node('p','',(d.department||'Specialist')+(d.qualification?' · '+d.qualification:'')));const choose=node('small','','Select for appointment ↓');info.append(choose);card.append(info);card.addEventListener('click',()=>{{select.value=d.id;q('#book').scrollIntoView({{behavior:'smooth'}});loadSlots()}});list.append(card)}}if(!data.length)list.append(node('div','empty','No active doctor profiles have been published by this hospital yet.'));}}catch(err){{q('#docs').replaceChildren(node('div','empty',err.message||'Could not load doctors. Please call the hospital.'))}}}}

async function loadSlots(){{const doctor=q('#doc').value,day=q('#dt').value,sel=q('#tm');sel.replaceChildren(new Option('Any time — hospital will confirm',''));if(!doctor||!day)return;try{{const r=await fetch('/api/public/'+encodeURIComponent(C)+'/slots?doctor_id='+encodeURIComponent(doctor)+'&day='+encodeURIComponent(day));const data=await r.json();if(!r.ok)throw new Error(data.detail||'Could not load slots');for(const s of (data.slots||[]))sel.add(new Option(s,s));}}catch(e){{showMsg(String(e.message||e))}}}}

q('#doc').addEventListener('change',loadSlots);q('#dt').addEventListener('change',loadSlots);

q('#f').addEventListener('submit',async e=>{{e.preventDefault();const f=Object.fromEntries(new FormData(e.target));if(!f.appt_time)delete f.appt_time;showMsg('Sending your request…',true);try{{const r=await fetch('/api/public/'+encodeURIComponent(C)+'/appointments',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(f)}});const j=await r.json();if(!r.ok)throw new Error(typeof j.detail==='string'?j.detail:'Please check your details and try again.');showMsg(j.message||'Request received. The hospital will contact you.',true);e.target.reset();q('#dt').min=new Date().toISOString().slice(0,10);}}catch(err){{showMsg(err.message||'Request failed. Please call the hospital.')}}}});

loadDoctors();

</script></body></html>"""

def json_str(s):

    import json

    return json.dumps(s)

@app.get("/site/{code}", response_class=HTMLResponse)

def site(code: str, request: Request):

    return site_html(pub(request, code))

@app.get("/", response_class=HTMLResponse)

def root(request: Request):

    try:

        t = resolve_tenant(request)

        if "website" in t["modules"]:

            return site_html(t)

    except HTTPException:

        pass

    return RedirectResponse("/app")

# ---------------- platform (super admin) ----------------

class PLogin(BaseModel):

    username: str

    password: str

@app.post("/api/platform/login")

def plogin(b: PLogin):

    with platform_tx() as c:

        c.execute("select * from platform_users where username=%s and active", (b.username.strip().lower(),))

        u = c.fetchone()

    if not u or not check_pw(b.password, u["password_hash"]):

        raise HTTPException(401, "Wrong username or password")

    return {"token": sign({"plat": True, "uid": str(u["id"]), "un": u["username"], "role": u["role"], "tids": [str(x) for x in u["partner_tenant_ids"]]}), "name": u["name"], "role": u["role"]}

@app.get("/api/platform/plans")

def plans(p=Depends(need_plat())):

    with platform_tx() as c:

        c.execute("select * from plans order by array_length(modules,1)")

        rows = c.fetchall()

    for r in rows:

        r["built"] = sorted(BUILT & set(r["modules"]))

    return rows

class TenantIn(BaseModel):

    name: str = Field(min_length=2)

    code: str = Field(pattern=r"^[a-z0-9-]{2,30}$")

    plan: str = Field(pattern=r"^(basic|advance|pro)$")

    domain: Optional[str] = None

    primary: str = "#0b6e99"

    secondary: str = "#e8f4f8"

    logo_url: str = ""

    tagline: str = ""

    phone: str = ""

    address: str = ""

    uhid_prefix: str = Field("PT", pattern=r"^[A-Z0-9]{1,6}$")

    bill_prefix: str = Field("BL", pattern=r"^[A-Z0-9]{1,6}$")

    discount_limit_pct: float = Field(10, ge=0, le=100)

    admin_username: str = Field("admin", pattern=r"^[a-z0-9._-]{3,30}$")

    admin_password: str = Field(min_length=8)

@app.post("/api/platform/tenants")

def new_tenant(b: TenantIn, p=Depends(need_plat("superadmin"))):

    if not (HEX.match(b.primary) and HEX.match(b.secondary)):

        raise HTTPException(422, "Colours must look like #0b6e99")

    try:

        with platform_tx() as c:

            c.execute("insert into tenants(code,name,domain,plan,branding,settings) values(%s,%s,%s,%s,%s,%s) returning *",

                      (b.code, b.name, b.domain, b.plan,

                       Json({"primary": b.primary, "secondary": b.secondary, "logo_url": b.logo_url, "tagline": b.tagline, "phone": b.phone, "address": b.address}),

                       Json({"uhid_prefix": b.uhid_prefix, "bill_prefix": b.bill_prefix, "timezone": "Asia/Kolkata",

                             "discount_limit_pct": b.discount_limit_pct, "sms_provider": "console", "powered_by": True})))

            t = c.fetchone()

        with tenant_tx(t["id"]) as c:

            c.execute("insert into users(tenant_id,username,name,role,password_hash) values(%s,%s,'Hospital Admin','admin',%s)", (t["id"], b.admin_username, hash_pw(b.admin_password)))

    except psycopg2.errors.UniqueViolation:

        raise HTTPException(409, "Hospital code or domain already exists")

    return {"id": t["id"], "code": t["code"], "site": f"/site/{t['code']}", "staff_login": f"/app?t={t['code']}"}

@app.get("/api/platform/tenants")

def list_tenants(p=Depends(need_plat())):

    with platform_tx() as c:

        c.execute("""select t.id,t.code,t.name,t.plan,t.status,t.domain,t.modules_add,t.modules_remove,t.created_at,

                     (select count(*) from patients x where x.tenant_id=t.id) patients,

                     (select count(*) from appointments x where x.tenant_id=t.id) appointments,

                     (select count(*) from bills x where x.tenant_id=t.id) bills,

                     (select count(*) from users x where x.tenant_id=t.id and x.active) users from tenants t order by t.created_at desc""")

        rows = c.fetchall()

    return rows if p["role"] != "partner" else [r for r in rows if str(r["id"]) in p["tids"]]

class TenantPatch(BaseModel):

    plan: Optional[str] = Field(None, pattern=r"^(basic|advance|pro)$")

    status: Optional[str] = Field(None, pattern=r"^(active|trial|suspended)$")

    modules_add: Optional[List[str]] = None

    modules_remove: Optional[List[str]] = None

    domain: Optional[str] = None

@app.patch("/api/platform/tenants/{tid}")

def patch_tenant(tid: str, b: TenantPatch, p=Depends(need_plat("superadmin", "partner"))):

    if p["role"] == "partner" and (tid not in p["tids"] or b.plan or b.modules_add or b.modules_remove):

        raise HTTPException(403, "Partners can only suspend or reactivate their own hospitals")

    d = b.model_dump(exclude_none=True)

    if not d:

        return {"ok": True}

    with platform_tx() as c:

        sets = ", ".join(f"{k}=%s" for k in d)

        c.execute(f"update tenants set {sets} where id=%s returning id", (*d.values(), tid))

        if not c.fetchone():

            raise HTTPException(404, "Hospital not found")

    return {"ok": True}

@app.post("/api/platform/dispatch")

def dispatch_all(p=Depends(need_plat("superadmin"))):

    with platform_tx() as c:

        c.execute("select id,settings from tenants where status<>'suspended'")

        ts = c.fetchall()

    return {"sent": sum(dispatch(t["id"], t["settings"]) for t in ts)}

from .phase2 import router as phase2_router  # noqa: E402

app.include_router(phase2_router)

app.mount("/app", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static"), html=True), name="static")
