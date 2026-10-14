"""Phase 2: EMR, IPD (beds, admissions, nursing, MAR, diet), IPD billing (deposits, GST, packages, discounts), discharge."""
import csv, io
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from psycopg2.extras import Json

from .app import D, audit, branding, fy, need, next_no, today, tz
from .db import tenant_tx

router = APIRouter(prefix="/api")
M2 = Decimal("0.01")
q2 = lambda x: Decimal(x).quantize(M2, rounding=ROUND_HALF_UP)


def one(c, sql, a=()):
    c.execute(sql, a)
    return c.fetchone()


def allr(c, sql, a=()):
    c.execute(sql, a)
    return c.fetchall()


def nf(x, what):
    if not x:
        raise HTTPException(404, f"{what} not found")
    return x


def adm_open(c, aid):
    a = nf(one(c, "select * from admissions where id=%s", (aid,)), "Admission")
    return a


# ======================= EMR =======================
class Diag(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=2)


@router.post("/diagnoses/import")
def import_diagnoses(rows: List[Diag], ctx=Depends(need("emr:w", "emr"))):
    """Hospital imports its own diagnosis list (e.g. licensed ICD export). The platform ships no medical content."""
    n = 0
    with tenant_tx(ctx.tid) as c:
        for r in rows:
            c.execute("insert into diagnosis_master(tenant_id,code,name) values(%s,%s,%s) on conflict (tenant_id,code) do update set name=excluded.name", (ctx.tid, r.code.upper(), r.name))
            n += 1
        audit(c, ctx, "import", "diagnosis_master", None, new={"rows": n})
    return {"imported": n}


@router.get("/diagnoses/search")
def search_diag(q: str = "", ctx=Depends(need("emr:r", "emr"))):
    with tenant_tx(ctx.tid) as c:
        return allr(c, "select code,name from diagnosis_master where code ilike %s or name ilike %s order by code limit 20", (f"{q}%", f"%{q}%"))


class Allergy(BaseModel):
    allergen: str = Field(min_length=1)
    reaction: str = ""
    severity: str = ""


@router.post("/patients/{pid}/allergies")
def add_allergy(pid: str, b: Allergy, ctx=Depends(need("emr:w", "emr"))):
    with tenant_tx(ctx.tid) as c:
        nf(one(c, "select 1 from patients where id=%s", (pid,)), "Patient")
        r = one(c, "insert into allergies(tenant_id,patient_id,allergen,reaction,severity,noted_by) values(%s,%s,%s,%s,%s,%s) returning *", (ctx.tid, pid, b.allergen, b.reaction, b.severity, ctx.uid))
        audit(c, ctx, "create", "allergy", r["id"], new=b.model_dump())
    return r


class VitalsIn(BaseModel):
    patient_id: str
    admission_id: Optional[str] = None
    appointment_id: Optional[str] = None
    temp: Optional[float] = Field(None, ge=25, le=45)
    pulse: Optional[int] = Field(None, ge=0, le=300)
    bp_sys: Optional[int] = Field(None, ge=0, le=350)
    bp_dia: Optional[int] = Field(None, ge=0, le=250)
    resp: Optional[int] = Field(None, ge=0, le=120)
    spo2: Optional[int] = Field(None, ge=0, le=100)
    weight: Optional[float] = Field(None, ge=0, le=500)
    height: Optional[float] = Field(None, ge=0, le=300)
    notes: str = ""


