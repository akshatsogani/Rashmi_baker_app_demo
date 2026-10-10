# Staff Manager — Streamlit edition

A Streamlit conversion of the supplied Personal Staff Manager prototype. Includes staff profiles, attendance logs, advances/deductions/bonuses, payroll estimates, payment records, configurable rules, CSV exports, and JSON backup/restore.

## Run locally

Python 3.10+ recommended.

```bash
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate       # Windows
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Create a **private GitHub repository** and upload `app.py`, `requirements.txt`, and this README.
2. Open [Streamlit Community Cloud](https://share.streamlit.io/), connect GitHub, and create an app using `app.py` as the entry point.
3. Deploy, then open the generated app URL.

**Privacy warning:** this app currently has no login/access control. Anyone who can access a public app URL may be able to view or alter records. Do not deploy real staff information to an unrestricted public URL. Make the repository private, but note that a private GitHub repo does **not** make the deployed app private. Add authentication/authorization before using real staff data.

## Storage and backups

The app stores records in `staff_manager.db` (SQLite) on the host. This is suitable for local use and prototyping. Filesystem persistence on hosted app platforms may be temporary or reset on rebuild/redeploy, and multiple replicas do not share this database. Use **Rules & backup → Download full JSON backup** regularly. For reliable production use, move storage to a managed database and add authentication.

## Payroll caveats

Payroll is a configurable estimate, not legal or payroll advice. It does not automatically calculate overtime, and it floors net payable at ₹0. Verify payroll rules and deductions against worker agreements and applicable law before paying staff.
