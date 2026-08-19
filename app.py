from flask import Flask, render_template, request, send_file, jsonify, Response, stream_with_context
from PIL import Image, ImageDraw, ImageFont
from werkzeug.utils import secure_filename
import io
import base64
import os
import csv
import zipfile
import smtplib
import ssl
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 128 * 1024 * 1024  # 128MB max
app.config['UPLOAD_FOLDER'] = 'uploads'

os.makedirs('uploads', exist_ok=True)

FONT_MAP = {
    'arial.ttf':   {'regular': '/usr/share/fonts/noto/NotoSans-Regular.ttf', 'bold': '/usr/share/fonts/noto/NotoSans-Bold.ttf'},
    'georgia.ttf': {'regular': '/usr/share/fonts/noto/NotoSerif-Regular.ttf', 'bold': '/usr/share/fonts/noto/NotoSerif-Bold.ttf'},
    'times.ttf':   {'regular': '/usr/share/fonts/noto/NotoSerif-Regular.ttf', 'bold': '/usr/share/fonts/noto/NotoSerif-Bold.ttf'},
}

IMAGE_FORMATS = {
    'image/png':  ('PNG',  'png'),
    'image/jpeg': ('JPEG', 'jpg'),
    'image/jpg':  ('JPEG', 'jpg'),
    'image/webp': ('WEBP', 'webp'),
    'image/bmp':  ('BMP',  'bmp'),
    'image/tiff': ('TIFF', 'tiff'),
    'image/gif':  ('GIF',  'gif'),
}

EXPORT_FORMAT_MAP = {
    'png':      ('PNG',  'png'),
    'jpg':      ('JPEG', 'jpg'),
    'webp':     ('WEBP', 'webp'),
    'pdf':      ('PDF',  'pdf'),
    'pdf_each': ('PDF',  'pdf'),
}

BULK_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg'}
BULK_IMAGE_FORMATS = {'PNG', 'JPEG', 'JPG'}
ZIP_MIMETYPES = {'application/zip', 'application/x-zip-compressed', 'multipart/x-zip'}

SMTP_PRESETS = {
    'gmail':   ('smtp.gmail.com',            587,  'starttls'),
    'outlook': ('smtp-mail.outlook.com',      587,  'starttls'),
    'yahoo':   ('smtp.mail.yahoo.com',        587,  'starttls'),
    'zoho':    ('smtp.zoho.com',              465,  'ssl'),
}