@router.post("/vitals")
def add_vitals(b: VitalsIn, ctx=Depends(need("vitals:w", "emr"))):
    with tenant_tx(ctx.tid) as c:
        nf(one(c, "select 1 from patients where id=%s", (b.patient_id,)), "Patient")
        r = one(c, "insert into vitals(tenant_id,patient_id,admission_id,appointment_id,temp,pulse,bp_sys,bp_dia,resp,spo2,weight,height,notes,recorded_by) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                (ctx.tid, b.patient_id, b.admission_id, b.appointment_id, b.temp, b.pulse, b.bp_sys, b.bp_dia, b.resp, b.spo2, b.weight, b.height, b.notes, ctx.uid))
        audit(c, ctx, "create", "vitals", r["id"], new={"patient": b.patient_id})
    return r


class Enc(BaseModel):
    patient_id: str
    appointment_id: Optional[str] = None
    admission_id: Optional[str] = None
    complaints: str = ""
    history: str = ""
    examination: str = ""
    diagnosis_code: str = ""
    diagnosis_text: str = ""
    plan: str = ""
    follow_up_date: Optional[date] = None


@router.post("/encounters")
def add_enc(b: Enc, ctx=Depends(need("emr:w", "emr"))):
    if not ctx.doctor_id:
        raise HTTPException(403, "Only a doctor account linked to a doctor profile can write clinical notes")
    with tenant_tx(ctx.tid) as c:
        nf(one(c, "select 1 from patients where id=%s", (b.patient_id,)), "Patient")
        r = one(c, "insert into encounters(tenant_id,patient_id,doctor_id,appointment_id,admission_id,complaints,history,examination,diagnosis_code,diagnosis_text,plan,follow_up_date) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                (ctx.tid, b.patient_id, ctx.doctor_id, b.appointment_id, b.admission_id, b.complaints, b.history, b.examination, b.diagnosis_code, b.diagnosis_text, b.plan, b.follow_up_date))
        audit(c, ctx, "create", "encounter", r["id"], new={"patient": b.patient_id, "dx": b.diagnosis_code})
    return r


@router.get("/patients/{pid}/timeline")
def timeline(pid: str, ctx=Depends(need("emr:r", "emr"))):
    with tenant_tx(ctx.tid) as c:
        p = nf(one(c, "select * from patients where id=%s", (pid,)), "Patient")
        ev = []
        for r in allr(c, "select e.*, d.name as doctor_name from encounters e join doctors d on d.id=e.doctor_id where patient_id=%s", (pid,)):
            ev.append({"at": r["created_at"], "type": "encounter", "title": f"Consultation: {r['diagnosis_text'] or r['complaints'][:60]}", "by": r["doctor_name"], "id": r["id"]})
        for r in allr(c, "select * from prescriptions where patient_id=%s", (pid,)):
            ev.append({"at": r["created_at"], "type": "prescription", "title": "Prescription: " + (r["diagnosis"] or ""), "id": r["id"]})
        for r in allr(c, "select * from admissions where patient_id=%s", (pid,)):
            ev.append({"at": r["admitted_at"], "type": "admission", "title": f"Admission {r['adm_no']} ({r['status']})", "id": r["id"]})
        for r in allr(c, "select * from vitals where patient_id=%s", (pid,)):
            ev.append({"at": r["recorded_at"], "type": "vitals", "title": f"Vitals: BP {r['bp_sys']}/{r['bp_dia']}, pulse {r['pulse']}, SpO2 {r['spo2']}, temp {r['temp']}", "id": r["id"]})
        for r in allr(c, "select * from bills where patient_id=%s", (pid,)):
            ev.append({"at": r["created_at"], "type": "bill", "title": f"OPD bill {r['bill_no']}", "id": r["id"]})
        for r in allr(c, "select id,title,created_at from documents where patient_id=%s", (pid,)):
            ev.append({"at": r["created_at"], "type": "document", "title": r["title"], "id": r["id"]})
        al = allr(c, "select allergen,reaction,severity from allergies where patient_id=%s", (pid,))
    ev.sort(key=lambda x: x["at"], reverse=True)
    return {"patient": p, "allergies": al, "events": ev}


class Tmpl(BaseModel):
    name: str = Field(min_length=2)
    items: List[dict] = []
    advice: str = ""


@router.post("/rx-templates")
def add_tmpl(b: Tmpl, ctx=Depends(need("emr:w", "emr"))):
    if not ctx.doctor_id:
        raise HTTPException(403, "Only doctors can save templates")
    with tenant_tx(ctx.tid) as c:
        r = one(c, "insert into rx_templates(tenant_id,doctor_id,name,items,advice) values(%s,%s,%s,%s,%s) returning *", (ctx.tid, ctx.doctor_id, b.name, Json(b.items), b.advice))
        audit(c, ctx, "create", "rx_template", r["id"], new={"name": b.name})
    return r


@router.get("/rx-templates")
def get_tmpl(ctx=Depends(need("emr:r", "emr"))):
    with tenant_tx(ctx.tid) as c:
        return allr(c, "select * from rx_templates where doctor_id=%s::uuid or %s::uuid is null order by name", (ctx.doctor_id, ctx.doctor_id))


class DocIn(BaseModel):
    patient_id: str
    admission_id: Optional[str] = None
    doc_type: str = Field(pattern=r"^(certificate|consent|other)$")
    title: str = Field(min_length=2)
    body: str = Field(min_length=2)


@router.post("/documents")
def add_doc(b: DocIn, ctx=Depends(need("emr:w", "emr"))):
    with tenant_tx(ctx.tid) as c:
        nf(one(c, "select 1 from patients where id=%s", (b.patient_id,)), "Patient")
        r = one(c, "insert into documents(tenant_id,patient_id,admission_id,doc_type,title,body,created_by) values(%s,%s,%s,%s,%s,%s,%s) returning *", (ctx.tid, b.patient_id, b.admission_id, b.doc_type, b.title, b.body, ctx.uid))
        audit(c, ctx, "create", "document", r["id"], new={"type": b.doc_type, "title": b.title})
    return r


@router.get("/documents/{did}")
def get_doc(did: str, ctx=Depends(need("emr:r", "emr"))):
    with tenant_tx(ctx.tid) as c:
        r = nf(one(c, "select d.*, p.name as patient_name, p.uhid from documents d join patients p on p.id=d.patient_id where d.id=%s", (did,)), "Document")
    r["branding"] = branding(ctx.t)
    return r


# ======================= IPD: wards, beds, packages =======================
class WardIn(BaseModel):
    name: str = Field(min_length=1)
    category: str = "general"
    daily_rate: float = Field(ge=0)
    gst_pct: float = Field(0, ge=0, le=100)
    beds: List[str] = []


@router.post("/wards")
def add_ward(b: WardIn, ctx=Depends(need("ipd:admin", "ipd"))):
    try:
        with tenant_tx(ctx.tid) as c:
            w = one(c, "insert into wards(tenant_id,name,category,daily_rate,gst_pct) values(%s,%s,%s,%s,%s) returning *", (ctx.tid, b.name, b.category, b.daily_rate, b.gst_pct))
            for n in b.beds:
                c.execute("insert into beds(tenant_id,ward_id,bed_no) values(%s,%s,%s)", (ctx.tid, w["id"], n))
            audit(c, ctx, "create", "ward", w["id"], new=b.model_dump())
        return w
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "Ward or bed number already exists")


@router.post("/wards/{wid}/beds")
def add_beds(wid: str, beds: List[str], ctx=Depends(need("ipd:admin", "ipd"))):
    try:
        with tenant_tx(ctx.tid) as c:
            nf(one(c, "select 1 from wards where id=%s", (wid,)), "Ward")
            for n in beds:
                c.execute("insert into beds(tenant_id,ward_id,bed_no) values(%s,%s,%s)", (ctx.tid, wid, n))
            audit(c, ctx, "create", "beds", wid, new={"beds": beds})
        return {"added": len(beds)}
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "Bed number already exists in this ward")


