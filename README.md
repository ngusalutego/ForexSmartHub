# Forex Smart Hub

Flask + **Neon (PostgreSQL)** + **Cloudflare R2**.

## Variables za Railway (zinazotumika)
`DATABASE_URL`, `R2_ENDPOINT`, `R2_BUCKET_NAME`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (tayari unazo) +
**`SECRET_KEY`** (neno refu lolote) na **`ADMIN_PASSWORD`** (zinahitajika). `R2_PUBLIC_URL` ni hiari.
Bila `R2_PUBLIC_URL`, video zinachezwa kwa link za muda zilizosainiwa; ukiiweka zitakuwa za kudumu na haraka zaidi.

## 1. Neon
1. Neon dashboard → Project → **Connection string** (chagua *Pooled* ikipatikana).
2. Weka kwenye `DATABASE_URL` (lazima iwe na `?sslmode=require`).
Tables zinaundwa zenyewe app ikianza.

## 2. Cloudflare R2
1. Unda bucket (mf. `forex-smart-hub`).
2. Bucket → Settings → **Public access**: washa `r2.dev` URL (au unganisha domain yako). Nakili URL → `R2_PUBLIC_URL`.
3. R2 → **Manage API Tokens** → Create token (Object Read & Write) → nakili `Access Key ID` na `Secret Access Key`. `Account ID` iko juu kulia kwenye R2 overview.
4. Bucket → Settings → **CORS Policy** (inahitajika ili video zipakiwe moja kwa moja kutoka kwenye browser):
```json
[
  {
    "AllowedOrigins": ["https://JINA-LA-SITE-YAKO.com", "http://localhost:5000"],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["Content-Type"],
    "MaxAgeSeconds": 3600
  }
]
```

## 3. Endesha
```bash
cp .env.example .env      # jaza thamani zako
pip install -r requirements.txt
FLASK_DEBUG=1 python app.py          # local  (http://localhost:5000)
```
Production (Render/Railway/VPS): Build `pip install -r requirements.txt`, Start `gunicorn app:app --workers 2 --threads 4 --timeout 120` (kuna `Procfile`). Weka variables zote za `.env.example` kwenye dashboard ya host.

## 4. Admin
Admin ni `ADMIN_EMAIL`. Akaunti ikikosekana huundwa kwa `ADMIN_PASSWORD` (au nenosiri la muda linaonekana kwenye logs). **Badilisha nenosiri kupitia Settings.**

## 5. Hamisha data za zamani (hiari)
```bash
python tools/migrate_sqlite.py forex.db static/uploads
```

## Vipengele
Login/Signup • Video upload (R2, progress bar) • YouTube • Watch page + views • Likes • Comments •
Edit/Delete video • Profile + picha (R2) • Subscribe + Following feed • Search + pagination •
Guardian panel (block, warning, delete) • Onyo linaonekana kwa mtumiaji • CSRF + rate-limit ya login.
