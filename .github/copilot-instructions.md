# Copilot instructions

## Repository context

This repo contains a Flask-based certificate generation tool for event organizers. It can generate bulk certificate files from participant data and supports template uploads and bulk image editing.

## Key files

- `app.py` — main Flask application, routes, validation, and generation logic
- `generate_certs.py` — standalone certificate generation script
- `process_certificates.py` — bulk processing logic for certificate tasks
- `templates/` — UI templates
- `static/` — frontend assets

## Expectations for changes

- Keep changes minimal and focused.
- Preserve existing validation and auth patterns.
- Do not remove upload safety checks, CSRF protections, or admin login protections unless the task explicitly requires it.
- Favor compatibility with the current Flask/Pillow stack.
- Default to using project-local conventions and file layout.

## Validation

Run this before finishing substantial edits:

```bash
python -m py_compile app.py generate_certs.py process_certificates.py
```

If a local smoke test is needed, run:

```bash
python app.py
```

## Notes

- This is a certificate-generation project, not a general web app; keep functionality aligned with that purpose.
- Be careful with file handling and image processing because incorrect resizing or output naming can break certificate generation.