@router.get("/beds/board")
def board(ctx=Depends(need("ipd:r", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        rows = allr(c, """select w.id as ward_id, w.name as ward, w.category, w.daily_rate, b.id as bed_id, b.bed_no,
                          a.id as admission_id, a.adm_no, a.status, p.name as patient_name, p.uhid
                          from wards w join beds b on b.ward_id=w.id and b.active
                          left join admissions a on a.bed_id=b.id and a.status<>'discharged'
                          left join patients p on p.id=a.patient_id where w.active order by w.name, b.bed_no""")
    wards = {}
    for r in rows:
        w = wards.setdefault(str(r["ward_id"]), {"ward": r["ward"], "category": r["category"], "daily_rate": r["daily_rate"], "beds": []})
        w["beds"].append({"bed_id": r["bed_id"], "bed_no": r["bed_no"], "state": ("occupied" if r["admission_id"] else "vacant"),
                          "admission_id": r["admission_id"], "adm_no": r["adm_no"], "patient": r["patient_name"], "uhid": r["uhid"], "status": r["status"]})
    return list(wards.values())


class PkgIn(BaseModel):
    name: str = Field(min_length=2)
    price: float = Field(ge=0)
    includes_room: bool = False
    gst_pct: float = Field(0, ge=0, le=100)


@router.post("/packages")
def add_pkg(b: PkgIn, ctx=Depends(need("ipd:admin", "ipd_billing"))):
    try:
        with tenant_tx(ctx.tid) as c:
            r = one(c, "insert into packages(tenant_id,name,price,includes_room,gst_pct) values(%s,%s,%s,%s,%s) returning *", (ctx.tid, b.name, b.price, b.includes_room, b.gst_pct))
            audit(c, ctx, "create", "package", r["id"], new=b.model_dump())
        return r
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "Package name already exists")


@router.get("/packages")
def pkgs(ctx=Depends(need("ipd:r", "ipd_billing"))):
    with tenant_tx(ctx.tid) as c:
        return allr(c, "select * from packages where active order by name")


# ======================= IPD: admission, transfer, notes, orders =======================
class AdmIn(BaseModel):
    patient_id: str
    doctor_id: str
    bed_id: str
    package_id: Optional[str] = None
    reason: str = ""
    diagnosis: str = ""


def start_stay(c, ctx, aid, bed_id):
    bed = nf(one(c, "select b.id, w.name, w.daily_rate, w.gst_pct from beds b join wards w on w.id=b.ward_id where b.id=%s and b.active and w.active", (bed_id,)), "Bed")
    c.execute("insert into bed_stays(tenant_id,admission_id,bed_id,ward_name,daily_rate,gst_pct) values(%s,%s,%s,%s,%s,%s)", (ctx.tid, aid, bed_id, bed["name"], bed["daily_rate"], bed["gst_pct"]))


@router.post("/admissions")
def admit(b: AdmIn, ctx=Depends(need("ipd:w", "ipd"))):
    try:
        with tenant_tx(ctx.tid) as c:
            nf(one(c, "select 1 from patients where id=%s", (b.patient_id,)), "Patient")
            nf(one(c, "select 1 from doctors where id=%s and active", (b.doctor_id,)), "Doctor")
            n = next_no(c, ctx.tid, f"adm:{fy(today(ctx.t))}")
            no = f"{ctx.t['settings'].get('adm_prefix', 'ADM')}/{fy(today(ctx.t))}/{n:05d}"
            a = one(c, "insert into admissions(tenant_id,adm_no,patient_id,doctor_id,bed_id,package_id,reason,diagnosis,created_by) values(%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                    (ctx.tid, no, b.patient_id, b.doctor_id, b.bed_id, b.package_id, b.reason, b.diagnosis, ctx.uid))
            start_stay(c, ctx, a["id"], b.bed_id)
            audit(c, ctx, "create", "admission", a["id"], new={"adm_no": no, "bed": b.bed_id})
        return a
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "Bed is occupied or the patient is already admitted")


@router.get("/admissions")
def list_adm(status: str = "active", ctx=Depends(need("ipd:r", "ipd"))):
    cond = "a.status<>'discharged'" if status == "active" else "true"
    with tenant_tx(ctx.tid) as c:
        return allr(c, f"""select a.id,a.adm_no,a.status,a.admitted_at,a.discharged_at,p.name as patient_name,p.uhid,d.name as doctor_name,w.name as ward,b.bed_no
                           from admissions a join patients p on p.id=a.patient_id join doctors d on d.id=a.doctor_id join beds b on b.id=a.bed_id join wards w on w.id=b.ward_id
                           where {cond} order by a.admitted_at desc limit 200""")


@router.get("/admissions/{aid}")
def get_adm(aid: str, ctx=Depends(need("ipd:r", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        a = adm_open(c, aid)
        a["patient"] = one(c, "select * from patients where id=%s", (a["patient_id"],))
        a["stays"] = allr(c, "select * from bed_stays where admission_id=%s order by from_at", (aid,))
        a["nursing_notes"] = allr(c, "select * from nursing_notes where admission_id=%s order by at desc", (aid,))
        a["vitals"] = allr(c, "select * from vitals where admission_id=%s order by recorded_at desc limit 100", (aid,))
        a["med_orders"] = allr(c, "select * from med_orders where admission_id=%s order by ordered_at desc", (aid,))
        a["mar"] = allr(c, "select * from mar_entries where admission_id=%s order by at desc limit 200", (aid,))
        a["diet"] = allr(c, "select * from diet_orders where admission_id=%s order by at desc", (aid,))
        a["encounters"] = allr(c, "select * from encounters where admission_id=%s order by created_at desc", (aid,))
        a["allergies"] = allr(c, "select allergen,reaction,severity from allergies where patient_id=%s", (a["patient_id"],))
    return a


class TransferIn(BaseModel):
    bed_id: str


@router.post("/admissions/{aid}/transfer")
def transfer(aid: str, b: TransferIn, ctx=Depends(need("ipd:w", "ipd"))):
    try:
        with tenant_tx(ctx.tid) as c:
            a = adm_open(c, aid)
            if a["status"] == "discharged":
                raise HTTPException(409, "Patient is already discharged")
            if one(c, "select 1 from ipd_bills where admission_id=%s and kind='final' and status='active'", (aid,)):
                raise HTTPException(409, "Final bill exists; void it to change the bed")
            c.execute("update bed_stays set to_at=now() where admission_id=%s and to_at is null", (aid,))
            c.execute("update admissions set bed_id=%s where id=%s", (b.bed_id, aid))
            start_stay(c, ctx, aid, b.bed_id)
            audit(c, ctx, "transfer", "admission", aid, old={"bed": a["bed_id"]}, new={"bed": b.bed_id})
        return {"ok": True}
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "Target bed is occupied")


class DocChange(BaseModel):
    doctor_id: str


@router.patch("/admissions/{aid}/doctor")
def change_doctor(aid: str, b: DocChange, ctx=Depends(need("ipd:w", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        a = adm_open(c, aid)
        nf(one(c, "select 1 from doctors where id=%s and active", (b.doctor_id,)), "Doctor")
        c.execute("update admissions set doctor_id=%s where id=%s", (b.doctor_id, aid))
        audit(c, ctx, "update", "admission", aid, old={"doctor": a["doctor_id"]}, new={"doctor": b.doctor_id})
    return {"ok": True}


def active_adm(c, aid):
    a = adm_open(c, aid)
    if a["status"] == "discharged":
        raise HTTPException(409, "Admission is closed")
    return a


class NoteIn(BaseModel):
    note: str = Field(min_length=2)


@router.post("/admissions/{aid}/nursing-notes")
def nursing(aid: str, b: NoteIn, ctx=Depends(need("nursing:w", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        active_adm(c, aid)
        r = one(c, "insert into nursing_notes(tenant_id,admission_id,note,by_user,by_name) values(%s,%s,%s,%s,%s) returning *", (ctx.tid, aid, b.note, ctx.uid, ctx.username))
        audit(c, ctx, "create", "nursing_note", r["id"], new={"admission": aid})
    return r


class MedIn(BaseModel):
    drug: str = Field(min_length=2)
    dose: str = ""
    route: str = ""
    frequency: str = ""


@router.post("/admissions/{aid}/med-orders")
def med_order(aid: str, b: MedIn, ctx=Depends(need("orders:w", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        active_adm(c, aid)
        r = one(c, "insert into med_orders(tenant_id,admission_id,drug,dose,route,frequency,ordered_by) values(%s,%s,%s,%s,%s,%s,%s) returning *", (ctx.tid, aid, b.drug, b.dose, b.route, b.frequency, ctx.uid))
        audit(c, ctx, "create", "med_order", r["id"], new=b.model_dump())
    return r


@router.post("/med-orders/{oid}/stop")
def stop_order(oid: str, ctx=Depends(need("orders:w", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        o = nf(one(c, "select * from med_orders where id=%s", (oid,)), "Order")
        c.execute("update med_orders set active=false, stopped_at=now() where id=%s", (oid,))
        audit(c, ctx, "stop", "med_order", oid, old={"drug": o["drug"]})
    return {"ok": True}


class GiveIn(BaseModel):
    status: str = Field(pattern=r"^(given|held|refused)$")
    note: str = ""


@router.post("/med-orders/{oid}/give")
def give(oid: str, b: GiveIn, ctx=Depends(need("mar:w", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        o = nf(one(c, "select * from med_orders where id=%s", (oid,)), "Order")
        if not o["active"]:
            raise HTTPException(409, "Order is stopped")
        active_adm(c, o["admission_id"])
        r = one(c, "insert into mar_entries(tenant_id,order_id,admission_id,status,note,by_user,by_name) values(%s,%s,%s,%s,%s,%s,%s) returning *", (ctx.tid, oid, o["admission_id"], b.status, b.note, ctx.uid, ctx.username))
        audit(c, ctx, "mar", "med_order", oid, new={"status": b.status})
    return r


class DietIn(BaseModel):
    diet: str = Field(min_length=2)
    notes: str = ""


@router.post("/admissions/{aid}/diet")
def diet(aid: str, b: DietIn, ctx=Depends(need("orders:w", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        active_adm(c, aid)
        c.execute("update diet_orders set active=false where admission_id=%s", (aid,))
        r = one(c, "insert into diet_orders(tenant_id,admission_id,diet,notes,ordered_by) values(%s,%s,%s,%s,%s) returning *", (ctx.tid, aid, b.diet, b.notes, ctx.uid))
        audit(c, ctx, "create", "diet_order", r["id"], new=b.model_dump())
    return r


# ======================= IPD billing =======================
def room_lines(c, t, a, end_dt, final):
    """One charge per calendar date the patient occupied a bed. The discharge date counts only if discharge is after the checkout hour."""
    z = tz(t)
    hour = int(t["settings"].get("ipd_checkout_hour", 12))
    stays = allr(c, "select * from bed_stays where admission_id=%s order by from_at", (a["id"],))
    per_date = {}
    for s in stays:
        start = s["from_at"].astimezone(z)
        end = (s["to_at"] or end_dt).astimezone(z)
        d = start.date()
        last = end.date()
        if final and s["to_at"] is None and last > start.date() and end.hour * 60 + end.minute <= hour * 60:
            last = last - timedelta(days=1)
        while d <= last:
            per_date[d] = (s["ward_name"], s["daily_rate"], s["gst_pct"])
            d += timedelta(days=1)
    grouped = {}
    for d, (name, rate, gst) in per_date.items():
        g = grouped.setdefault((name, rate, gst), 0)
        grouped[(name, rate, gst)] = g + 1
    return [{"description": f"Bed charges: {k[0]}", "qty": n, "price": k[1], "gst_pct": k[2], "sac": "", "kind": "room"} for k, n in grouped.items()]


def account(c, t, a, discount=Decimal(0), end_dt=None):
    final = end_dt is not None or bool(a["discharge_ts"])
    end_dt = end_dt or a["discharge_ts"] or datetime.now(tz(t))
    pkg = one(c, "select * from packages where id=%s", (a["package_id"],)) if a["package_id"] else None
    lines = []
    if not (pkg and pkg["includes_room"]):
        lines += room_lines(c, t, a, end_dt, final)
    if pkg:
        lines.append({"description": f"Package: {pkg['name']}", "qty": 1, "price": pkg["price"], "gst_pct": pkg["gst_pct"], "sac": "", "kind": "package"})
    for ch in allr(c, "select * from ipd_charges where admission_id=%s and not cancelled order by created_at", (a["id"],)):
        lines.append({"description": ch["description"] + (" (in package)" if ch["in_package"] else ""), "qty": ch["qty"],
                      "price": Decimal(0) if ch["in_package"] else ch["price"], "gst_pct": ch["gst_pct"], "sac": ch["sac_code"], "kind": "service", "id": str(ch["id"])})
    sub = sum((q2(D(l["price"]) * l["qty"]) for l in lines), Decimal(0))
    if discount > sub:
        raise HTTPException(422, "Discount cannot exceed the bill amount")
    factor = (sub - discount) / sub if sub > 0 else Decimal(1)
    taxable = tax = Decimal(0)
    for l in lines:
        amt = q2(D(l["price"]) * l["qty"])
        tv = q2(amt * factor)
        tx = q2(tv * D(l["gst_pct"]) / 100)
        l.update({"amount": amt, "taxable": tv, "tax": tx, "total": tv + tx})
        taxable += tv; tax += tx
    cgst = q2(tax / 2)
    sgst = tax - cgst
    total = taxable + tax
    pays = allr(c, "select kind, sum(amount) s from ipd_payments where admission_id=%s group by kind", (a["id"],))
    pm = {r["kind"]: r["s"] for r in pays}
    received = pm.get("advance", Decimal(0)) + pm.get("payment", Decimal(0)) - pm.get("refund", Decimal(0))
    return {"lines": lines, "subtotal": sub, "discount": discount, "taxable": taxable, "cgst": cgst, "sgst": sgst, "total": total,
            "advance": pm.get("advance", Decimal(0)), "payments": pm.get("payment", Decimal(0)), "refunds": pm.get("refund", Decimal(0)),
            "received": received, "balance": total - received}


class ChargeIn(BaseModel):
    tariff_id: Optional[str] = None
    description: str = ""
    qty: int = Field(1, ge=1, le=999)
    price: Optional[float] = Field(None, ge=0)
    gst_pct: float = Field(0, ge=0, le=100)
    in_package: bool = False


@router.post("/admissions/{aid}/charges")
def add_charge(aid: str, b: ChargeIn, ctx=Depends(need("ipd_bill:w", "ipd_billing"))):
    with tenant_tx(ctx.tid) as c:
        active_adm(c, aid)
        if one(c, "select 1 from ipd_bills where admission_id=%s and kind='final' and status='active'", (aid,)):
            raise HTTPException(409, "Final bill is generated; void it to add charges")
        if b.tariff_id:
            tf = nf(one(c, "select name,price,gst_pct,sac_code from tariff_items where id=%s and active", (b.tariff_id,)), "Tariff item")
            desc, price, gst, sac = tf["name"], tf["price"], tf["gst_pct"], tf["sac_code"]
        else:
            if ctx.role != "admin" or b.price is None or not b.description:
                raise HTTPException(422, "Free-text charges need description and price and can only be added by an admin")
            desc, price, gst, sac = b.description, D(b.price), D(b.gst_pct), ""
        r = one(c, "insert into ipd_charges(tenant_id,admission_id,description,qty,price,gst_pct,sac_code,in_package,added_by) values(%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *", (ctx.tid, aid, desc, b.qty, price, gst, sac, b.in_package, ctx.uid))
        audit(c, ctx, "create", "ipd_charge", r["id"], new={"desc": desc, "qty": b.qty, "price": price})
    return r


@router.delete("/admissions/{aid}/charges/{cid}")
def cancel_charge(aid: str, cid: str, ctx=Depends(need("ipd_bill:w", "ipd_billing"))):
    with tenant_tx(ctx.tid) as c:
        if one(c, "select 1 from ipd_bills where admission_id=%s and kind='final' and status='active'", (aid,)):
            raise HTTPException(409, "Final bill is generated; void it first")
        r = nf(one(c, "update ipd_charges set cancelled=true where id=%s and admission_id=%s returning id", (cid, aid)), "Charge")
        audit(c, ctx, "cancel", "ipd_charge", cid)
    return {"ok": True}


@router.get("/admissions/{aid}/account")
def get_account(aid: str, discount: float = 0, ctx=Depends(need("ipd_bill:r", "ipd_billing"))):
    with tenant_tx(ctx.tid) as c:
        a = adm_open(c, aid)
        acc = account(c, ctx.t, a, D(discount))
        acc["payments_list"] = allr(c, "select * from ipd_payments where admission_id=%s order by at", (aid,))
        acc["bills"] = allr(c, "select id,bill_no,kind,status,total,created_at from ipd_bills where admission_id=%s order by created_at", (aid,))
        acc["discount_requests"] = allr(c, "select * from discount_requests where admission_id=%s order by at desc", (aid,))
    return acc


class PayIn(BaseModel):
    kind: str = Field(pattern=r"^(advance|payment|refund)$")
    mode: str = Field(pattern=r"^(cash|upi|card)$")
    amount: float = Field(gt=0)
    ref: str = ""


@router.post("/admissions/{aid}/payments")
def pay(aid: str, b: PayIn, ctx=Depends(need("ipd_bill:w", "ipd_billing"))):
    with tenant_tx(ctx.tid) as c:
        a = adm_open(c, aid)
        if b.kind != "refund" and a["status"] == "discharged":
            raise HTTPException(409, "Admission is closed")
        amt = D(b.amount)
        if b.kind == "refund":
            fin = one(c, "select total from ipd_bills where admission_id=%s and kind='final' and status='active'", (aid,))
            if not fin:
                raise HTTPException(422, "Refund is allowed only after the final bill is generated")
            acc = account(c, ctx.t, a, D(0))
            over = acc["received"] - fin["total"]
            if amt > over:
                raise HTTPException(422, f"Refund cannot exceed the excess received ({over})")
        n = next_no(c, ctx.tid, f"rcpt:{fy(today(ctx.t))}")
        no = f"{ctx.t['settings'].get('receipt_prefix', 'RC')}/{fy(today(ctx.t))}/{n:05d}"
        r = one(c, "insert into ipd_payments(tenant_id,admission_id,kind,mode,amount,ref,receipt_no,by_user) values(%s,%s,%s,%s,%s,%s,%s,%s) returning *", (ctx.tid, aid, b.kind, b.mode, amt, b.ref, no, ctx.uid))
        audit(c, ctx, "create", "ipd_payment", r["id"], new={"kind": b.kind, "amount": amt, "receipt": no})
    return r


class DiscReq(BaseModel):
    amount: float = Field(gt=0)
    reason: str = Field(min_length=3)


@router.post("/admissions/{aid}/discount-requests")
def request_discount(aid: str, b: DiscReq, ctx=Depends(need("ipd_bill:w", "ipd_billing"))):
    with tenant_tx(ctx.tid) as c:
        active_adm(c, aid)
        r = one(c, "insert into discount_requests(tenant_id,admission_id,amount,reason,requested_by) values(%s,%s,%s,%s,%s) returning *", (ctx.tid, aid, b.amount, b.reason, ctx.uid))
        audit(c, ctx, "request", "discount", r["id"], new=b.model_dump())
    return r


class Decide(BaseModel):
    approve: bool


@router.post("/discount-requests/{rid}/decide")
def decide(rid: str, b: Decide, ctx=Depends(need("discount:approve"))):
    with tenant_tx(ctx.tid) as c:
        r = nf(one(c, "update discount_requests set status=%s, decided_by=%s where id=%s and status='pending' returning *", ("approved" if b.approve else "rejected", ctx.uid, rid)), "Pending request")
        audit(c, ctx, "decide", "discount", rid, new={"approved": b.approve})
    return r


class BillIn2(BaseModel):
    kind: str = Field(pattern=r"^(interim|final)$")
    discount: float = Field(0, ge=0)


@router.post("/admissions/{aid}/bills")
def make_ipd_bill(aid: str, b: BillIn2, ctx=Depends(need("ipd_bill:w", "ipd_billing"))):
    try:
        with tenant_tx(ctx.tid) as c:
            a = active_adm(c, aid)
            disc = D(b.discount)
            if b.kind == "final" and one(c, "select 1 from ipd_bills where admission_id=%s and kind='final' and status='active'", (aid,)):
                raise HTTPException(409, "A final bill already exists")
            end_dt = None
            if b.kind == "final":
                if not a["discharge_ts"]:
                    c.execute("update admissions set discharge_ts=now() where id=%s returning *", (aid,))
                    a = c.fetchone()
                end_dt = a["discharge_ts"]
            acc = account(c, ctx.t, a, D(0), end_dt)
            limit = D(ctx.t["settings"].get("discount_limit_pct", 10))
            if disc > 0 and ctx.role != "admin" and disc > acc["subtotal"] * limit / 100:
                req = one(c, "select * from discount_requests where admission_id=%s and status='approved' and amount>=%s order by at desc limit 1", (aid, disc))
                if not req:
                    raise HTTPException(403, f"Discount above {limit}% needs admin approval: create a discount request first")
                c.execute("update discount_requests set status='used' where id=%s", (req["id"],))
            acc = account(c, ctx.t, a, disc, end_dt)
            n = next_no(c, ctx.tid, f"ipdbill:{fy(today(ctx.t))}")
            no = f"{ctx.t['settings'].get('ipd_bill_prefix', 'IP')}/{fy(today(ctx.t))}/{n:05d}"
            lines = [{**l, "price": str(l["price"]), "amount": str(l["amount"]), "taxable": str(l["taxable"]), "tax": str(l["tax"]), "total": str(l["total"]), "gst_pct": str(l["gst_pct"])} for l in acc["lines"]]
            r = one(c, "insert into ipd_bills(tenant_id,admission_id,bill_no,kind,lines,subtotal,discount,taxable,cgst,sgst,total,created_by) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *",
                    (ctx.tid, aid, no, b.kind, Json(lines), acc["subtotal"], disc, acc["taxable"], acc["cgst"], acc["sgst"], acc["total"], ctx.uid))
            audit(c, ctx, "create", "ipd_bill", r["id"], new={"bill_no": no, "kind": b.kind, "total": acc["total"], "discount": disc})
        return r
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "A final bill already exists")


@router.post("/ipd-bills/{bid}/void")
def void_bill(bid: str, ctx=Depends(need("ipd_bill:void"))):
    with tenant_tx(ctx.tid) as c:
        b = nf(one(c, "update ipd_bills set status='void' where id=%s and status='active' returning *", (bid,)), "Active bill")
        if b["kind"] == "final":
            c.execute("update admissions set discharge_ts=null where id=%s and status<>'discharged'", (b["admission_id"],))
        audit(c, ctx, "void", "ipd_bill", bid, old={"bill_no": b["bill_no"]})
    return {"ok": True}


@router.get("/ipd-bills/{bid}")
def get_ipd_bill(bid: str, ctx=Depends(need("ipd_bill:r", "ipd_billing"))):
    with tenant_tx(ctx.tid) as c:
        b = nf(one(c, "select b.*, p.name as patient_name, p.uhid, p.mobile, a.adm_no, a.admitted_at, a.discharged_at, d.name as doctor_name from ipd_bills b join admissions a on a.id=b.admission_id join patients p on p.id=a.patient_id join doctors d on d.id=a.doctor_id where b.id=%s", (bid,)), "Bill")
        b["payments"] = allr(c, "select kind,mode,amount,receipt_no,at from ipd_payments where admission_id=%s order by at", (b["admission_id"],))
    b["branding"] = branding(ctx.t)
    b["gstin"] = ctx.t["settings"].get("gstin", "")
    b["gst_note"] = "CGST and SGST shown at half of each line's GST rate. The hospital must verify tax treatment and place of supply."
    return b


# ======================= discharge =======================
class SummaryIn(BaseModel):
    text: str = Field(min_length=5)
    condition: str = ""
    advice: str = ""
    medicines: List[dict] = []
    follow_up_date: Optional[date] = None


@router.post("/admissions/{aid}/discharge/initiate")
def initiate(aid: str, ctx=Depends(need("discharge:w", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        a = active_adm(c, aid)
        c.execute("update admissions set status='discharge_initiated' where id=%s", (aid,))
        audit(c, ctx, "discharge_initiate", "admission", aid, old={"status": a["status"]})
    return {"ok": True}


@router.post("/admissions/{aid}/discharge/summary")
def save_summary(aid: str, b: SummaryIn, ctx=Depends(need("discharge:w", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        active_adm(c, aid)
        c.execute("update admissions set summary=%s where id=%s", (Json(b.model_dump(mode="json")), aid))
        audit(c, ctx, "summary", "admission", aid, new={"len": len(b.text)})
    return {"ok": True}


@router.post("/admissions/{aid}/clearance")
def clearance(aid: str, dept: str, ctx=Depends(need("clearance:nursing"))):
    col = {"nursing": "clr_nursing", "pharmacy": "clr_pharmacy"}.get(dept)
    if not col:
        raise HTTPException(422, "dept must be nursing or pharmacy")
    if dept == "pharmacy" and ctx.role != "admin":
        raise HTTPException(403, "Pharmacy clearance is given by the admin until the pharmacy module is added")
    with tenant_tx(ctx.tid) as c:
        active_adm(c, aid)
        c.execute(f"update admissions set {col}=true where id=%s", (aid,))
        audit(c, ctx, "clearance", "admission", aid, new={"dept": dept})
    return {"ok": True}


@router.get("/admissions/{aid}/discharge/status")
def dstatus(aid: str, ctx=Depends(need("ipd:r", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        return clear_state(c, ctx, adm_open(c, aid))


def clear_state(c, ctx, a):
    fin = one(c, "select * from ipd_bills where admission_id=%s and kind='final' and status='active'", (a["id"],))
    bal = None
    if fin:
        acc = account(c, ctx.t, a, D(fin["discount"]), a["discharge_ts"])
        bal = acc["total"] - acc["received"]
    return {"summary": bool((a["summary"] or {}).get("text")), "nursing": a["clr_nursing"], "pharmacy": a["clr_pharmacy"],
            "final_bill": bool(fin), "balance": bal, "billing": bool(fin) and bal == 0}


@router.post("/admissions/{aid}/discharge/complete")
def complete(aid: str, ctx=Depends(need("discharge:w", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        a = active_adm(c, aid)
        st = clear_state(c, ctx, a)
        missing = [k for k in ("summary", "nursing", "pharmacy", "billing") if not st[k]]
        if missing:
            raise HTTPException(409, "Discharge blocked. Pending: " + ", ".join(missing) + (f" (balance {st['balance']})" if st["balance"] else ""))
        c.execute("update bed_stays set to_at=%s where admission_id=%s and to_at is null", (a["discharge_ts"], aid))
        c.execute("update admissions set status='discharged', discharged_at=discharge_ts where id=%s", (aid,))
        audit(c, ctx, "discharge", "admission", aid, new={"at": a["discharge_ts"]})
    return {"ok": True}


@router.get("/admissions/{aid}/summary-print")
def summary_print(aid: str, ctx=Depends(need("ipd:r", "ipd"))):
    with tenant_tx(ctx.tid) as c:
        a = adm_open(c, aid)
        a["patient"] = one(c, "select * from patients where id=%s", (a["patient_id"],))
        a["doctor"] = one(c, "select name,qualification from doctors where id=%s", (a["doctor_id"],))
        a["allergies"] = allr(c, "select allergen,reaction from allergies where patient_id=%s", (a["patient_id"],))
    a["branding"] = branding(ctx.t)
    return a


# ======================= Tally-style export =======================
@router.get("/reports/vouchers.csv", response_class=PlainTextResponse)
def vouchers(start: date, end: date, ctx=Depends(need("ipd_bill:r", "ipd_billing"))):
    """Generic voucher CSV (date, type, number, party, ledger, debit, credit, narration). It is NOT Tally XML; import mapping is done by the accountant."""
    z = ctx.t["settings"].get("timezone", "Asia/Kolkata")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", "voucher_type", "voucher_no", "party", "ledger", "debit", "credit", "narration"])
    with tenant_tx(ctx.tid) as c:
        for b in allr(c, "select b.*, p.name pn, a.adm_no from ipd_bills b join admissions a on a.id=b.admission_id join patients p on p.id=a.patient_id where b.status='active' and b.kind='final' and (b.created_at at time zone %s)::date between %s and %s order by b.created_at", (z, start, end)):
            d = b["created_at"].astimezone(tz(ctx.t)).date().isoformat()
            w.writerow([d, "Sales", b["bill_no"], b["pn"], "Patient receivable", b["total"], "", f"IPD final bill {b['adm_no']}"])
            w.writerow([d, "Sales", b["bill_no"], b["pn"], "Hospital services income", "", b["taxable"], ""])
            if b["cgst"] > 0:
                w.writerow([d, "Sales", b["bill_no"], b["pn"], "CGST payable", "", b["cgst"], ""])
                w.writerow([d, "Sales", b["bill_no"], b["pn"], "SGST payable", "", b["sgst"], ""])
        for p in allr(c, "select ip.*, pt.name pn from ipd_payments ip join admissions a on a.id=ip.admission_id join patients pt on pt.id=a.patient_id where (ip.at at time zone %s)::date between %s and %s order by ip.at", (z, start, end)):
            d = p["at"].astimezone(tz(ctx.t)).date().isoformat()
            sign = (p["amount"], "") if p["kind"] != "refund" else ("", p["amount"])
            w.writerow([d, "Receipt" if p["kind"] != "refund" else "Payment", p["receipt_no"], p["pn"], f"Cash/Bank ({p['mode']})", sign[0], sign[1], p["kind"]])
            w.writerow([d, "Receipt" if p["kind"] != "refund" else "Payment", p["receipt_no"], p["pn"], "Patient receivable" if p["kind"] != "advance" else "Patient advances", sign[1], sign[0], p["kind"]])
    return out.getvalue()