EMAIL_ATTACH_FORMATS = {
    'same':     None,          # use template native format
    'png':      ('PNG',  'png'),
    'jpg':      ('JPEG', 'jpg'),
    'pdf':      ('PDF',  'pdf'),
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def load_font(font_family, size, is_bold=False):
    style = 'bold' if is_bold else 'regular'
    font_entry = FONT_MAP.get(font_family)
    
    if isinstance(font_entry, dict):
        font_path = font_entry.get(style, font_entry.get('regular', font_family))
    else:
        font_path = font_entry if font_entry else font_family
        
    try:
        return ImageFont.truetype(font_path, size)
    except OSError:
        try:
            fallback = '/usr/share/fonts/noto/NotoSans-Bold.ttf' if is_bold else '/usr/share/fonts/noto/NotoSans-Regular.ttf'
            return ImageFont.truetype(fallback, size)
        except OSError:
            return ImageFont.load_default(size=size)


def decode_template(template_data):
    """Decode base64 data-URL → (PIL Image, pil_format, extension)."""
    header, raw = template_data.split(',', 1)
    mime = header.split(':')[1].split(';')[0].lower()
    pil_format, ext = IMAGE_FORMATS.get(mime, ('PNG', 'png'))
    image = Image.open(io.BytesIO(base64.b64decode(raw)))
    if pil_format == 'JPEG' and image.mode in ('RGBA', 'P'):
        image = image.convert('RGB')
    return image, pil_format, ext


def draw_certificate(template_image, name, department, settings):
    """Overlay name + department text on a copy of the template."""
    cert = template_image.copy()
    draw = ImageDraw.Draw(cert)

    name_bold = settings.get('nameBold') in (True, 'true', 'True', '1', 1)
    dept_bold = settings.get('deptBold') in (True, 'true', 'True', '1', 1)
    name_font = load_font(settings.get('nameFont', 'arial.ttf'), int(settings.get('nameFontSize', 38)), name_bold)
    dept_font = load_font(settings.get('deptFont', 'arial.ttf'), int(settings.get('deptFontSize', 32)), dept_bold)

    draw.text(
        (int(settings.get('nameX', 420)), int(settings.get('nameY', 270))),
        name, fill=hex_to_rgb(settings.get('nameColor', '#000000')), font=name_font,
    )
    draw.text(
        (int(settings.get('deptX', 76)), int(settings.get('deptY', 303))),
        department, fill=hex_to_rgb(settings.get('deptColor', '#000000')), font=dept_font,
    )
    return cert


def image_to_bytes(image, pil_format):
    buf = io.BytesIO()
    if pil_format in ('JPEG', 'PDF') and image.mode in ('RGBA', 'P'):
        image = image.convert('RGB')
    image.save(buf, pil_format)
    return buf.getvalue()


def extract_zip_attachments(b64_zip_data):
    """Decode a base64 zip data-URL and return sorted [(filename, bytes), ...] of its files."""
    if not b64_zip_data:
        return []
    if ',' in b64_zip_data:
        _, raw = b64_zip_data.split(',', 1)
    else:
        raw = b64_zip_data
    zip_bytes = base64.b64decode(raw)
    files = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            # skip junk entries like __MACOSX or .DS_Store
            base = os.path.basename(member.filename)
            if not base or base.startswith('.') or '__MACOSX' in member.filename:
                continue
            files.append((member.filename, zf.read(member)))
    files.sort(key=lambda x: x[0])
    return files


def safe_hex_color(value, fallback='#ffffff'):
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    if len(value) == 7 and value[0] == '#' and all(c in '0123456789abcdefABCDEF' for c in value[1:]):
        return value
    return fallback


def clamp_number(value, minimum, maximum, fallback=0):
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(maximum, number))


def encode_bulk_image(image, pil_format):
    buf = io.BytesIO()
    if pil_format == 'JPEG':
        if image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')
        image.save(buf, 'JPEG', quality=95, subsampling=0, optimize=True)
    else:
        image.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def decode_bulk_image_stream(stream, display_name):
    try:
        image = Image.open(stream)
        source_format = (image.format or '').upper()
        if source_format not in BULK_IMAGE_FORMATS:
            raise ValueError(f'{display_name} is not a supported PNG/JPG image.')
        image.load()
    except ValueError:
        raise
    except Exception:
        raise ValueError(f'{display_name} could not be opened as a PNG/JPG image.')

    pil_format = 'JPEG' if source_format in ('JPEG', 'JPG') else 'PNG'
    ext = 'jpg' if pil_format == 'JPEG' else 'png'
    return image, pil_format, ext


def is_zip_upload(upload):
    ext = os.path.splitext(upload.filename or '')[1].lower()
    return ext == '.zip' or (upload.mimetype or '').lower() in ZIP_MIMETYPES


def iter_bulk_upload_images(files):
    for upload in files:
        if not upload or not upload.filename:
            continue

        if is_zip_upload(upload):
            try:
                archive = zipfile.ZipFile(upload.stream)
            except zipfile.BadZipFile:
                raise ValueError(f'{upload.filename} is not a valid ZIP archive.')

            with archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    member_ext = os.path.splitext(member.filename)[1].lower()
                    if member_ext not in BULK_IMAGE_EXTENSIONS:
                        continue
                    with archive.open(member) as image_stream:
                        image, pil_format, ext = decode_bulk_image_stream(
                            image_stream,
                            f'{upload.filename}/{member.filename}',
                        )
                    yield member.filename, image, pil_format, ext
            continue

        upload_ext = os.path.splitext(upload.filename)[1].lower()
        if upload_ext not in BULK_IMAGE_EXTENSIONS:
            raise ValueError(f'{upload.filename} is not supported. Upload PNG, JPG, or ZIP files.')

        image, pil_format, ext = decode_bulk_image_stream(upload.stream, upload.filename)
        yield upload.filename, image, pil_format, ext


