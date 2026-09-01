# AGENTS.md

## Project overview

This repository is a Flask-based certificate generation app for bulk PDF/PNG certificate creation. The app supports:

- uploading a certificate template
- generating certificate images for participant lists
- bulk image processing for existing certificates
- optional login protection and admin password setup
- CSV/Excel driven generation workflows

Primary application files:

- `app.py` — Flask app and request handling
- `generate_certs.py` — a script for generating certificates from an Excel file and template image
- `process_certificates.py` — certificate-processing utilities for bulk operations
- `templates/` — HTML templates for the web UI
- `static/` — CSS and JS assets
- `output_certs/` — generated outputs
- `uploads/` — uploaded files

## Working rules

- Prefer the smallest, targeted edit that resolves the issue.
- Keep Flask routes and validation logic consistent with the existing patterns in `app.py`.
- Preserve security-sensitive behavior: CSRF checks, upload validation, login gating, and rate limiting.
- Avoid broad refactors or changing unrelated functionality.
- If a bug fix requires a new dependency, confirm it is necessary and keep the change minimal.
- Prefer existing project conventions over introducing new frameworks or architectures.

## Validation

Use lightweight verification after changes, for example:

```bash
python -m py_compile app.py generate_certs.py process_certificates.py
```

If behavior changes are user-facing, also run the app locally with:

```bash
python app.py
```

Or with Docker:

```bash
docker compose up
```

## Relevant commands

```bash
# install deps
pip install -r requirements.txt

# run the app
python app.py

# run via Docker
docker compose up
```

## Notes

- This project uses Pillow, Flask, pandas, and related image-processing libraries.
- Keep image and upload limits in mind when editing validation code.
- Generated outputs should be saved under the existing project directories rather than scattered outside the repo unless explicitly required.
