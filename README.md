# certflow 📜

> Bulk certificate generator. Upload a template, add names, download a ZIP. Built for college events.

Made this because generating 200 individual certificates by hand was not happening. Used it for AKIRA CTF at Velammal Engineering College and CYVENTURA at the CSE Cyber Security dept. It works.

## How it works

1. Upload your certificate PNG template
2. Paste participant names + departments (one per line)
3. Adjust text position, size, font, and color
4. Preview → generate → download all as ZIP

## Bulk Certificate Editor

Open `/bulk-editor` to apply the same visual edit to existing PNG/JPG certificates:

1. Upload one sample certificate image
2. Draw one or more rectangular regions on the sample
3. Choose a cover color and optional replacement text for each region
4. Upload the certificate image batch as loose PNG/JPG files or a ZIP archive
5. Download the processed certificates as a ZIP

## Run

```bash
# Local
pip install -r requirements.txt
python app.py

# Docker
docker compose up
```

Open `http://localhost:5000`.

## API

```
POST /generate        → single certificate
POST /generate-batch  → bulk generation, returns ZIP
POST /bulk-editor/process → bulk image edits, returns ZIP
```

## Stack

- Python / Flask
- Pillow
- Docker

### Dynamic text and resumable email sending

Upload a CSV (or paste CSV data) in Step 2. Step 3 creates a text field for each
non-email column. Choose a column, adjust its position/font/color, and use **Add
text field** or **Remove** to control what appears on the certificate. Use the
header-row selector for custom column names or headerless files. Name the email
column `Email`, `Email ID`, or `Email Address`; without a header, put email
addresses in the last column. Email templates accept `{string1}`, `{string2}`, …
for every text column; `{name}` and `{department}` remain aliases for the first two.

The main email sender supports **Pause** and **Resume**. Pause takes effect after
the current email finishes. Temporary SMTP/network failures retry after 15 seconds,
using SQLite checkpoints to skip completed rows and retain ZIP attachment order.
Keep the page open: its current batch payload and SMTP credentials stay in browser
memory, so refreshing/closing the page loses the ability to resume that batch from
the UI. Authentication errors require correcting the password and clicking Resume.
Permanent recipient errors are logged as failed and sending continues.

The throttled mailer also supports **Pause** and **Resume**, preserving its existing
timing limits. Connection failures leave the recipient pending; the next scheduled
cron trigger (or **Trigger Next Send**) retries after a minimum 60-second cooldown.
Concurrent cron/browser triggers are serialized. As with any SMTP retry, a lost
acknowledgement after server acceptance can cause a duplicate delivery for that row.

Regression checks (no real emails are sent):

```bash
.venv/bin/python -m unittest discover -s tests -v
```