def unique_edited_filename(source_name, ext, used_names, index):
    basename = secure_filename(os.path.basename(source_name)) or f'certificate_{index}.{ext}'
    stem = os.path.splitext(basename)[0] or f'certificate_{index}'
    candidate = f'{stem}_edited.{ext}'
    suffix = 2

    while candidate in used_names:
        candidate = f'{stem}_edited_{suffix}.{ext}'
        suffix += 1

    used_names.add(candidate)
    return candidate


def apply_bulk_actions(image, raw_actions):
    """Apply extensible bulk editor actions to a PIL image."""
    if image.mode == 'P':
        image = image.convert('RGBA')

    width, height = image.size
    draw = ImageDraw.Draw(image)

    for action in raw_actions:
        if not isinstance(action, dict) or action.get('type') != 'cover_text':
            continue

        region = action.get('region') or {}
        x = clamp_number(region.get('x'), 0, max(width - 1, 0), 0)
        y = clamp_number(region.get('y'), 0, max(height - 1, 0), 0)
        w = clamp_number(region.get('width'), 1, width - x, 1)
        h = clamp_number(region.get('height'), 1, height - y, 1)

        cover = action.get('cover') or {}
        cover_color = safe_hex_color(cover.get('color'), '#ffffff')
        draw.rectangle([x, y, x + w, y + h], fill=hex_to_rgb(cover_color))

        text = action.get('text') or {}
        content = str(text.get('content') or '')[:1000]
        if not content:
            continue

        font_family = text.get('fontFamily') if text.get('fontFamily') in FONT_MAP else 'arial.ttf'
        font_size = clamp_number(text.get('fontSize'), 1, 400, 32)
        font = load_font(font_family, font_size)
        text_color = safe_hex_color(text.get('color'), '#000000')
        text_x = clamp_number(text.get('x'), 0, width, x + 8)
        text_y = clamp_number(text.get('y'), 0, height, y + 8)

        draw.multiline_text(
            (text_x, text_y),
            content,
            fill=hex_to_rgb(text_color),
            font=font,
            spacing=max(2, int(font_size * 0.2)),
        )

    return image


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/bulk-editor')
def bulk_editor():
    return render_template('bulk_editor.html')


@app.route('/parse-csv', methods=['POST'])
def parse_csv():
    """Parse CSV → [{name, department, email}, …]"""
    try:
        if 'csvFile' not in request.files or request.files['csvFile'].filename == '':
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400

        stream = io.StringIO(request.files['csvFile'].stream.read().decode('UTF8'), newline=None)
        rows = list(csv.reader(stream))

        # Auto-detect header row
        if rows and rows[0] and rows[0][0].strip().lower() in ('name', 'participant'):
            rows = rows[1:]

        participants = []
        for row in rows:
            if row and row[0].strip():
                participants.append({
                    'name':       row[0].strip(),
                    'department': row[1].strip() if len(row) > 1 else '',
                    'email':      row[2].strip() if len(row) > 2 else '',
                })

        return jsonify({'success': True, 'participants': participants, 'count': len(participants)})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/bulk-editor/process', methods=['POST'])
def process_bulk_editor_batch():
    """Apply visual edits to uploaded PNG/JPG certificates and return a ZIP."""
    try:
        files = request.files.getlist('certificates')
        if not files:
            return jsonify({'success': False, 'error': 'Upload at least one certificate image.'}), 400

        try:
            edits_payload = json.loads(request.form.get('edits', '{}'))
        except json.JSONDecodeError:
            return jsonify({'success': False, 'error': 'Invalid edit instructions.'}), 400

        actions = edits_payload.get('actions', [])
        if not isinstance(actions, list) or not actions:
            return jsonify({'success': False, 'error': 'Draw at least one edit region before processing.'}), 400

        buf = io.BytesIO()
        processed_count = 0
        used_names = set()

        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for index, (source_name, image, pil_format, ext) in enumerate(iter_bulk_upload_images(files), start=1):
                processed = apply_bulk_actions(image.copy(), actions)
                output_name = unique_edited_filename(source_name, ext, used_names, index)
                zf.writestr(output_name, encode_bulk_image(processed, pil_format))
                processed_count += 1

        if processed_count == 0:
            return jsonify({'success': False, 'error': 'No PNG/JPG certificate images were found.'}), 400

        buf.seek(0)
        return send_file(
            buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name='bulk_edited_certificates.zip',
        )

    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/generate', methods=['POST'])
