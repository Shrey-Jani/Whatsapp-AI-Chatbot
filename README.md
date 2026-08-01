# Tax Assistant Chatbot

An AI chatbot that intakes personal-tax clients for a firm. It runs a guided
conversation (web widget **and** WhatsApp), reads uploaded tax slips with OCR,
quotes fees, and hands a clean summary + PDF to staff via an admin dashboard.

- **Multilingual** — understands and replies in the user's language/script,
  including romanized Hinglish/Punglish; greets first, then asks.
- **Slip OCR** — upload a T4/T5/etc. (PDF/JPG/PNG); the bot identifies the slip
  and pulls key fields.
- **Existing clients** — matched by SIN, prior details prefilled.
- **SIN encrypted at rest** (Fernet); shown back once at review so a typo is caught.
- **Deterministic flow** — the question order is code, not the LLM. The LLM only
  parses free-text answers and translates.

## Stack

FastAPI · async SQLAlchemy + asyncpg · Supabase Postgres · Groq or Gemini (LLM) ·
Gemini vision (slip OCR) · reportlab (summary PDF) · local-disk file storage.

## Setup

```bash
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then fill in the values below
uvicorn app.main:app --reload
```

- Chat widget: <http://localhost:8000/>
- Admin dashboard: <http://localhost:8000/admin>

## Environment (`.env`)

| Var | Required | Notes |
|---|---|---|
| `DATABASE_URL` | ✅ | Supabase **pooler** URL, `postgresql+asyncpg://…pooler.supabase.com:6543/postgres` |
| `LLM_PROVIDER` | ✅ | `groq` (free, no card) or `gemini` |
| `GROQ_API_KEY` | if groq | from console.groq.com |
| `GEMINI_API_KEY` | if gemini / OCR | Gemini vision is used for slip OCR either way |
| `ADMIN_PASSWORD` | ✅ | gate for `/admin` |
| `SIN_ENCRYPTION_KEY` | ✅ | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — back it up; losing it loses the SINs |
| `STORAGE_DIR` | — | where uploaded slips are written (default `uploaded_files`; use a persistent path in prod) |
| `TAX_YEAR` | — | active filing year (default 2025) |
| `VERIFY_TOKEN`, `APP_SECRET` | WhatsApp only | Meta Cloud API webhook |

## Tests

```bash
pytest -q
```

## Deploy (Railway)

The included `Procfile` is all Railway needs.

1. Push to GitHub → railway.app → **New Project → Deploy from GitHub repo**.
2. **Variables** tab: add the env vars above (use the Supabase pooler URL).
3. **Settings → Volumes**: mount a volume at `/data`, set `STORAGE_DIR=/data`
   so uploaded files survive restarts.
4. **Settings → Networking → Generate Domain** for the public URL.

> Note: hosts with an ephemeral disk (no volume) wipe uploaded files on restart —
> client data in Postgres is always safe; only the slip files need the volume.

## Layout

```
app/
  main.py            app + routes wiring, global error handler
  chat_engine.py     flow control: next question, validate, advance, review
  question_flow.py   the questions, as data
  documents.py       upload → OCR/parse → Document row
  ocr.py             Gemini vision slip reader
  storage.py         local-disk file storage
  admin_routes.py    dashboard API (X-Admin-Key gated)
  whatsapp_routes.py Meta Cloud API webhook
  security.py        SIN encrypt/decrypt (Fernet)
  i18n.py            language detect + localize
  templates/         chat.html, admin.html
```
