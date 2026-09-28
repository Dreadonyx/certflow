from flask import Flask, render_template, request, send_file, jsonify, Response, stream_with_context, session, redirect, url_for, abort, g
from werkzeug.middleware.proxy_fix import ProxyFix
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
import re
import secrets
import hashlib
import random
from contextlib import closing
import subprocess
import logging
import time
import tempfile
import shutil
import sqlite3
import urllib.request
import urllib.error
from functools import wraps
from email.utils import parseaddr
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

Image.MAX_IMAGE_PIXELS = int(os.environ.get('MAX_IMAGE_PIXELS', '25000000'))

app = Flask(__name__)
# Trust exactly one proxy hop (Caddy) for X-Forwarded-For / X-Forwarded-Proto.
# x_for=1 means only the outermost address in the header is used, preventing
# spoofed IPs from bypassing rate limits.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY', 'dev-only-change-me'),
    MAX_CONTENT_LENGTH=int(os.environ.get('MAX_REQUEST_BYTES', 128 * 1024 * 1024)),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax'),
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', 'true' if os.environ.get('FLASK_ENV') == 'production' else 'false').lower() in ('1', 'true', 'yes'),
    PERMANENT_SESSION_LIFETIME=int(os.environ.get('SESSION_LIFETIME_SECONDS', 8 * 3600)),
)
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', os.path.join(tempfile.gettempdir(), 'certflow-uploads'))
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ── SQLite user database ───────────────────────────────────────────────────────
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'certflow_users.db'))