def generate_certificate():
    """Generate a single certificate (for preview). Returns base64 image."""
    try:
        data = request.json
        template_image, pil_format, ext = decode_template(data['template'])
        cert = draw_certificate(template_image, data.get('name', ''), data.get('department', ''), data)
        img_bytes = image_to_bytes(cert, pil_format)
        mime = 'image/jpeg' if ext == 'jpg' else f'image/{ext}'
        return jsonify({
            'success': True,
            'image': f'data:{mime};base64,{base64.b64encode(img_bytes).decode()}',
            'ext': ext,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/generate-batch', methods=['POST'])
def generate_batch():
    """Batch generate → merged PDF, individual PDFs ZIP, or image ZIP depending on exportFormat."""
    try:
        data = request.json
        template_image, default_fmt, default_ext = decode_template(data['template'])
        participants = data.get('participants', [])
        settings = data.get('settings', {})

        export_fmt = settings.get('exportFormat', 'same')
        pil_format, ext = EXPORT_FORMAT_MAP.get(export_fmt, (default_fmt, default_ext))

        certs = []
        for p in participants:
            c = draw_certificate(template_image, p['name'], p.get('department', ''), settings)
            if pil_format in ('JPEG', 'PDF') and c.mode in ('RGBA', 'P'):
                c = c.convert('RGB')
            certs.append(c)

        buf = io.BytesIO()

        # ── Merged single PDF ─────────────────────────────────────────────────
        if export_fmt == 'pdf':
            if certs:
                certs[0].save(buf, 'PDF', save_all=True, append_images=certs[1:])
            buf.seek(0)
            return send_file(
                buf,
                mimetype='application/pdf',
                as_attachment=True,
                download_name='certificates.pdf',
            )

        # ── Individual PDFs in ZIP ────────────────────────────────────────────
        if export_fmt == 'pdf_each':
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p, c in zip(participants, certs):
                    pdf_buf = io.BytesIO()
                    c.save(pdf_buf, 'PDF')
                    filename = f"{p['name'].replace(' ', '_')}_certificate.pdf"
                    zf.writestr(filename, pdf_buf.getvalue())
            buf.seek(0)
            return send_file(
                buf,
                mimetype='application/zip',
                as_attachment=True,
                download_name='certificates_pdf.zip',
            )

        # ── Image formats (PNG / JPG / WebP / same-as-template) in ZIP ───────
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for p, c in zip(participants, certs):
                zf.writestr(
                    f"{p['name'].replace(' ', '_')}_certificate.{ext}",
                    image_to_bytes(c, pil_format),
                )
        buf.seek(0)
        return send_file(buf, mimetype='application/zip', as_attachment=True, download_name='certificates.zip')

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


def _make_smtp_connection(smtp_host, smtp_port, smtp_mode, smtp_user, smtp_pass):
    """Open, authenticate, and return an SMTP connection. Raises on any failure."""
    ctx = ssl.create_default_context()
    if smtp_mode == 'ssl':
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=120, context=ctx)
        server.ehlo()
    else:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=120)
        server.ehlo()
        try:
            server.starttls(context=ctx)
            server.ehlo()   # required after STARTTLS upgrade
        except smtplib.SMTPNotSupportedError:
            pass            # server doesn't support STARTTLS — proceed plain
    server.login(smtp_user, smtp_pass)
    return server


