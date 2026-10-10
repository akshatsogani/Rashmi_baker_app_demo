from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, time
from io import BytesIO, StringIO
from pathlib import Path

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "staff_manager.db"
DEFAULT_RULES = {
    "days": 30, "absent": "daily", "half": "half", "late": "none",
    "lateAmt": 0.0, "early": "none", "earlyAmt": 0.0,
    "leave": "paid", "weekly": "paid", "purchases": "yes"
}
TABLES = {
    "staff": ["id","name","salary","join_date","start_time","end_time","grace","off_days","active","notes"],
    "attendance": ["id","staff_id","date","status","in_time","out_time","break_min","notes"],
    "ledger": ["id","staff_id","date","type","amount","method","ref","notes"],
    "payments": ["id","staff_id","date","month","amount","method","ref"],
}

st.set_page_config(page_title="Staff Manager", page_icon="🗂️", layout="wide")

def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS staff (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, salary REAL NOT NULL DEFAULT 0,
          join_date TEXT, start_time TEXT, end_time TEXT, grace INTEGER DEFAULT 10,
          off_days TEXT, active TEXT DEFAULT 'active', notes TEXT
        );
        CREATE TABLE IF NOT EXISTS attendance (
          id TEXT PRIMARY KEY, staff_id TEXT NOT NULL, date TEXT NOT NULL, status TEXT NOT NULL,
          in_time TEXT, out_time TEXT, break_min INTEGER DEFAULT 0, notes TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS attendance_staff_date ON attendance(staff_id,date);
        CREATE TABLE IF NOT EXISTS ledger (
          id TEXT PRIMARY KEY, staff_id TEXT NOT NULL, date TEXT NOT NULL, type TEXT NOT NULL,
          amount REAL NOT NULL, method TEXT, ref TEXT, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS payments (
          id TEXT PRIMARY KEY, staff_id TEXT NOT NULL, date TEXT NOT NULL, month TEXT NOT NULL,
          amount REAL NOT NULL, method TEXT, ref TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if not c.execute("SELECT 1 FROM settings WHERE key='rules'").fetchone():
            c.execute("INSERT INTO settings(key,value) VALUES('rules',?)", (json.dumps(DEFAULT_RULES),))

init_db()

def read_table(table):
    with conn() as c:
        return pd.read_sql_query(f"SELECT * FROM {table}", c)

def rules_get():
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key='rules'").fetchone()
    return {**DEFAULT_RULES, **(json.loads(row["value"]) if row else {})}

def rules_save(r):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('rules',?)", (json.dumps(r),))

def save_record(table, record, record_id=None):
    cols = TABLES[table]
    record = {k: record.get(k) for k in cols}
    if record_id:
        record["id"] = record_id
    elif not record.get("id"):
        record["id"] = str(uuid.uuid4())
    with conn() as c:
        if table == "attendance":
            existing = c.execute("SELECT id FROM attendance WHERE staff_id=? AND date=?", (record["staff_id"], record["date"])).fetchone()
            if existing:
                record["id"] = existing["id"]
        if table == "staff" and c.execute("SELECT 1 FROM staff WHERE id=?", (record["id"],)).fetchone():
            assignments = ",".join(f"{k}=?" for k in cols if k != "id")
            c.execute(f"UPDATE staff SET {assignments} WHERE id=?", tuple(record[k] for k in cols if k != "id") + (record["id"],))
        else:
            names = ",".join(cols)
            marks = ",".join("?" for _ in cols)
            c.execute(f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({marks})", tuple(record[k] for k in cols))

def delete_record(table, record_id):
    with conn() as c:
        if table == "staff":
            c.execute("UPDATE staff SET active='inactive' WHERE id=?", (record_id,))
        else:
            c.execute(f"DELETE FROM {table} WHERE id=?", (record_id,))

def now_date():
    return date.today()

def fmt_money(x):
    return f"₹{float(x or 0):,.2f}"

def minutes(t):
    if not t or pd.isna(t): return None
    try:
        h, m = map(int, str(t)[:5].split(":"))
        return h*60+m
    except Exception:
        return None

def duration(a,b):
    x,y=minutes(a),minutes(b)
    if x is None or y is None: return 0
    if y<x: y += 1440
    return max(0,y-x)

def flag_times(att, staff):
    late = early = 0
    if att.get("status") in ("present","half"):
        ain=minutes(att.get("in_time")); start=minutes(staff.get("start_time"))
        aout=minutes(att.get("out_time")); end=minutes(staff.get("end_time"))
        if ain is not None and start is not None and ain > start + int(staff.get("grace") or 0): late=1
        if aout is not None and end is not None and aout < end: early=1
    return late, early

def payroll_for(s, month, staff_df, att_df, ledger_df, payments_df, rules):
    month_start=f"{month}-01"
    y,m=map(int,month.split("-"))
    next_month = date(y+1,1,1) if m==12 else date(y,m+1,1)
    month_end=str(next_month - pd.Timedelta(days=1))[:10]
    a=att_df[(att_df.staff_id==s["id"]) & (att_df.date>=month_start) & (att_df.date<=month_end)]
    daily=float(s["salary"] or 0)/max(1,int(rules.get("days",30) or 30))
    ded=0.0; late_count=early_count=0
    counts={"present":0,"half":0,"absent":0,"leave":0,"weekly":0}
    for _, row in a.iterrows():
        status=row["status"]; counts[status]=counts.get(status,0)+1
        late,early=flag_times(row.to_dict(),s); late_count+=late; early_count+=early
        if status=="absent" and rules["absent"]=="daily": ded+=daily
        if status=="half":
            ded += daily/2 if rules["half"]=="half" else daily if rules["half"]=="full" else 0
        if status=="leave" and rules["leave"]=="unpaid": ded+=daily
        if status=="weekly" and rules["weekly"]=="unpaid": ded+=daily
        if late and rules["late"]=="fixed": ded+=float(rules.get("lateAmt") or 0)
        if early and rules["early"]=="fixed": ded+=float(rules.get("earlyAmt") or 0)
    l=ledger_df[(ledger_df.staff_id==s["id"]) & (ledger_df.date>=month_start) & (ledger_df.date<=month_end)]
    total=lambda typ: float(l[l.type==typ].amount.sum()) if len(l) else 0.0
    advances=total("advance")
    purchases=total("purchase") if rules["purchases"]=="yes" else 0.0
    other=total("deduction"); bonus=total("bonus")
    p=payments_df[(payments_df.staff_id==s["id"]) & (payments_df.month==month)]
    paid=float(p.amount.sum()) if len(p) else 0.0
    gross=float(s["salary"] or 0)+bonus
    net=max(0,gross-ded-advances-purchases-other)
    return {"Worker":s["name"],"Month":month,"Monthly salary":float(s["salary"] or 0),"Bonus":bonus,
            "Attendance deductions":ded,"Advances":advances,"Shop purchases":purchases,
            "Other deductions":other,"Net payable":net,"Paid":paid,"Balance":net-paid,
            "Absent":counts["absent"],"Half-days":counts["half"],"Late flags":late_count,"Early flags":early_count,
            "Daily rate":daily}

def df_or_empty(df, columns):
    return df if len(df) else pd.DataFrame(columns=columns)

def download_csv(df, name):
    st.download_button(f"Download {name} CSV", df.to_csv(index=False).encode("utf-8-sig"), file_name=name, mime="text/csv")

def staff_map(staff):
    return {r["id"]:r["name"] for _,r in staff.iterrows()}

def main():
    st.title("🗂️ Staff Manager")
    st.caption("Owner workspace · Staff profiles, attendance, advances, deductions and payroll")
    staff=read_table("staff"); attendance=read_table("attendance"); ledger=read_table("ledger"); payments=read_table("payments"); rules=rules_get()
    active=staff[staff.active=="active"] if len(staff) else staff
    today=now_date().isoformat(); month=now_date().strftime("%Y-%m")
    payroll_rows=[payroll_for(r.to_dict(),month,staff,attendance,ledger,payments,rules) for _,r in active.iterrows()]
    paid_month=float(payments[payments.month==month].amount.sum()) if len(payments) else 0
    due=sum(max(0,float(x["Balance"])) for x in payroll_rows)
    k1,k2,k3,k4=st.columns(4)
    k1.metric("Active staff",len(active))
    k2.metric("Marked today",int((attendance.date==today).sum()) if len(attendance) else 0)
    k3.metric("Paid this month",fmt_money(paid_month))
    k4.metric("Salary balance",fmt_money(due))
    st.info("Data is stored in a local SQLite database on the app host. Hosted-app storage may be temporary; download regular backups. Do not use a public deployment for real staff data without access controls.")

    tabs=st.tabs(["Overview","Staff","Attendance","Advances & deductions","Payroll","Rules & backup"])
    with tabs[0]:
        st.subheader("Today's attendance")
        if len(active):
            today_rows=[]
            for _,s in active.iterrows():
                a=attendance[(attendance.staff_id==s.id)&(attendance.date==today)]
                rec=a.iloc[0].to_dict() if len(a) else {}
                today_rows.append({"Worker":s["name"],"Status":rec.get("status","Not recorded"),"In":rec.get("in_time","—") or "—","Out":rec.get("out_time","—") or "—"})
            st.dataframe(pd.DataFrame(today_rows), use_container_width=True, hide_index=True)
        else: st.write("Add staff profiles to begin.")
        st.subheader("Current month payroll snapshot")
        if payroll_rows: st.dataframe(pd.DataFrame(payroll_rows)[["Worker","Net payable","Paid","Balance"]],use_container_width=True,hide_index=True)
        else: st.write("No active staff members.")
    with tabs[1]:
        st.subheader("Add or edit staff member")
        options={"— Select staff to edit —":""}
        options.update({f'{r["name"]} · {r["id"][:6]}':r["id"] for _,r in staff.iterrows()})
        chosen=st.selectbox("Edit existing profile (optional)",list(options),key="staff_edit_select")
        current=staff[staff.id==options[chosen]] if options[chosen] and len(staff) else pd.DataFrame()
        old=current.iloc[0].to_dict() if len(current) else {}
        with st.form("staff_form", clear_on_submit=True):
            c1,c2=st.columns(2)
            name=c1.text_input("Full name *",value=old.get("name",""))
            salary=c2.number_input("Monthly salary (₹) *",min_value=0.0,value=float(old.get("salary") or 0),step=500.0)
            join=c1.date_input("Joining date",value=date.fromisoformat(old["join_date"]) if old.get("join_date") else None)
            start=c2.time_input("Usual start time",value=time.fromisoformat(old["start_time"]) if old.get("start_time") else time(9,0))
            end=c1.time_input("Usual end time",value=time.fromisoformat(old["end_time"]) if old.get("end_time") else time(21,0))
            grace=c2.number_input("Grace minutes",min_value=0,max_value=240,value=int(old.get("grace") or 10))
            off=c1.text_input("Weekly off days (optional)",value=old.get("off_days") or "")
            status=c2.selectbox("Status",["active","inactive"],index=0 if old.get("active","active")=="active" else 1)
            notes=st.text_area("Notes (avoid sensitive information)",value=old.get("notes") or "")
            submitted=st.form_submit_button("Save staff profile",type="primary")
        if submitted:
            if not name.strip(): st.error("Enter a staff name.")
            else:
                save_record("staff",{"id":old.get("id") or str(uuid.uuid4()),"name":name.strip(),"salary":salary,"join_date":join.isoformat() if join else "",
                    "start_time":start.strftime("%H:%M"),"end_time":end.strftime("%H:%M"),"grace":grace,"off_days":off.strip(),"active":status,"notes":notes.strip()})
                st.success("Staff profile saved."); st.rerun()
        st.subheader("Staff list")
        if len(staff):
            view=staff.copy()
            st.dataframe(view[["name","salary","join_date","start_time","end_time","grace","off_days","active","notes"]],use_container_width=True,hide_index=True)
            to_archive=st.selectbox("Archive staff profile",["— Select —"]+[f'{r["name"]} · {r["id"][:6]}' for _,r in staff[staff.active=="active"].iterrows()],key="archive_staff")
            if st.button("Archive selected staff"):
                if to_archive!="— Select —":
                    sid=to_archive.rsplit(" · ",1)[-1]
                    match=staff[staff.id.str.startswith(sid)]
                    if len(match): delete_record("staff",match.iloc[0]["id"]); st.rerun()
        else: st.write("No staff profiles yet.")
    with tabs[2]:
        st.subheader("Record attendance and working time")
        if len(staff):
            labels={f'{r["name"]} · {r["id"][:6]}':r["id"] for _,r in staff.iterrows()}
            with st.form("attendance_form"):
                c1,c2=st.columns(2)
                label=c1.selectbox("Worker",list(labels)); att_date=c2.date_input("Date",value=now_date())
                status=c1.selectbox("Status",["present","half","absent","leave","weekly"],format_func=lambda x:{"present":"Present","half":"Half-day","absent":"Absent","leave":"Approved leave","weekly":"Weekly off"}[x])
                in_time=c2.time_input("Clock-in time",value=time(9,0))
                out_time=c1.time_input("Clock-out time",value=time(21,0))
                break_min=c2.number_input("Unpaid break (minutes)",min_value=0,max_value=1440,value=0)
                notes=st.text_input("Notes / reason")
                submitted=st.form_submit_button("Save attendance",type="primary")
            if submitted:
                save_record("attendance",{"id":"","staff_id":labels[label],"date":att_date.isoformat(),"status":status,"in_time":in_time.strftime("%H:%M"),"out_time":out_time.strftime("%H:%M"),"break_min":break_min,"notes":notes.strip()})
                st.success("Attendance saved. Existing record for this worker/date is replaced."); st.rerun()
        else: st.warning("Add a staff member before recording attendance.")
        st.subheader("Attendance log")
        month_filter=st.text_input("Month (YYYY-MM)",value=month,key="att_month")
        aview=attendance[attendance.date.astype(str).str.startswith(month_filter)] if len(attendance) else attendance
        if len(aview):
            name_map=staff_map(staff); out=aview.copy(); out["Worker"]=out.staff_id.map(name_map).fillna("Archived worker")
            out["Duration"]=out.apply(lambda r: f'{duration(r.in_time,r.out_time)//60}h {duration(r.in_time,r.out_time)%60}m',axis=1)
            st.dataframe(out[["Worker","date","status","in_time","out_time","break_min","Duration","notes"]].sort_values("date",ascending=False),use_container_width=True,hide_index=True)
            download_csv(out,"attendance.csv")
            del_id=st.selectbox("Delete attendance record",["— Select —"]+[f'{r["date"]} · {name_map.get(r["staff_id"],"Worker")} · {r["id"][:6]}' for _,r in aview.iterrows()])
            if st.button("Delete selected attendance"):
                if del_id!="— Select —":
                    rid=del_id.rsplit(" · ",1)[-1]; row=aview[aview.id.str.startswith(rid)]
                    if len(row): delete_record("attendance",row.iloc[0]["id"]); st.rerun()
        else: st.write("No attendance entries for this month.")
    with tabs[3]:
        st.subheader("Add salary advance, purchase, deduction or bonus")
        if len(staff):
            labels={f'{r["name"]} · {r["id"][:6]}':r["id"] for _,r in staff.iterrows()}
            with st.form("ledger_form",clear_on_submit=True):
                c1,c2=st.columns(2)
                label=c1.selectbox("Worker",list(labels),key="ledger_worker")
                ledger_date=c2.date_input("Date",value=now_date(),key="ledger_date")
                typ=c1.selectbox("Entry type",["advance","purchase","deduction","bonus"],format_func=lambda x:{"advance":"Salary advance","purchase":"Shop purchase","deduction":"Other deduction","bonus":"Bonus / extra pay"}[x])
                amount=c2.number_input("Amount (₹)",min_value=0.01,value=100.0,step=100.0)
                method=c1.selectbox("Method",["Cash","Bank transfer","UPI","Other"])
                ref=c2.text_input("Reference (optional)")
                notes=st.text_input("Notes (optional)")
                submitted=st.form_submit_button("Save ledger entry",type="primary")
            if submitted:
                save_record("ledger",{"id":"","staff_id":labels[label],"date":ledger_date.isoformat(),"type":typ,"amount":amount,"method":method,"ref":ref.strip(),"notes":notes.strip()})
                st.success("Ledger entry saved."); st.rerun()
        else: st.warning("Add a staff member first.")
        st.subheader("Ledger history")
        lm=st.text_input("Ledger month (YYYY-MM)",value=month,key="ledger_month")
        lview=ledger[ledger.date.astype(str).str.startswith(lm)] if len(ledger) else ledger
        if len(lview):
            names=staff_map(staff); out=lview.copy(); out["Worker"]=out.staff_id.map(names).fillna("Archived worker")
            out["Type"]=out.type.map({"advance":"Salary advance","purchase":"Shop purchase","deduction":"Other deduction","bonus":"Bonus / extra pay"}).fillna(out.type)
            st.dataframe(out[["Worker","date","Type","amount","method","ref","notes"]].sort_values("date",ascending=False),use_container_width=True,hide_index=True)
            download_csv(out,"staff-ledger.csv")
            choices=["— Select —"]+[f'{r["date"]} · {names.get(r["staff_id"],"Worker")} · {r["id"][:6]}' for _,r in lview.iterrows()]
            delete_choice=st.selectbox("Delete ledger entry",choices)
            if st.button("Delete selected ledger entry"):
                if delete_choice!="— Select —":
                    rid=delete_choice.rsplit(" · ",1)[-1]; row=lview[lview.id.str.startswith(rid)]
                    if len(row): delete_record("ledger",row.iloc[0]["id"]); st.rerun()
        else: st.write("No ledger entries for this month.")
    with tabs[4]:
        st.subheader("Payroll calculation")
        c1,c2=st.columns(2)
        payroll_month=c1.text_input("Payroll month (YYYY-MM)",value=month,key="payroll_month")
        filter_names={"All active staff":"all"}
        filter_names.update({r["name"]:r["id"] for _,r in active.iterrows()})
        filter_label=c2.selectbox("Staff filter",list(filter_names))
        rows=[payroll_for(r.to_dict(),payroll_month,staff,attendance,ledger,payments,rules) for _,r in active.iterrows() if filter_names[filter_label]=="all" or r["id"]==filter_names[filter_label]]
        if rows:
            pdf=pd.DataFrame(rows)
            st.dataframe(pdf[["Worker","Monthly salary","Bonus","Attendance deductions","Advances","Shop purchases","Other deductions","Net payable","Paid","Balance","Absent","Half-days","Late flags","Early flags"]],use_container_width=True,hide_index=True)
            download_csv(pdf,"payroll-"+payroll_month+".csv")
            st.caption("Net payable is floored at ₹0. Excess advances/deductions remain visible in the ledger. Overtime is not automatically added. Verify rules and calculations before paying.")
        else: st.write("No active staff members match this filter.")
        st.divider(); st.subheader("Record salary payment")
        if len(staff):
            labels={f'{r["name"]} · {r["id"][:6]}':r["id"] for _,r in staff.iterrows()}
            with st.form("payment_form",clear_on_submit=True):
                c1,c2=st.columns(2)
                label=c1.selectbox("Worker",list(labels),key="payment_worker")
                pay_month=c2.text_input("Payroll month (YYYY-MM)",value=payroll_month)
                pay_date=c1.date_input("Payment date",value=now_date())
                pay_amount=c2.number_input("Amount paid (₹)",min_value=0.01,value=1000.0,step=500.0)
                method=c1.selectbox("Payment method",["Cash","Bank transfer","UPI","Other"],key="payment_method")
                ref=c2.text_input("Payment reference (optional)")
                submitted=st.form_submit_button("Record payment",type="primary")
            if submitted:
                if len(pay_month)!=7 or pay_month[4:5]!="-": st.error("Enter month as YYYY-MM.")
                else:
                    save_record("payments",{"id":"","staff_id":labels[label],"date":pay_date.isoformat(),"month":pay_month,"amount":pay_amount,"method":method,"ref":ref.strip()})
                    st.success("Payment recorded."); st.rerun()
        if len(payments):
            names=staff_map(staff); out=payments.copy(); out["Worker"]=out.staff_id.map(names).fillna("Archived worker")
            st.subheader("Payment history")
            st.dataframe(out[["Worker","date","month","amount","method","ref"]].sort_values("date",ascending=False),use_container_width=True,hide_index=True)
            choice=st.selectbox("Delete payment record",["— Select —"]+[f'{r["date"]} · {names.get(r["staff_id"],"Worker")} · {r["id"][:6]}' for _,r in payments.iterrows()])
            if st.button("Delete selected payment"):
                if choice!="— Select —":
                    rid=choice.rsplit(" · ",1)[-1]; row=payments[payments.id.str.startswith(rid)]
                    if len(row): delete_record("payments",row.iloc[0]["id"]); st.rerun()
    with tabs[5]:
        st.subheader("Payroll rules")
        with st.form("rules_form"):
            c1,c2=st.columns(2)
            days=c1.number_input("Salary basis days",min_value=1,max_value=31,value=int(rules["days"]))
            absent=c2.selectbox("Absent deduction",["daily","none"],index=["daily","none"].index(rules["absent"]),format_func=lambda x:"Daily rate deduction" if x=="daily" else "No deduction")
            half=c1.selectbox("Half-day deduction",["half","full","none"],index=["half","full","none"].index(rules["half"]),format_func=lambda x:{"half":"Half daily rate","full":"Full daily rate","none":"No deduction"}[x])
            late=c2.selectbox("Late arrival deduction",["none","fixed"],index=["none","fixed"].index(rules["late"]),format_func=lambda x:"Flag only" if x=="none" else "Fixed amount per late day")
            late_amt=c1.number_input("Fixed late deduction (₹)",min_value=0.0,value=float(rules["lateAmt"]))
            early=c2.selectbox("Early departure deduction",["none","fixed"],index=["none","fixed"].index(rules["early"]),format_func=lambda x:"Flag only" if x=="none" else "Fixed amount per early departure")
            early_amt=c1.number_input("Fixed early deduction (₹)",min_value=0.0,value=float(rules["earlyAmt"]))
            leave=c2.selectbox("Approved leave",["paid","unpaid"],index=["paid","unpaid"].index(rules["leave"]),format_func=lambda x:"Paid" if x=="paid" else "Unpaid (daily deduction)")
            weekly=c1.selectbox("Weekly off",["paid","unpaid"],index=["paid","unpaid"].index(rules["weekly"]),format_func=lambda x:"Paid" if x=="paid" else "Unpaid (daily deduction)")
            purchases=c2.selectbox("Shop purchases",["yes","no"],index=["yes","no"].index(rules["purchases"]),format_func=lambda x:"Count as deduction" if x=="yes" else "Track only")
            submitted=st.form_submit_button("Save payroll rules",type="primary")
        if submitted:
            rules_save({"days":days,"absent":absent,"half":half,"late":late,"lateAmt":late_amt,"early":early,"earlyAmt":early_amt,"leave":leave,"weekly":weekly,"purchases":purchases})
            st.success("Payroll rules saved."); st.rerun()
        if st.button("Restore default payroll rules"):
            rules_save(DEFAULT_RULES.copy()); st.rerun()
        st.caption("Defaults use a 30-day salary basis. Confirm deductions with each worker and verify compliance with applicable wage laws.")
        st.divider(); st.subheader("Backup & restore")
        current_db={}
        for table in TABLES:
            current_db[table]=read_table(table).where(pd.notna(read_table(table)),None).to_dict(orient="records")
        current_db["rules"]=rules_get()
        payload=json.dumps(current_db,ensure_ascii=False,indent=2,default=str).encode("utf-8")
        st.download_button("Download full JSON backup",payload,file_name=f"staff-manager-backup-{today}.json",mime="application/json",type="primary")
        uploaded=st.file_uploader("Restore from a Staff Manager JSON backup",type=["json"])
        if uploaded and st.button("Replace all current data with uploaded backup",type="secondary"):
            try:
                backup=json.load(uploaded)
                if not all(isinstance(backup.get(t),list) for t in TABLES): raise ValueError("Missing required data tables")
                with conn() as c:
                    c.execute("DELETE FROM attendance"); c.execute("DELETE FROM ledger"); c.execute("DELETE FROM payments"); c.execute("DELETE FROM staff")
                    for table,cols in TABLES.items():
                        for item in backup[table]:
                            if not isinstance(item,dict): continue
                            values={k:item.get(k) for k in cols}
                            if not values.get("id"): values["id"]=str(uuid.uuid4())
                            names=",".join(cols); marks=",".join("?" for _ in cols)
                            c.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})",tuple(values[k] for k in cols))
                    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('rules',?)",(json.dumps({**DEFAULT_RULES,**backup.get("rules",{})}),))
                st.success("Backup restored."); st.rerun()
            except Exception as e: st.error(f"Could not restore this backup: {e}")
        st.warning("This app has no built-in sign-in or multi-user permissions. Do not publish real staff records to an unrestricted public URL. Streamlit-hosted filesystem persistence is not guaranteed; keep backups outside the app.")

if __name__ == "__main__":
    main()