def get_db():
    """Return a thread-local SQLite connection."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_db(exc):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Create the users table if it doesn't exist."""
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                pw_hash  TEXT    NOT NULL,
                created  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""CREATE TABLE IF NOT EXISTS send_jobs (
            id TEXT PRIMARY KEY, owner TEXT NOT NULL, fingerprint TEXT NOT NULL,
            completed TEXT NOT NULL DEFAULT '{}', paused INTEGER NOT NULL DEFAULT 0,
            lease REAL NOT NULL DEFAULT 0
        )""")
        conn.commit()

init_db()

# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO'), format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger('certflow')
password_hasher = PasswordHasher()
limiter = Limiter(key_func=get_remote_address, app=app, default_limits=[os.environ.get('GLOBAL_RATE_LIMIT', '300 per hour')], storage_uri=os.environ.get('RATELIMIT_STORAGE_URI', 'memory://'))

MAX_CSV_ROWS = int(os.environ.get('MAX_CSV_ROWS', '5000'))
MAX_PARTICIPANTS = int(os.environ.get('MAX_PARTICIPANTS', '5000'))
MAX_IMAGE_BYTES = int(os.environ.get('MAX_IMAGE_BYTES', str(32 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.environ.get('MAX_IMAGE_PIXELS', '25000000'))
MAX_ZIP_ENTRIES = int(os.environ.get('MAX_ZIP_ENTRIES', '500'))
MAX_ZIP_UNCOMPRESSED = int(os.environ.get('MAX_ZIP_UNCOMPRESSED', str(256 * 1024 * 1024)))
MAX_NAME_LENGTH = int(os.environ.get('MAX_NAME_LENGTH', '200'))
MAX_DEPARTMENT_LENGTH = int(os.environ.get('MAX_DEPARTMENT_LENGTH', '200'))
EMAIL_RE = re.compile(r'^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$')
USERNAME_RE = re.compile(r'^[A-Za-z0-9._-]{3,40}$')

if os.environ.get('FLASK_ENV') == 'production' and app.config['SECRET_KEY'] == 'dev-only-change-me':
    raise RuntimeError('SECRET_KEY must be set in production')


def db_get_user(username):
    """Fetch a user row by username (case-insensitive)."""
    row = get_db().execute(
        'SELECT * FROM users WHERE username = ? COLLATE NOCASE', (username,)
    ).fetchone()
    return row


def db_create_user(username, password):
    """Hash password and insert a new user. Returns True on success, False if username taken."""
    pw_hash = password_hasher.hash(password)
    try:
        get_db().execute(
            'INSERT INTO users (username, pw_hash) VALUES (?, ?)', (username, pw_hash)
        )
        get_db().commit()
        return True
    except sqlite3.IntegrityError:
        return False


def verify_user_password(username, password):
    """Check password against DB user, then fall back to env-var admin."""
    password_value = str(password or '')

    # 1. Check SQLite users table
    row = db_get_user(username)
    if row:
        try:
            password_hasher.verify(row['pw_hash'], password_value)
            return True
        except (VerifyMismatchError, VerificationError, ValueError):
            return False

    # 2. Fallback: env-var admin (keeps backward compat)
    admin_username = os.environ.get('ADMIN_USERNAME', 'admin')
    if not secrets.compare_digest(username, admin_username):
        return False
    configured_hash = os.environ.get('ADMIN_PASSWORD_HASH')
    if configured_hash:
        try:
            password_hasher.verify(configured_hash, password_value)
            return True
        except (VerifyMismatchError, VerificationError, ValueError):
            return False
    configured_password = os.environ.get('ADMIN_PASSWORD')
    if configured_password:
        return secrets.compare_digest(str(configured_password), password_value)

    return False

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
    'application/pdf': ('PDF', 'pdf'),
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


def csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


def require_csrf():
    supplied = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
    expected = session.get('_csrf_token')
    if not expected or not supplied or not secrets.compare_digest(str(supplied), str(expected)):
        abort(403, description='Invalid CSRF token')


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('authenticated'):
            if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            return redirect(url_for('login', next=request.path))
        session.permanent = True
        return view(*args, **kwargs)
    return wrapped


def validate_email(value):
    value = (value or '').strip()
    if not value or len(value) > 254 or not EMAIL_RE.fullmatch(value):
        return None
    local, domain = value.rsplit('@', 1)
    if len(local) > 64 or '..' in value:
        return None
    return value


def safe_filename(value, fallback='certificate'):
    value = re.sub(r'[^A-Za-z0-9._-]+', '_', str(value or ''))[:100].strip('._')
    return value or fallback


def validate_participants(participants):
    if not isinstance(participants, list) or len(participants) > MAX_PARTICIPANTS:
        raise ValueError(f'At most {MAX_PARTICIPANTS} participants are allowed per request.')
    cleaned = []
    errors = []
    for index, p in enumerate(participants, 1):
        if not isinstance(p, dict):
            errors.append(f'Row {index}: invalid participant')
            continue
        name = str(p.get('string1') or p.get('name') or '').strip()
        department = str(p.get('string2') or p.get('department') or '').strip()
        str3 = str(p.get('string3') or '').strip()
        values = p.get('values', [name, department, str3])
        if not isinstance(values, list) or any(not isinstance(v, str) or len(v) > MAX_DEPARTMENT_LENGTH for v in values):
            errors.append(f'Row {index}: invalid or excessively long column values')
            continue
        email = str(p.get('email') or '').strip()
        if not name:
            errors.append(f'Row {index}: name is required')
            continue
        if len(name) > MAX_NAME_LENGTH or len(department) > MAX_DEPARTMENT_LENGTH or len(str3) > MAX_DEPARTMENT_LENGTH:
            errors.append(f'Row {index}: text is too long')
            continue
        if email and not validate_email(email):
            errors.append(f'Row {index}: invalid email address')
        cleaned.append({
            'values': values,
            'string1': name,
            'string2': department,
            'string3': str3,
            'name': name,
            'department': department,
            'email': validate_email(email) or '',
        })
    if errors:
        raise ValueError('; '.join(errors[:10]))
    return cleaned


def validate_settings(settings, width=None, height=None):
    if not isinstance(settings, dict):
        return {}
    allowed_fonts = set(FONT_MAP)
    out = dict(settings)
    for key in ('nameX', 'nameY', 'deptX', 'deptY', 'str3X', 'str3Y'):
        limit = max(width or 10000, height or 10000)
        try: out[key] = max(-limit, min(limit, int(float(settings.get(key, 0)))) )
        except (TypeError, ValueError): out[key] = 0
    for key in ('nameFontSize', 'deptFontSize', 'str3FontSize'):
        try: out[key] = max(1, min(400, int(float(settings.get(key, 32)))))
        except (TypeError, ValueError): out[key] = 32
    for key in ('nameFont', 'deptFont', 'str3Font'):
        if out.get(key) not in allowed_fonts: out[key] = 'arial.ttf'
    for key in ('nameColor', 'deptColor', 'str3Color'):
        if not isinstance(out.get(key), str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', out[key]): out[key] = '#000000'
    if 'fields' in settings:
        if not isinstance(settings['fields'], list):
            raise ValueError('Text fields must be a list.')
        out['fields'] = []
        for field in settings['fields']:
            if not isinstance(field, dict):
                raise ValueError('Invalid text field.')
            column = field.get('column')
            if not isinstance(column, int) or isinstance(column, bool) or column < 0:
                raise ValueError('Choose a valid CSV column.')
            normalized = validate_settings({f'name{k}': field.get(k) for k in ('X', 'Y', 'FontSize', 'Font', 'Color', 'Bold')}, width, height)
            out['fields'].append({'column': column, **{k: normalized.get(f'name{k}') for k in ('X', 'Y', 'FontSize', 'Font', 'Color', 'Bold')}})
    return out


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


def render_pdf_first_page(pdf_bytes):
    """Rasterize the first page of a PDF to PNG bytes using poppler's pdftoppm."""
    proc = subprocess.run(
        ['pdftoppm', '-f', '1', '-l', '1', '-png', '-singlefile', '-'],
        input=pdf_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise ValueError('Failed to render PDF template.')
    return proc.stdout


def decode_template(template_data):
    """Decode base64 data-URL → (PIL Image, pil_format, extension)."""
    if not isinstance(template_data, str) or len(template_data) > MAX_IMAGE_BYTES * 2:
        raise ValueError('Template is missing or exceeds the upload limit.')
    if ',' not in template_data:
        raise ValueError('Invalid template data.')
    header, raw = template_data.split(',', 1)
    mime = header.split(':')[1].split(';')[0].lower()
    if mime not in IMAGE_FORMATS:
        raise ValueError('Unsupported template image format.')
    try:
        decoded = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError('Invalid template encoding.') from exc
    if len(decoded) > MAX_IMAGE_BYTES:
        raise ValueError('Template exceeds the upload limit.')
    pil_format, ext = IMAGE_FORMATS.get(mime, ('PNG', 'png'))
    try:
        if pil_format == 'PDF':
            decoded = render_pdf_first_page(decoded)
            pil_format, ext = 'PNG', 'png'
        image = Image.open(io.BytesIO(decoded))
        image.verify()
        image = Image.open(io.BytesIO(decoded))
        image.load()
    except Exception as exc:
        raise ValueError('Template is not a valid image.') from exc
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError('Template dimensions are too large.')
    if pil_format == 'JPEG' and image.mode in ('RGBA', 'P'):
        image = image.convert('RGB')
    return image, pil_format, ext


def draw_certificate(template_image, str1, str2, settings, str3='', values=None):
    """Overlay string 1 + string 2 + optional string 3 text on a copy of the template."""
    cert = template_image.copy()
    draw = ImageDraw.Draw(cert)

    if 'fields' in settings:
        values = values if values is not None else [str1, str2, str3]
        for field in settings['fields']:
            column = field['column']
            text = values[column] if column < len(values) else ''
            font = load_font(field['Font'], field['FontSize'], field.get('Bold') in (True, 'true', 'True', '1', 1))
            draw.text((field['X'], field['Y']), text, fill=hex_to_rgb(field['Color']), font=font)
        return cert

    name_bold = settings.get('nameBold') in (True, 'true', 'True', '1', 1)
    dept_bold = settings.get('deptBold') in (True, 'true', 'True', '1', 1)
    str3_bold = settings.get('str3Bold') in (True, 'true', 'True', '1', 1)
    name_font = load_font(settings.get('nameFont', 'arial.ttf'), int(settings.get('nameFontSize', 38)), name_bold)
    dept_font = load_font(settings.get('deptFont', 'arial.ttf'), int(settings.get('deptFontSize', 32)), dept_bold)
    str3_font = load_font(settings.get('str3Font', 'arial.ttf'), int(settings.get('str3FontSize', 28)), str3_bold)

    draw.text(
        (int(settings.get('nameX', 420)), int(settings.get('nameY', 270))),
        str1, fill=hex_to_rgb(settings.get('nameColor', '#000000')), font=name_font,
    )
    draw.text(
        (int(settings.get('deptX', 76)), int(settings.get('deptY', 303))),
        str2, fill=hex_to_rgb(settings.get('deptColor', '#000000')), font=dept_font,
    )
    if str3:
        draw.text(
            (int(settings.get('str3X', 76)), int(settings.get('str3Y', 340))),
            str3, fill=hex_to_rgb(settings.get('str3Color', '#000000')), font=str3_font,
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
        if len(zf.infolist()) > MAX_ZIP_ENTRIES:
            raise ValueError('ZIP contains too many files.')
        total_size = 0
        for member in zf.infolist():
            if member.is_dir():
                continue
            if os.path.isabs(member.filename) or '..' in member.filename.replace('\\', '/').split('/'):
                raise ValueError('ZIP contains an unsafe path.')
            total_size += member.file_size
            if total_size > MAX_ZIP_UNCOMPRESSED:
                raise ValueError('ZIP expands beyond the allowed size.')
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
    count = 0
    total_size = 0
    for upload in files:
        if not upload or not upload.filename:
            continue

        if is_zip_upload(upload):
            try:
                archive = zipfile.ZipFile(upload.stream)
            except zipfile.BadZipFile:
                raise ValueError(f'{upload.filename} is not a valid ZIP archive.')

            with archive:
                if len(archive.infolist()) > MAX_ZIP_ENTRIES:
                    raise ValueError('ZIP contains too many files.')
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    member_ext = os.path.splitext(member.filename)[1].lower()
                    if member_ext not in BULK_IMAGE_EXTENSIONS:
                        continue
                    if os.path.isabs(member.filename) or '..' in member.filename.replace('\\', '/').split('/') or member.file_size > MAX_IMAGE_BYTES:
                        raise ValueError('ZIP contains an unsafe or oversized image.')
                    total_size += member.file_size
                    if total_size > MAX_ZIP_UNCOMPRESSED:
                        raise ValueError('ZIP expands beyond the allowed size.')
                    with archive.open(member) as image_stream:
                        image, pil_format, ext = decode_bulk_image_stream(
                            image_stream,
                            f'{upload.filename}/{member.filename}',
                        )
                    yield member.filename, image, pil_format, ext
                    count += 1
                    if count > MAX_PARTICIPANTS:
                        raise ValueError(f'At most {MAX_PARTICIPANTS} images are allowed.')
            continue

        upload_ext = os.path.splitext(upload.filename)[1].lower()
        if upload_ext not in BULK_IMAGE_EXTENSIONS:
            raise ValueError(f'{upload.filename} is not supported. Upload PNG, JPG, or ZIP files.')

        if upload.content_length and upload.content_length > MAX_IMAGE_BYTES:
            raise ValueError('Image exceeds the upload limit.')
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
@login_required
def index():
    return render_template('index.html', csrf_token=csrf_token())


@app.route('/bulk-editor')
@login_required
def bulk_editor():
    return render_template('bulk_editor.html', csrf_token=csrf_token())


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit(os.environ.get('LOGIN_RATE_LIMIT', '5 per minute'), methods=['POST'])
def login():
    if request.method == 'GET':
        if session.get('authenticated'):
            return redirect(url_for('index'))
        return render_template('login.html', csrf_token=csrf_token())
    require_csrf()
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    if not username or not verify_user_password(username, password):
        logger.warning('Failed login attempt for "%s" from %s', username, get_remote_address())
        return render_template('login.html', csrf_token=csrf_token(), error='Invalid username or password.'), 401
    session.clear()
    session.permanent = True
    session['authenticated'] = True
    session['username'] = username
    session['_csrf_token'] = secrets.token_urlsafe(32)
    logger.info('Successful login for "%s" from %s', username, get_remote_address())
    next_url = request.args.get('next', '')
    if next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect(url_for('index'))


@app.route('/signup', methods=['GET', 'POST'])
@limiter.limit('10 per hour', methods=['POST'])
def signup():
    if request.method == 'GET':
        if session.get('authenticated'):
            return redirect(url_for('index'))
        return render_template('signup.html', csrf_token=csrf_token())
    require_csrf()
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    confirm  = request.form.get('confirm_password') or ''

    # Validate username
    if not USERNAME_RE.fullmatch(username):
        return render_template('signup.html', csrf_token=csrf_token(),
            error='Username must be 3–40 characters: letters, numbers, dots, hyphens, underscores only.'), 422
    # Validate password strength
    if len(password) < 8:
        return render_template('signup.html', csrf_token=csrf_token(),
            error='Password must be at least 8 characters.'), 422
    if password != confirm:
        return render_template('signup.html', csrf_token=csrf_token(),
            error='Passwords do not match.'), 422

    if not db_create_user(username, password):
        return render_template('signup.html', csrf_token=csrf_token(),
            error='Username already taken. Please choose another.'), 409

    logger.info('New user registered: "%s" from %s', username, get_remote_address())
    # Auto-login after signup
    session.clear()
    session.permanent = True
    session['authenticated'] = True
    session['username'] = username
    session['_csrf_token'] = secrets.token_urlsafe(32)
    return redirect(url_for('index'))


@app.route('/logout', methods=['POST'])
@login_required
@limiter.limit('20 per minute')
def logout():
    require_csrf()
    session.clear()
    return redirect(url_for('login'))


@app.route('/health')
@limiter.limit('60 per minute')
def health():
    return jsonify({'status': 'ok'}), 200


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    response.headers.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    response.headers.setdefault('X-Frame-Options', 'DENY')
    if app.config['SESSION_COOKIE_SECURE']:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return response


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({'success': False, 'error': 'Upload is too large.'}), 413


@app.errorhandler(429)
def too_many_requests(_error):
    return jsonify({'success': False, 'error': 'Too many requests. Please try again later.'}), 429


@app.errorhandler(400)
def bad_request(error):
    return jsonify({'success': False, 'error': getattr(error, 'description', 'Invalid request.')}), 400


@app.errorhandler(403)
def forbidden(error):
    return jsonify({'success': False, 'error': getattr(error, 'description', 'Forbidden.')}), 403


@app.errorhandler(404)
def not_found(_error):
    return jsonify({'success': False, 'error': 'Not found.'}), 404


@app.errorhandler(Exception)
def unhandled_error(error):
    logger.exception('Unhandled application error: %s', error)
    return jsonify({'success': False, 'error': 'An unexpected server error occurred.'}), 500


def parse_csv_rows(rows, header_mode='auto'):
    rows = [[c.strip() for c in row] for row in rows if any(c.strip() for c in row)]
    if not rows:
        return [], []
    first = [c.lower() for c in rows[0]]
    has_header = header_mode == 'yes' or (header_mode == 'auto' and (
        first[0] in ('name', 'participant', 'string1', 'string 1', 'str1', 'string_1', 'participant name', 'student name')
        or any(c in ('email', 'email id', 'email address', 'e-mail') for c in first)))
    headers = rows.pop(0) if has_header else []
    width = max([len(headers)] + [len(row) for row in rows])
    email_index = next((i for i, h in enumerate(headers) if h.lower() in ('email', 'email id', 'email address', 'e-mail')), None)
    if email_index is None and rows and width > 1 and any(len(r) == width and validate_email(r[-1]) for r in rows):
        email_index = width - 1
    indexes = [i for i in range(width) if i != email_index]
    columns = [headers[i] if i < len(headers) and headers[i] else f'String {n + 1}' for n, i in enumerate(indexes)]
    participants = []
    for row in rows:
        values = [row[i] if i < len(row) else '' for i in indexes]
        participants.append({'values': values, 'name': values[0] if values else '',
                             'department': values[1] if len(values) > 1 else '',
                             'string3': values[2] if len(values) > 2 else '',
                             'email': row[email_index] if email_index is not None and email_index < len(row) else ''})
    return participants, columns


def personalize(text, participant):
    values = participant.get('values', [])
    substitutions = {'name': participant['name'], 'department': participant['department']}
    substitutions.update({f'string{i + 1}': value for i, value in enumerate(values)})
    return re.sub(r'\{(name|department|string\d+)\}', lambda m: substitutions.get(m[1], ''), text)


@app.route('/parse-csv', methods=['POST'])
@login_required
@limiter.limit('30 per minute')
def parse_csv():
    """Parse CSV → [{string1, string2, string3, email}, …]"""
    try:
        if 'csvFile' not in request.files or request.files['csvFile'].filename == '':
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400

        require_csrf()
        raw = request.files['csvFile'].stream.read(MAX_IMAGE_BYTES)
        if len(raw) >= MAX_IMAGE_BYTES:
            return jsonify({'success': False, 'error': 'CSV exceeds the upload limit'}), 413
        stream = io.StringIO(raw.decode('utf-8-sig'), newline=None)
        rows = list(csv.reader(stream))
        if len(rows) > MAX_CSV_ROWS + 1:
            return jsonify({'success': False, 'error': f'Maximum {MAX_CSV_ROWS} CSV rows allowed'}), 413

        participants, columns = parse_csv_rows(rows, request.form.get('headerMode', 'auto'))
        return jsonify({'success': True, 'participants': validate_participants(participants),
                        'columns': columns, 'count': len(participants)})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/bulk-editor/process', methods=['POST'])
@login_required
@limiter.limit('10 per minute')
def process_bulk_editor_batch():
    """Apply visual edits to uploaded PNG/JPG certificates and return a ZIP."""
    try:
        require_csrf()
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
@login_required
@limiter.limit('60 per minute')
def generate_certificate():
    """Generate a single certificate (for preview). Returns base64 image."""
    try:
        require_csrf()
        data = request.json
        template_image, pil_format, ext = decode_template(data['template'])
        str1 = data.get('string1') or data.get('name', '')
        str2 = data.get('string2') or data.get('department', '')
        str3 = data.get('string3', '')
        participants = validate_participants([dict(data, name=str1, department=str2, string3=str3)])
        settings = validate_settings(data, template_image.width, template_image.height)
        cert = draw_certificate(
            template_image,
            participants[0]['name'],
            participants[0]['department'],
            settings,
            str3=participants[0].get('string3', ''), values=participants[0]['values'],
        )
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
@login_required
@limiter.limit('10 per minute')
def generate_batch():
    """Batch generate → merged PDF, individual PDFs ZIP, or image ZIP depending on exportFormat."""
    try:
        require_csrf()
        data = request.json
        template_image, default_fmt, default_ext = decode_template(data['template'])
        participants = validate_participants(data.get('participants', []))
        settings = validate_settings(data.get('settings', {}), template_image.width, template_image.height)

        export_fmt = settings.get('exportFormat', 'same')
        pil_format, ext = EXPORT_FORMAT_MAP.get(export_fmt, (default_fmt, default_ext))

        certs = []
        for p in participants:
            str1 = p.get('string1') or p.get('name', '')
            str2 = p.get('string2') or p.get('department', '')
            str3 = p.get('string3', '')
            c = draw_certificate(template_image, str1, str2, settings, str3=str3, values=p['values'])
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
                for idx, (p, c) in enumerate(zip(participants, certs), start=1):
                    p_name = p.get('string1') or p.get('name') or f'certificate_{idx}'
                    pdf_buf = io.BytesIO()
                    c.save(pdf_buf, 'PDF')
                    filename = f"{safe_filename(p_name)}_certificate.pdf"
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
            for idx, (p, c) in enumerate(zip(participants, certs), start=1):
                p_name = p.get('string1') or p.get('name') or f'certificate_{idx}'
                zf.writestr(
                    f"{safe_filename(p_name)}_certificate.{ext}",
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


def _resolve_smtp_from_request(data):
    """Build (host, port, mode, user, password, sender) from request data using SMTP_PRESETS."""
    provider = (data.get('smtpProvider') or 'gmail').lower()
    user     = (data.get('smtpUser') or '').strip()
    password = data.get('smtpPassword') or ''
    if not user or not password:
        raise ValueError('Sender email and app password are required.')
    if provider in SMTP_PRESETS:
        host, port, mode = SMTP_PRESETS[provider]
    else:  # custom
        host = (data.get('smtpHost') or '').strip()
        port = int(data.get('smtpPort') or 587)
        mode = 'ssl' if port == 465 else 'starttls'
        if not host:
            raise ValueError('SMTP host is required for a custom provider.')
    return host, port, mode, user, password, user  # sender == user


class EmailService:
    """Provider abstraction; credentials are read only from server environment."""
    def __init__(self):
        self.provider = os.environ.get('EMAIL_PROVIDER', 'disabled').lower()
        self.sender = os.environ.get('EMAIL_FROM', '').strip()
        self.reply_to = os.environ.get('EMAIL_REPLY_TO', '').strip()
        self.host = os.environ.get('EMAIL_SMTP_HOST', '').strip()
        self.port = int(os.environ.get('EMAIL_SMTP_PORT', '587'))
        self.user = os.environ.get('EMAIL_SMTP_USER', '').strip()
        self.password = os.environ.get('EMAIL_SMTP_PASSWORD', '')
        self.api_key = os.environ.get('EMAIL_API_KEY', '')
        self.server = None

    def connect(self):
        if self.provider == 'smtp':
            if not all((self.sender, self.host, self.user, self.password)):
                raise RuntimeError('Server email provider is not configured.')
            self.server = _make_smtp_connection(self.host, self.port, 'ssl' if self.port == 465 else 'starttls', self.user, self.password)
        elif self.provider == 'resend':
            if not self.sender or not self.api_key:
                raise RuntimeError('Server email provider is not configured.')
        else:
            raise RuntimeError('Email sending is not enabled.')

    def send(self, message, recipient):
        if self.provider == 'smtp':
            try:
                self.server.sendmail(self.sender, recipient, message.as_string())
            except (smtplib.SMTPServerDisconnected, TimeoutError, OSError, smtplib.SMTPException):
                self.connect()
                self.server.sendmail(self.sender, recipient, message.as_string())
            return 'accepted'
        if self.provider == 'resend':
            body = {
                'from': self.sender,
                'to': [recipient],
                'subject': message['Subject'],
                'text': message.get_payload()[0].get_payload(decode=True).decode('utf-8', errors='replace') if message.get_payload() else '',
            }
            if self.reply_to:
                body['reply_to'] = [self.reply_to]
            req = urllib.request.Request('https://api.resend.com/emails', data=json.dumps(body).encode(), headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}, method='POST')
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    if response.status not in (200, 201, 202):
                        raise RuntimeError('Provider rejected message')
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500 and exc.code != 429:
                    raise ValueError('Email provider rejected the recipient or message')
                raise RuntimeError('Temporary email provider failure')
            except (urllib.error.URLError, TimeoutError) as exc:
                raise RuntimeError('Temporary email provider failure') from exc
            return 'accepted'
        raise RuntimeError('Email sending is not enabled.')

    def close(self):
        if self.server:
            try: self.server.quit()
            except Exception: pass
            self.server = None


@app.route('/test-smtp', methods=['POST'])
@login_required
@limiter.limit('10 per minute')
def test_smtp():
    """Test SMTP connectivity using credentials supplied by the browser."""
    try:
        require_csrf()
        data = request.json or {}
        host, port, mode, user, password, _ = _resolve_smtp_from_request(data)
        server = _make_smtp_connection(host, port, mode, user, password)
        server.quit()
        provider = (data.get('smtpProvider') or 'custom').lower()
        return jsonify({'success': True, 'message': f'Connected to {host} as {user} ✓'})
    except smtplib.SMTPAuthenticationError:
        return jsonify({'success': False, 'error': 'Authentication failed — check your email and app password.'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


def retryable_mail_error(error):
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return bool(error.recipients) and all(400 <= code < 500 for code, _ in error.recipients.values())
    if isinstance(error, smtplib.SMTPResponseException):
        return 400 <= error.smtp_code < 500
    if isinstance(error, smtplib.SMTPServerDisconnected):
        return True
    return isinstance(error, OSError) and not isinstance(error, smtplib.SMTPException)


def send_job_owner():
    return hashlib.sha256(session.get('_csrf_token', '').encode()).hexdigest()


@app.route('/send-jobs/<job_id>/control', methods=['POST'])
@login_required
@limiter.limit('60 per minute')
def control_send_job(job_id):
    require_csrf()
    action = (request.get_json(silent=True) or {}).get('action')
    if action not in ('pause', 'resume'):
        return jsonify(error='Invalid action'), 400
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        changed = conn.execute('UPDATE send_jobs SET paused=? WHERE id=? AND owner=?',
                               (int(action == 'pause'), job_id, send_job_owner())).rowcount
    if not changed:
        return jsonify(error='Send job not found'), 404
    return jsonify(success=True)


@app.route('/send-certificates', methods=['POST'])
@login_required
@limiter.limit('5 per minute')
def send_certificates():
    """Generate + email each certificate. Streams SSE progress events."""
    require_csrf()
    data = request.json or {}

    has_template = bool(data.get('template'))
    template_image, pil_format, ext = None, 'PNG', 'png'
    if has_template:
        try:
            template_image, pil_format, ext = decode_template(data['template'])
        except Exception as e:
            return jsonify(success=False, error=f'Template decode failed: {e}'), 400

    try:
        participants = validate_participants(data.get('participants', []))
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    try:
        settings = validate_settings(data.get('settings', {}), template_image.width if template_image else None, template_image.height if template_image else None)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    # Optional extra attachment ZIP — one file per participant, matched by sheet order
    attachments_zip_data = data.get('attachmentsZip')
    try:
        extra_attachments = extract_zip_attachments(attachments_zip_data)
    except Exception as e:
        return jsonify(success=False, error=f'Attachment ZIP could not be read: {e}'), 400

    if extra_attachments and len(extra_attachments) != len(participants):
        def _err():
            yield f"data: {json.dumps({'type': 'error', 'message': f'Attachment count mismatch: ZIP has {len(extra_attachments)} files but sheet has {len(participants)} rows. Fix this before sending.'})}\n\n"
        return Response(stream_with_context(_err()), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # ── Resolve credentials from UI payload ───────────────────────────────────────
    try:
        smtp_host, smtp_port, smtp_mode, smtp_user, smtp_pass, smtp_sender = _resolve_smtp_from_request(data)
    except ValueError as e:
        return jsonify(success=False, error=str(e)), 400

    class _InlineEmailService:
        """Lightweight SMTP wrapper built from per-request credentials."""
        def __init__(self):
            self.sender = smtp_sender
            self.server = None
        def connect(self):
            self.server = _make_smtp_connection(smtp_host, smtp_port, smtp_mode, smtp_user, smtp_pass)
        def send(self, message, recipient):
            self.server.sendmail(self.sender, recipient, message.as_string())
            return 'accepted'
        def close(self):
            if self.server:
                try: self.server.quit()
                except Exception: pass
                self.server = None

    from_name = (data.get('fromName') or os.environ.get('EMAIL_FROM_NAME', 'CertFlow')).strip()
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

    job_id = data.get('jobId') or secrets.token_urlsafe(24)
    if not isinstance(job_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', job_id):
        return jsonify(error='Invalid send job ID'), 400
    owner = send_job_owner()
    fingerprint = hashlib.sha256(json.dumps({k: v for k, v in data.items() if k not in ('jobId', 'smtpPassword')}, sort_keys=True).encode()).hexdigest()
    lease = time.time() + 600
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT OR IGNORE INTO send_jobs (id, owner, fingerprint) VALUES (?, ?, ?)', (job_id, owner, fingerprint))
        job = conn.execute('SELECT owner, fingerprint, completed, paused, lease FROM send_jobs WHERE id=?', (job_id,)).fetchone()
        if job[0] != owner or job[1] != fingerprint:
            return jsonify(error='Send job does not match this request'), 409
        if job[4] > time.time():
            return jsonify(error='The previous send is still finishing. Retry shortly.'), 409
        conn.execute('UPDATE send_jobs SET lease=? WHERE id=?', (lease, job_id))
        completed = json.loads(job[2])

    def checkpoint(index, status):
        completed[str(index)] = status
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.execute('UPDATE send_jobs SET completed=?, lease=? WHERE id=?',
                         (json.dumps(completed), time.time() + 600, job_id))

    def stream():
        results = [{'status': status} for status in completed.values() if status != 'skip']
        skipped = sum(status == 'skip' for status in completed.values())
        service = _InlineEmailService()

        try:
            # ── Initial SSE buffer flush for Gunicorn / reverse proxies ───────────
            yield ": " + (" " * 1024) + "\n\n"

            # ── Connect ──────────────────────────────────────────────────────────
            try:
                service.connect()
                yield f"data: {json.dumps({'type': 'status', 'message': 'Connected to mail server. Preparing emails...'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'retry' if retryable_mail_error(e) else 'error', 'message': f'Email connection failed: {e}'})}\n\n"
                return

            total = len([p for p in participants if p.get('email')])

            for i, p in enumerate(participants):
                if str(i) in completed:
                    continue
                with closing(sqlite3.connect(DB_PATH)) as conn, conn:
                    paused = conn.execute('SELECT paused FROM send_jobs WHERE id=?', (job_id,)).fetchone()[0]
                    conn.execute('UPDATE send_jobs SET lease=? WHERE id=?', (time.time() + 600, job_id))
                if paused:
                    yield f"data: {json.dumps({'type': 'paused', 'message': 'Paused. Resume to continue from the next unsent row.'})}\n\n"
                    return
                email_addr = (p.get('email') or '').strip()
                p_str1 = p.get('string1') or p.get('name', '')
                p_str2 = p.get('string2') or p.get('department', '')
                p_str3 = p.get('string3', '')
                p_name = p_str1 or f"Participant {i + 1}"
                if not email_addr:
                    skipped += 1
                    checkpoint(i, 'skip')
                    yield f"data: {json.dumps({'type': 'skip', 'name': p_name, 'reason': 'no email'})}\n\n"
                    continue

                try:
                    # Live status update before sending payload
                    progress_msg = f"Sending {i + 1}/{total}: {p_name} ({email_addr})..."
                    yield f"data: {json.dumps({'type': 'progress', 'name': p_name, 'email': email_addr, 'index': i + 1, 'total': total, 'message': progress_msg})}\n\n"

                    personal_body = personalize(body, p)

                    msg = MIMEMultipart()
                    msg['From']    = f'{from_name} <{service.sender}>'
                    msg['To']      = email_addr
                    msg['Subject'] = personalize(subject, p)
                    msg.attach(MIMEText(personal_body, 'plain', 'utf-8'))

                    # Generate and attach certificate if template was provided and attachment is enabled
                    attach_cert_flag = data.get('attachCert', True)
                    if has_template and template_image and attach_cert_flag:
                        cert = draw_certificate(template_image, p_str1, p_str2, settings, str3=p_str3, values=p['values'])

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

                    service.send(msg, email_addr)

                    checkpoint(i, 'sent')
                    results.append({'name': p_name, 'status': 'sent'})
                    yield f"data: {json.dumps({'type': 'sent', 'name': p_name, 'email': email_addr, 'index': i + 1, 'total': total})}\n\n"

                except Exception as e:
                    if retryable_mail_error(e):
                        yield f"data: {json.dumps({'type': 'retry', 'message': f'Temporary mail failure; retrying this row in 15 seconds: {e}'})}\n\n"
                        return
                    checkpoint(i, 'failed')
                    results.append({'name': p_name, 'status': 'failed', 'reason': str(e)})
                    yield f"data: {json.dumps({'type': 'failed', 'name': p_name, 'email': email_addr, 'reason': str(e)})}\n\n"

            sent   = sum(1 for r in results if r['status'] == 'sent')
            failed = sum(1 for r in results if r['status'] == 'failed')
            yield f"data: {json.dumps({'type': 'done', 'sent': sent, 'failed': failed, 'skipped': skipped})}\n\n"

        except Exception as top_err:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Server error: {top_err}'})}\n\n"
        finally:
            service.close()
            with closing(sqlite3.connect(DB_PATH)) as conn, conn:
                conn.execute('UPDATE send_jobs SET lease=0 WHERE id=?', (job_id,))

    return Response(stream_with_context(stream()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})



# ── Throttled Mailer — DB schema ──────────────────────────────────────────────

def init_mailer_db():
    """Create throttled-mailer tables if they don't exist yet."""
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mailer_queue (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                email    TEXT    NOT NULL,
                name     TEXT    NOT NULL DEFAULT '',
                cert_path TEXT   NOT NULL DEFAULT '',
                status   TEXT    NOT NULL DEFAULT 'pending',   -- pending | sent | failed
                added_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mailer_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()

init_mailer_db()


def _mstate_get(key, default=None):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        row = conn.execute('SELECT value FROM mailer_state WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def _mstate_set(key, value):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute('INSERT OR REPLACE INTO mailer_state (key, value) VALUES (?,?)', (key, str(value)))
        conn.commit()


def _mstate_delete(key):
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute('DELETE FROM mailer_state WHERE key=?', (key,))
        conn.commit()


# ── Throttle constants (same as mailer/mailer_app.py) ─────────────────────────

_TM_SEND_GAP_MIN  = 3 * 60    # 180 s
_TM_SEND_GAP_MAX  = 5 * 60    # 300 s
_TM_JITTER        = 0.30
_TM_BATCH_MIN     = 15
_TM_BATCH_MAX     = 20
_TM_PAUSE_MIN     = 15 * 60   # 900 s
_TM_PAUSE_MAX     = 25 * 60   # 1 500 s


def _tm_jitter(base):
    delta = base * _TM_JITTER
    return base + random.uniform(-delta, delta)


def _tm_send_gap():
    return _tm_jitter(random.uniform(_TM_SEND_GAP_MIN, _TM_SEND_GAP_MAX))


def _tm_batch_pause():
    return _tm_jitter(random.uniform(_TM_PAUSE_MIN, _TM_PAUSE_MAX))


# ── Mailer SMTP helper (uses env vars, same as existing _make_smtp_connection) ─

_TM_GMAIL_USER = lambda: os.environ.get('GMAIL_USER', '')
_TM_GMAIL_PASS = lambda: os.environ.get('GMAIL_APP_PASSWORD', '')
_TM_SUBJECT    = lambda: os.environ.get('MAILER_SUBJECT', 'Your Certificate — CertFlow')
_TM_BODY_HTML  = """\
<html><body>
<p>Dear <b>{name}</b>,</p>
<p>Congratulations! Please find your certificate attached to this email.</p>
<p>We appreciate your participation and look forward to seeing you again.</p>
<br><p>Best regards,<br>The CertFlow Team</p>
</body></html>"""
_TM_BODY_TEXT  = ("Dear {name},\n\nCongratulations! Please find your certificate attached.\n\n"
                  "Best regards,\nThe CertFlow Team")


def _tm_send_one(recipient_id, email, name, cert_path):
    """Send one certificate email via env-var Gmail credentials. Raises on failure."""
    gmail_user = _TM_GMAIL_USER()
    gmail_pass = _TM_GMAIL_PASS()
    if not gmail_user or not gmail_pass:
        raise RuntimeError('GMAIL_USER / GMAIL_APP_PASSWORD env vars are not set on this server.')

    full_path = os.path.join(os.path.dirname(__file__), cert_path)
    if not os.path.exists(full_path):
        raise FileNotFoundError(f'Certificate not found: {cert_path}')

    ext = os.path.splitext(cert_path)[1].lower()

    msg = MIMEMultipart('mixed')
    msg['Subject'] = _TM_SUBJECT()
    msg['From']    = gmail_user
    msg['To']      = email

    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(_TM_BODY_TEXT.format(name=name), 'plain', 'utf-8'))
    alt.attach(MIMEText(_TM_BODY_HTML.format(name=name), 'html', 'utf-8'))
    msg.attach(alt)

    with open(full_path, 'rb') as fh:
        data = fh.read()

    cert_filename = os.path.basename(cert_path)
    if ext == '.pdf':
        part = MIMEApplication(data, _subtype='pdf')
    elif ext == '.png':
        from email.mime.image import MIMEImage
        part = MIMEImage(data, _subtype='png')
    elif ext in ('.jpg', '.jpeg'):
        from email.mime.image import MIMEImage
        part = MIMEImage(data, _subtype='jpeg')
    else:
        part = MIMEApplication(data)
    part.add_header('Content-Disposition', 'attachment', filename=cert_filename)
    msg.attach(part)

    with smtplib.SMTP('smtp.gmail.com', 587, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(gmail_user, gmail_pass)
        smtp.sendmail(gmail_user, [email], msg.as_bytes())

    logger.info('[ThrottledMailer] Sent certificate to %s (%s)', email, name)


# ── Throttled Mailer Routes ───────────────────────────────────────────────────

@app.route('/mailer/load-queue', methods=['POST'])
@login_required
@limiter.limit('20 per minute')
def mailer_load_queue():
    """
    Replace the mailer queue from a JSON array POSTed from the browser.
    Each entry: {email, name, cert_path}
    Resets all throttle state so a fresh run starts immediately.
    """
    require_csrf()
    data = request.json or {}
    entries = data.get('queue', [])
    if not isinstance(entries, list) or not entries:
        return jsonify({'success': False, 'error': 'Provide a non-empty queue array.'}), 400
    if len(entries) > MAX_PARTICIPANTS:
        return jsonify({'success': False, 'error': f'At most {MAX_PARTICIPANTS} entries allowed.'}), 400

    cleaned = []
    for i, e in enumerate(entries, 1):
        em = validate_email(str(e.get('email') or ''))
        nm = str(e.get('name') or '').strip()[:MAX_NAME_LENGTH]
        cp = str(e.get('cert_path') or '').strip()
        if not em:
            return jsonify({'success': False, 'error': f'Row {i}: invalid email.'}), 400
        if not nm:
            return jsonify({'success': False, 'error': f'Row {i}: name is required.'}), 400
        cleaned.append((em, nm, cp))

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute('DELETE FROM mailer_queue')
        conn.executemany(
            'INSERT INTO mailer_queue (email, name, cert_path, status) VALUES (?,?,?,?)',
            [(em, nm, cp, 'pending') for em, nm, cp in cleaned]
        )
        conn.commit()

    # Reset all throttle state
    for key in ('tm_last_sent_at', 'tm_batch_count', 'tm_batch_size',
                'tm_batch_started_at', 'tm_next_gap', 'tm_in_batch_pause', 'tm_pause_deadline', 'tm_manual_pause', 'tm_retry_at'):
        _mstate_delete(key)

    logger.info('[ThrottledMailer] Queue loaded: %d recipients', len(cleaned))
    return jsonify({'success': True, 'loaded': len(cleaned)})


@app.route('/mailer/control', methods=['POST'])
@login_required
@limiter.limit('30 per minute')
def mailer_control():
    require_csrf()
    action = (request.get_json(silent=True) or {}).get('action')
    if action not in ('pause', 'resume'):
        return jsonify(error='Invalid action'), 400
    _mstate_set('tm_manual_pause', '1' if action == 'pause' else '0')
    return jsonify(success=True)


@app.route('/mailer/send-next')
@limiter.limit('30 per minute')
def mailer_send_next():
    """
    Throttled send endpoint. Called by cron-job.org every 4–5 min OR via the
    in-app "Trigger Next Send" button (which passes the session cookie).
    Two auth paths:
      1. Session login  — already authenticated via browser
      2. ?secret=MAILER_SECRET — for external cron callers
    """
    # Auth: session OR secret param
    mailer_secret = os.environ.get('MAILER_SECRET', '')
    via_secret = mailer_secret and request.args.get('secret', '') == mailer_secret
    via_session = bool(session.get('authenticated'))
    if not via_secret and not via_session:
        return jsonify({'status': 'forbidden'}), 403

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute("SELECT value FROM mailer_state WHERE key='tm_send_lease'").fetchone()
        if current and float(current[0]) > time.time():
            return jsonify(status='sending')
        conn.execute("INSERT OR REPLACE INTO mailer_state VALUES ('tm_send_lease', ?)", (str(time.time() + 600),))
    try:
        return _mailer_send_next_locked()
    finally:
        _mstate_delete('tm_send_lease')


def _mailer_send_next_locked():
    if _mstate_get('tm_manual_pause') == '1':
        return jsonify(status='manual_pause')
    if float(_mstate_get('tm_retry_at', 0)) > time.time():
        return jsonify(status='retry_wait')
    now = time.time()

    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute('SELECT COUNT(*) FROM mailer_queue').fetchone()[0]
        sent_count = conn.execute("SELECT COUNT(*) FROM mailer_queue WHERE status='sent'").fetchone()[0]
        next_row = conn.execute(
            "SELECT * FROM mailer_queue WHERE status='pending' ORDER BY id LIMIT 1"
        ).fetchone()

    if not next_row:
        return jsonify({'status': 'done', 'total_sent': sent_count, 'total': total})

    # ── Inter-batch pause check ────────────────────────────────────────────────
    if _mstate_get('tm_in_batch_pause') == '1':
        deadline = float(_mstate_get('tm_pause_deadline', 0))
        remaining = deadline - now
        if remaining > 0:
            return jsonify({
                'status': 'paused',
                'reason': 'inter-batch cooldown',
                'resume_in_seconds': round(remaining),
                'resume_in_minutes': round(remaining / 60, 1),
                'sent': sent_count,
                'remaining': total - sent_count,
            })
        # Pause over — reset batch
        _mstate_set('tm_in_batch_pause', '0')
        _mstate_set('tm_batch_count', '0')
        _mstate_set('tm_batch_size', str(random.randint(_TM_BATCH_MIN, _TM_BATCH_MAX)))
        _mstate_set('tm_batch_started_at', str(now))
        _mstate_set('tm_next_gap', str(_tm_send_gap()))

    # ── Per-email gap check ────────────────────────────────────────────────────
    last_sent = float(_mstate_get('tm_last_sent_at', 0))
    if last_sent > 0:
        next_gap = float(_mstate_get('tm_next_gap', _TM_SEND_GAP_MAX))
        elapsed  = now - last_sent
        if elapsed < next_gap:
            wait = next_gap - elapsed
            return jsonify({
                'status': 'wait',
                'next_allowed_in_seconds': round(wait),
                'next_allowed_in_minutes': round(wait / 60, 1),
                'sent': sent_count,
                'remaining': total - sent_count,
            })

    # ── Send ───────────────────────────────────────────────────────────────────
    rid   = next_row['id']
    email = next_row['email']
    name  = next_row['name']
    cpath = next_row['cert_path']

    try:
        _tm_send_one(rid, email, name, cpath)
    except Exception as exc:
        if retryable_mail_error(exc):
            _mstate_set('tm_retry_at', time.time() + 60)
            return jsonify(status='retry_wait', reason=str(exc), to=email), 503
        logger.error('[ThrottledMailer] SMTP failure for %s: %s', email, exc)
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.execute("UPDATE mailer_queue SET status='failed' WHERE id=?", (rid,))
            conn.commit()
        return jsonify({'status': 'error', 'reason': str(exc), 'to': email}), 500

    # ── Update state ──────────────────────────────────────────────────────────
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("UPDATE mailer_queue SET status='sent' WHERE id=?", (rid,))
        conn.commit()

    batch_count = int(_mstate_get('tm_batch_count', 0)) + 1
    batch_size  = int(_mstate_get('tm_batch_size', random.randint(_TM_BATCH_MIN, _TM_BATCH_MAX)))
    _mstate_set('tm_batch_count', batch_count)
    _mstate_set('tm_last_sent_at', now)
    _mstate_set('tm_next_gap', str(_tm_send_gap()))

    hit_batch_limit = batch_count >= batch_size
    if hit_batch_limit:
        # Check if there are more pending
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            more = conn.execute("SELECT 1 FROM mailer_queue WHERE status='pending' LIMIT 1").fetchone()
        if more:
            pause = _tm_batch_pause()
            _mstate_set('tm_in_batch_pause', '1')
            _mstate_set('tm_pause_deadline', str(now + pause))
            logger.info('[ThrottledMailer] Batch of %d done — pausing %.1f min', batch_count, pause / 60)

    new_sent = sent_count + 1
    return jsonify({
        'status': 'sent',
        'to': email,
        'name': name,
        'sent': new_sent,
        'remaining': total - new_sent,
        'batch_count': batch_count,
        'batch_size': batch_size,
        'batch_limit_hit': hit_batch_limit,
        'next_send_in_seconds': round(float(_mstate_get('tm_next_gap', _TM_SEND_GAP_MAX))) if not hit_batch_limit else None,
    })


@app.route('/mailer/status')
@login_required
@limiter.limit('60 per minute')
def mailer_status():
    """Read-only status view for the throttled mailer. No email sent."""
    now = time.time()
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.row_factory = sqlite3.Row
        total     = conn.execute('SELECT COUNT(*) FROM mailer_queue').fetchone()[0]
        sent      = conn.execute("SELECT COUNT(*) FROM mailer_queue WHERE status='sent'").fetchone()[0]
        pending   = conn.execute("SELECT COUNT(*) FROM mailer_queue WHERE status='pending'").fetchone()[0]
        failed    = conn.execute("SELECT COUNT(*) FROM mailer_queue WHERE status='failed'").fetchone()[0]
        # Preview rows around current index (last 3 sent + next 5 pending)
        recent    = conn.execute(
            "SELECT id,email,name,status FROM mailer_queue WHERE status='sent' ORDER BY id DESC LIMIT 3"
        ).fetchall()
        upcoming  = conn.execute(
            "SELECT id,email,name,status FROM mailer_queue WHERE status IN ('pending','failed') ORDER BY id LIMIT 6"
        ).fetchall()

    in_pause  = _mstate_get('tm_in_batch_pause') == '1'
    deadline  = float(_mstate_get('tm_pause_deadline', 0))
    last_sent = float(_mstate_get('tm_last_sent_at', 0))
    next_gap  = float(_mstate_get('tm_next_gap', _TM_SEND_GAP_MAX))
    b_count   = int(_mstate_get('tm_batch_count', 0))
    b_size    = int(_mstate_get('tm_batch_size', 0))

    next_in = None
    status_label = 'idle'

    if total == 0:
        status_label = 'empty'
    elif pending == 0 and failed == 0:
        status_label = 'done'
    elif _mstate_get('tm_manual_pause') == '1':
        status_label = 'manual_pause'
    elif float(_mstate_get('tm_retry_at', 0)) > now:
        status_label = 'retry_wait'
    elif in_pause:
        status_label = 'paused'
        next_in = max(deadline - now, 0)
    elif last_sent > 0:
        elapsed = now - last_sent
        wait = next_gap - elapsed
        if wait > 0:
            status_label = 'waiting'
            next_in = wait
        else:
            status_label = 'ready'
    elif pending > 0:
        status_label = 'ready'

    def _row(r):
        return {'id': r['id'], 'email': r['email'], 'name': r['name'], 'status': r['status']}

    return jsonify({
        'status': status_label,
        'total': total,
        'sent': sent,
        'pending': pending,
        'failed': failed,
        'batch_count': b_count,
        'batch_size': b_size,
        'in_batch_pause': in_pause,
        'next_send_in_seconds': round(next_in) if next_in is not None else None,
        'next_send_in_minutes': round(next_in / 60, 1) if next_in is not None else None,
        'last_sent_at': last_sent,
        'recent': [_row(r) for r in recent],
        'upcoming': [_row(r) for r in upcoming],
        'gmail_configured': bool(_TM_GMAIL_USER() and _TM_GMAIL_PASS()),
    })


@app.route('/mailer/reset', methods=['POST'])
@login_required
@limiter.limit('5 per minute')
def mailer_reset():
    """Hard-reset: clear all queue rows and throttle state."""
    require_csrf()
    data = request.json or {}
    if data.get('confirm') != 'yes':
        return jsonify({'success': False, 'error': 'Send {confirm: "yes"} to reset.'}), 400
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute('DELETE FROM mailer_queue')
        conn.commit()
    for key in ('tm_last_sent_at', 'tm_batch_count', 'tm_batch_size',
                'tm_batch_started_at', 'tm_next_gap', 'tm_in_batch_pause', 'tm_pause_deadline', 'tm_manual_pause', 'tm_retry_at'):
        _mstate_delete(key)
    logger.info('[ThrottledMailer] Queue and state reset by %s', session.get('username'))
    return jsonify({'success': True, 'message': 'Queue and state cleared.'})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