@app.route('/test-smtp', methods=['POST'])
def test_smtp():
    """Quick SMTP credential check — does not send any email."""
    try:
        data = request.json or {}
        provider = data.get('smtpProvider', 'custom')
        if provider in SMTP_PRESETS:
            smtp_host, smtp_port, smtp_mode = SMTP_PRESETS[provider]
        else:
            smtp_host = data.get('smtpHost', '').strip()
            smtp_port = int(data.get('smtpPort', 587) or 587)
            smtp_mode = 'ssl' if smtp_port == 465 else 'starttls'

        smtp_user = (data.get('smtpUser') or '').strip()
        smtp_pass = (data.get('smtpPass') or '').strip()

        if not smtp_host:
            return jsonify({'success': False, 'error': 'SMTP host is required for Custom SMTP.'}), 400
        if not smtp_user or not smtp_pass:
            return jsonify({'success': False, 'error': 'Email and password are required.'}), 400

        server = _make_smtp_connection(smtp_host, smtp_port, smtp_mode, smtp_user, smtp_pass)
        server.quit()
        return jsonify({'success': True, 'message': f'Connected to {smtp_host}:{smtp_port} successfully!'})
    except smtplib.SMTPAuthenticationError:
        return jsonify({'success': False, 'error': 'Authentication failed. Check your email/password. For Gmail use an App Password.'}), 400
    except smtplib.SMTPConnectError as e:
        return jsonify({'success': False, 'error': f'Could not connect to {smtp_host}:{smtp_port}. {e}'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/send-certificates', methods=['POST'])
def send_certificates():
    """Generate + email each certificate. Streams SSE progress events."""
    data = request.json or {}

    has_template = bool(data.get('template'))
    template_image, pil_format, ext = None, 'PNG', 'png'
    if has_template:
        try:
            template_image, pil_format, ext = decode_template(data['template'])
        except Exception as e:
            def _err():
                yield f"data: {json.dumps({'type': 'error', 'message': f'Template decode failed: {e}'})}\n\n"
            return Response(stream_with_context(_err()), mimetype='text/event-stream',
                            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    participants = data.get('participants', [])
    settings     = data.get('settings', {})

    # Optional extra attachment ZIP — one file per participant, matched by sheet order
    attachments_zip_data = data.get('attachmentsZip')
    try:
        extra_attachments = extract_zip_attachments(attachments_zip_data)
    except Exception as e:
        def _err():
            yield f"data: {json.dumps({'type': 'error', 'message': f'Attachment ZIP could not be read: {e}'})}\n\n"
        return Response(stream_with_context(_err()), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    if extra_attachments and len(extra_attachments) != len(participants):
        def _err():
            yield f"data: {json.dumps({'type': 'error', 'message': f'Attachment count mismatch: ZIP has {len(extra_attachments)} files but sheet has {len(participants)} rows. Fix this before sending.'})}\n\n"
        return Response(stream_with_context(_err()), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # SMTP config
    provider = data.get('smtpProvider', 'custom')
    if provider in SMTP_PRESETS:
        smtp_host, smtp_port, smtp_mode = SMTP_PRESETS[provider]
    else:
        smtp_host = (data.get('smtpHost') or '').strip()
        smtp_port = int(data.get('smtpPort') or 587)
        smtp_mode = 'ssl' if smtp_port == 465 else 'starttls'

    smtp_user = (data.get('smtpUser') or '').strip()
    smtp_pass = (data.get('smtpPass') or '').strip()
    from_name = (data.get('fromName') or 'CertFlow').strip()
    subject   = data.get('emailSubject') or 'Your Certificate'
    body      = data.get('emailBody') or 'Hi {name},\n\nPlease find your certificate attached.\n\nRegards,\nCertFlow'

    # Determine attachment format for email
    email_fmt_key = data.get('emailAttachFormat', 'same')
    if email_fmt_key == 'same' or email_fmt_key not in EMAIL_ATTACH_FORMATS:
        out_fmt, out_ext = pil_format, ext
    elif EMAIL_ATTACH_FORMATS[email_fmt_key] is None:
        out_fmt, out_ext = pil_format, ext
    else:
        out_fmt, out_ext = EMAIL_ATTACH_FORMATS[email_fmt_key]

    def stream():
        results = []
        skipped = 0
        server  = None

        try:
            # ── Initial SSE buffer flush for Gunicorn / reverse proxies ───────────
            yield ": " + (" " * 1024) + "\n\n"

            # ── Connect ──────────────────────────────────────────────────────────
            try:
                if not smtp_host:
                    raise ValueError('SMTP host is empty. Select a provider or fill in Custom SMTP host.')
                if not smtp_user or not smtp_pass:
                    raise ValueError('Email address and password/app-password are required.')
                server = _make_smtp_connection(smtp_host, smtp_port, smtp_mode, smtp_user, smtp_pass)
                yield f"data: {json.dumps({'type': 'status', 'message': 'Connected to mail server. Preparing emails...'})}\n\n"
            except smtplib.SMTPAuthenticationError:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Authentication failed. For Gmail, use an App Password (not your regular password).'})}\n\n"
                return
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': f'SMTP connection failed: {e}'})}\n\n"
                return

            total = len([p for p in participants if p.get('email')])

            for i, p in enumerate(participants):
                email_addr = (p.get('email') or '').strip()
                p_name = p.get('name', '')
                p_dept = p.get('department', '')
                if not email_addr:
                    skipped += 1
                    yield f"data: {json.dumps({'type': 'skip', 'name': p_name, 'reason': 'no email'})}\n\n"
                    continue

                try:
                    # Live status update before sending payload
                    progress_msg = f"Sending {i + 1}/{total}: {p_name} ({email_addr})..."
                    yield f"data: {json.dumps({'type': 'progress', 'name': p_name, 'email': email_addr, 'index': i + 1, 'total': total, 'message': progress_msg})}\n\n"

                    personal_body = body.replace('{name}', p_name).replace('{department}', p_dept)

                    msg = MIMEMultipart()
                    msg['From']    = f'{from_name} <{smtp_user}>'
                    msg['To']      = email_addr
                    msg['Subject'] = subject.replace('{name}', p_name).replace('{department}', p_dept)
                    msg.attach(MIMEText(personal_body, 'plain', 'utf-8'))

                    # Generate and attach certificate if template was provided
                    if has_template and template_image:
                        cert = draw_certificate(template_image, p_name, p_dept, settings)

                        # Convert to chosen attachment format
                        if out_fmt == 'PDF':
                            if cert.mode in ('RGBA', 'P'):
                                cert = cert.convert('RGB')
                            pdf_buf = io.BytesIO()
                            cert.save(pdf_buf, 'PDF')
                            cert_bytes = pdf_buf.getvalue()
                        else:
                            cert_bytes = image_to_bytes(cert, out_fmt)

                        attach_name = f"{p_name.replace(' ', '_')}_certificate.{out_ext}"
                        attachment = MIMEApplication(cert_bytes, Name=attach_name)
                        attachment['Content-Disposition'] = f'attachment; filename="{attach_name}"'
                        msg.attach(attachment)

                    # Attach the extra file matched by row position
                    if extra_attachments:
                        extra_name, extra_bytes = extra_attachments[i]
                        extra_display_name = os.path.basename(extra_name)
                        extra_part = MIMEApplication(extra_bytes, Name=extra_display_name)
                        extra_part['Content-Disposition'] = f'attachment; filename="{extra_display_name}"'
                        msg.attach(extra_part)

                    # Attempt send; reconnect once on broken pipe or timeout
                    try:
                        server.sendmail(smtp_user, email_addr, msg.as_string())
                    except (smtplib.SMTPServerDisconnected, TimeoutError, OSError, smtplib.SMTPException):
                        try:
                            server = _make_smtp_connection(smtp_host, smtp_port, smtp_mode, smtp_user, smtp_pass)
                            server.sendmail(smtp_user, email_addr, msg.as_string())
                        except Exception as retry_err:
                            raise retry_err

                    results.append({'name': p_name, 'status': 'sent'})
                    yield f"data: {json.dumps({'type': 'sent', 'name': p_name, 'email': email_addr, 'index': i + 1, 'total': total})}\n\n"

                except Exception as e:
                    results.append({'name': p_name, 'status': 'failed', 'reason': str(e)})
                    yield f"data: {json.dumps({'type': 'failed', 'name': p_name, 'email': email_addr, 'reason': str(e)})}\n\n"

            sent   = sum(1 for r in results if r['status'] == 'sent')
            failed = sum(1 for r in results if r['status'] == 'failed')
            yield f"data: {json.dumps({'type': 'done', 'sent': sent, 'failed': failed, 'skipped': skipped})}\n\n"

        except Exception as top_err:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Server error: {top_err}'})}\n\n"
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    pass

    return Response(stream_with_context(stream()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
