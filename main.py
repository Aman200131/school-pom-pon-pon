from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

try:
    import plivo
    from plivo import plivoxml
except Exception as exc:
    plivo = plivoxml = None
    PLIVO_IMPORT_ERROR = str(exc)
else:
    PLIVO_IMPORT_ERROR = ""

try:
    from google import genai
except Exception as exc:
    genai = None
    GEMINI_IMPORT_ERROR = str(exc)
else:
    GEMINI_IMPORT_ERROR = ""

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("digiviewer-sales-caller")

# ============================================================
# CONFIGURATION & CONSTANTS
# ============================================================
APP_VERSION = "5.6.1"
PLIVO_AUTH_ID = os.getenv("PLIVO_AUTH_ID", "").strip()
PLIVO_AUTH_TOKEN = os.getenv("PLIVO_AUTH_TOKEN", "").strip()
PLIVO_PHONE_NUMBER = os.getenv("PLIVO_PHONE_NUMBER", "").strip()

_raw_host = os.getenv("SERVER_HOST", "localhost:8000").strip()
SERVER_HOST = re.sub(r"^https?://", "", _raw_host).rstrip("/")
HTTP_SCHEME = "https" if os.getenv("USE_TLS", "true").lower() == "true" else "http"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", os.getenv("RENDER_EXTERNAL_URL", f"{HTTP_SCHEME}://{SERVER_HOST}")).strip().rstrip("/")

DEFAULT_RETRY_COUNT = max(0, min(3, int(os.getenv("DEFAULT_RETRY_COUNT", "1"))))
MAX_CONCURRENT_CALLS = max(1, min(15, int(os.getenv("MAX_CONCURRENT_CALLS", "15"))))
RETRY_DELAY_SECONDS = max(1, int(os.getenv("RETRY_DELAY_SECONDS", "12")))
CALL_WAIT_TIMEOUT_SECONDS = max(1, int(os.getenv("CALL_WAIT_TIMEOUT_SECONDS", "180")))
MAX_IMPORT_BYTES = max(1024 * 1024, int(os.getenv("MAX_IMPORT_BYTES", str(10 * 1024 * 1024))))
MAX_CONTACTS_PER_IMPORT = max(1, int(os.getenv("MAX_CONTACTS_PER_IMPORT", "10000")))
SALES_API_KEY = os.getenv("SALES_API_KEY", "").strip()
REQUIRE_PLIVO_SIGNATURES = os.getenv("REQUIRE_PLIVO_SIGNATURES", "false").lower() == "true"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
SALES_AI_MODEL = os.getenv("SALES_AI_MODEL", "gemini-2.5-flash").strip()
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts").strip()
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "").strip()

try:
    GEMINI_TTS_RESPONSE_TIMEOUT_SECONDS = max(1.5, min(8.0, float(os.getenv("GEMINI_TTS_RESPONSE_TIMEOUT_SECONDS", "2.8"))))
    SALES_AI_RESPONSE_TIMEOUT_SECONDS = max(2.0, min(8.0, float(os.getenv("SALES_AI_RESPONSE_TIMEOUT_SECONDS", "4"))))
except ValueError:
    GEMINI_TTS_RESPONSE_TIMEOUT_SECONDS, SALES_AI_RESPONSE_TIMEOUT_SECONDS = 7.0, 5.0

ENFORCE_CALLING_HOURS = os.getenv("ENFORCE_CALLING_HOURS", "false").lower() == "true"
CALLING_HOURS_START_IST = int(os.getenv("CALLING_HOURS_START_IST", "9"))
CALLING_HOURS_END_IST = int(os.getenv("CALLING_HOURS_END_IST", "20"))
CALL_PACING_DELAY_SECONDS = max(0.01, min(3.0, float(os.getenv("CALL_PACING_DELAY_SECONDS", "0.2"))))
MAX_AUDIO_CACHE_FILES = max(50, int(os.getenv("MAX_AUDIO_CACHE_FILES", "500")))

AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "/tmp/digiviewer-sales-audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("DB_PATH", "/tmp/digiviewer_sales_caller.db"))
DASHBOARD_DIR = Path(__file__).parent / "dashboard"
ROOT_INDEX_HTML = Path(__file__).parent / "index.html"

# ============================================================
# PERSISTENT SQLITE STORAGE LAYER
# ============================================================
_DB_LOCK = threading.RLock()
SIMULATE_DB_FAILURE = False


class _DualLock:
    """Event-loop-independent lock supporting both 'with' and 'async with'."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def __enter__(self) -> _DualLock:
        self._lock.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._lock.release()

    async def __aenter__(self) -> _DualLock:
        self._lock.acquire()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._lock.release()


def _get_db_conn() -> sqlite3.Connection:
    if SIMULATE_DB_FAILURE or os.getenv("SIMULATE_DB_FAILURE", "false").lower() == "true":
        raise sqlite3.OperationalError("Simulated database storage connection failure")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS contacts ("
        "id TEXT PRIMARY KEY, tenant_id TEXT, campaign_id TEXT, phone TEXT, business_segment TEXT, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS campaigns ("
        "id TEXT PRIMARY KEY, tenant_id TEXT, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS callbacks ("
        "id TEXT PRIMARY KEY, tenant_id TEXT, data TEXT)"
    )
    return conn


class PersistentDict(dict):
    """Dictionary backed by SQLite so leads and campaigns survive page refreshes, navigation, and restarts."""

    def __init__(self, table: str):
        super().__init__()
        self.table = table
        try:
            self.reload()
        except Exception as exc:
            logger.warning("Initial DB load skipped for %s: %s", table, exc)

    def reload(self) -> None:
        with _DB_LOCK, _get_db_conn() as conn:
            rows = conn.execute(f"SELECT id, data FROM {self.table}").fetchall()
        super().clear()
        for row_id, raw_json in rows:
            try:
                super().__setitem__(row_id, json.loads(raw_json))
            except Exception:
                pass

    def sync_from_db(self) -> None:
        with _DB_LOCK, _get_db_conn() as conn:
            rows = conn.execute(f"SELECT id, data FROM {self.table}").fetchall()
        for row_id, raw_json in rows:
            if row_id not in self:
                try:
                    item = json.loads(raw_json)
                    super().__setitem__(row_id, item)
                except Exception:
                    pass

    def save(self, key: str) -> None:
        if key in self:
            self[key] = self[key]

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            super().__setitem__(key, value)
            return
        value.setdefault("tenant_id", "default")
        if self.table == "contacts":
            value.setdefault("campaign_id", "")
        with _DB_LOCK, _get_db_conn() as conn:
            if self.table == "contacts":
                conn.execute(
                    "INSERT OR REPLACE INTO contacts (id, tenant_id, campaign_id, phone, business_segment, data) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(key),
                        str(value.get("tenant_id", "default")),
                        str(value.get("campaign_id", "")),
                        str(value.get("phone", "")),
                        str(value.get("business_segment", "schools")),
                        json.dumps(value),
                    ),
                )
            else:
                conn.execute(
                    f"INSERT OR REPLACE INTO {self.table} (id, tenant_id, data) VALUES (?, ?, ?)",
                    (str(key), str(value.get("tenant_id", "default")), json.dumps(value)),
                )
            conn.commit()
        super().__setitem__(key, value)

    def pop(self, key: str, *args: Any) -> Any:
        res = super().pop(key, *args)
        try:
            with _DB_LOCK, _get_db_conn() as conn:
                conn.execute(f"DELETE FROM {self.table} WHERE id = ?", (str(key),))
                conn.commit()
        except Exception as exc:
            logger.warning("DB delete failed for %s/%s: %s", self.table, key, exc)
        return res

    def clear(self) -> None:
        if self.table == "campaigns":
            for t in list(CAMPAIGN_TASKS.values()):
                try:
                    t.cancel()
                except Exception:
                    pass
            CAMPAIGN_TASKS.clear()
        super().clear()
        try:
            with _DB_LOCK, _get_db_conn() as conn:
                conn.execute(f"DELETE FROM {self.table}")
                conn.commit()
        except Exception as exc:
            logger.warning("DB clear failed for %s: %s", self.table, exc)

    def clear_memory_only(self) -> None:
        """Clears only in-memory cache while preserving SQLite records (simulates worker restart)."""
        super().clear()


class PersistentList(list):
    """List backed by SQLite so inbound callbacks survive server restarts and worker reloads."""

    def __init__(self, table: str = "callbacks"):
        super().__init__()
        self.table = table
        try:
            self.reload()
        except Exception as exc:
            logger.warning("Initial DB load skipped for %s: %s", table, exc)

    def reload(self) -> None:
        with _DB_LOCK, _get_db_conn() as conn:
            rows = conn.execute(f"SELECT data FROM {self.table} ORDER BY rowid ASC").fetchall()
        super().clear()
        for (raw_json,) in rows:
            try:
                super().append(json.loads(raw_json))
            except Exception:
                pass

    def sync_from_db(self) -> None:
        with _DB_LOCK, _get_db_conn() as conn:
            rows = conn.execute(f"SELECT id, data FROM {self.table} ORDER BY rowid ASC").fetchall()
        existing_ids = {x.get("id") for x in self if isinstance(x, dict)}
        for row_id, raw_json in rows:
            if row_id not in existing_ids:
                try:
                    super().append(json.loads(raw_json))
                except Exception:
                    pass

    def save_item(self, item: dict[str, Any]) -> None:
        if not isinstance(item, dict) or not item.get("id"):
            return
        item.setdefault("tenant_id", "default")
        with _DB_LOCK, _get_db_conn() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {self.table} (id, tenant_id, data) VALUES (?, ?, ?)",
                (str(item["id"]), str(item.get("tenant_id", "default")), json.dumps(item)),
            )
            conn.commit()

    def append(self, item: dict[str, Any]) -> None:
        if isinstance(item, dict):
            item.setdefault("id", str(uuid.uuid4()))
            item.setdefault("tenant_id", "default")
            self.save_item(item)
        super().append(item)

    def clear(self) -> None:
        super().clear()
        try:
            with _DB_LOCK, _get_db_conn() as conn:
                conn.execute(f"DELETE FROM {self.table}")
                conn.commit()
        except Exception as exc:
            logger.warning("DB clear failed for %s: %s", self.table, exc)

    def clear_memory_only(self) -> None:
        super().clear()


# Global state
CAMPAIGN_TASKS: dict[str, asyncio.Task] = {}
CONTACTS: PersistentDict = PersistentDict("contacts")
CAMPAIGNS: PersistentDict = PersistentDict("campaigns")
CALL_CONTEXTS: dict[str, dict[str, Any]] = {}
CALLBACKS: PersistentList = PersistentList("callbacks")
CALLBACK_BY_PHONE: dict[str, dict[str, Any]] = {
    c["phone"]: c for c in CONTACTS.values() if isinstance(c, dict) and c.get("phone")
}
GENERATED_AUDIO_CACHE: dict[str, Path] = {}
RECENT_NONCES: dict[str, float] = {}
FEE_DEMOS: dict[str, dict[str, Any]] = {}
FEE_IMPORT_PREVIEWS: dict[str, dict[str, Any]] = {}
FEE_CALL_CONTEXTS: dict[str, dict[str, Any]] = {}

CAMPAIGN_LOCK = _DualLock()
IMPORT_LOCK = _DualLock()
AUDIO_GENERATION_LOCK = threading.Lock()
NONCE_LOCK = threading.Lock()
_PACING_LOCK = threading.Lock()
_LAST_CALL_DISPATCH_TIME = 0.0

MAX_CALLBACKS, MAX_CALL_CONTEXTS, MAX_CAMPAIGNS, MAX_TOTAL_CONTACTS = 10000, 20000, 100, 50000
MAX_TEXT_FIELD_CHARS, FEE_DEMO_HOURS, FEE_DEMO_CALL_LIMIT = 500, 2, 10

VALID_SEGMENTS = {"schools", "clinics", "real-estate", "retail", "agencies", "other"}
SEGMENT_LABELS = {
    "schools": "Schools & Education", "clinics": "Clinics & Doctors",
    "real-estate": "Real Estate", "retail": "Retail & E-commerce",
    "agencies": "Agencies & Service Businesses", "other": "Other Businesses",
}

SEGMENT_ANNOUNCEMENTS = {
    "schools": "नमस्ते। मैं डिजीव्यूअर की ओर से बोल रहा हूँ। हम स्कूलों के लिए EduUPPSTAIRS School ERP और Fee CRM के ज़रिये पेंडिंग फीस फॉलो-अप और स्कूल मैनेजमेंट ऑटोमेशन की सुविधा प्रदान करते हैं। अगर आप फ्री डेमो देखना चाहते हैं, तो इसी नंबर पर कॉल बैक करें। धन्यवाद।",
    "clinics": "नमस्ते। मैं डिजीव्यूअर की ओर से बोल रहा हूँ। हम क्लिनिक्स और डॉक्टर्स के लिए पेशेंट इन्क्वायरी, अपॉइंटमेंट फॉलो-अप और पेशेंट कम्युनिकेशन ऑटोमेशन की सुविधा प्रदान करते हैं। डेमो के लिए इसी नंबर पर कॉल बैक करें। धन्यवाद।",
    "real-estate": "नमस्ते। मैं डिजीव्यूअर की ओर से बोल रहा हूँ। हम रियल एस्टेट बिज़नेस के लिए प्रॉपर्टी इन्क्वायरी फॉलो-अप, बायर लीड क्वालिफिकेशन और साइट विज़िट कोऑर्डिनेशन की सुविधा प्रदान करते हैं। अधिक जानकारी के लिए कॉल बैक करें। धन्यवाद।",
    "retail": "नमस्ते। मैं डिजीव्यूअर की ओर से बोल रहा हूँ। हम रिटेल और ई-कॉमर्स बिज़नेस के लिए कस्टमर इन्क्वायरी, सेल्स फॉलो-अप और ऑर्डर कम्युनिकेशन ऑटोमेशन की सुविधा प्रदान करते हैं। डेमो के लिए कॉल बैक करें। धन्यवाद।",
    "agencies": "नमस्ते। मैं डिजीव्यूअर की ओर से बोल रहा हूँ। हम एजेंसीज़ और सर्विस बिज़नेसेज़ के लिए इनबाउंड लीड क्वालिफिकेशन, क्लाइंट फॉलो-अप और बिज़नेस डेवलपमेंट ऑटोमेशन में मदद करते हैं। अधिक जानकारी के लिए कॉल बैक करें। धन्यवाद।",
    "other": "नमस्ते। मैं डिजीव्यूअर की ओर से बोल रहा हूँ। हम विभिन्न बिज़नेसेज़ के लिए कस्टमर इन्क्वायरी हैंडलिंग और लीड फॉलो-अप ऑटोमेशन की सुविधा प्रदान करते हैं। सही सॉल्यूशन के लिए कृपया कॉल बैक करें। धन्यवाद।",
}

HINDI_PHRASE_MAP = [
    (r"\bNamaste ji!?\s*Main DigiViewer se bol raha hoon\.?\s*Kya abhi do minute baat karna convenient rahega\??", "नमस्ते जी! मैं DigiViewer से बोल रहा हूँ। क्या अभी दो मिनट बात करना convenient रहेगा?"),
    (r"\bAapka samay dene ke liye dhanyavaad\.?", "आपका समय देने के लिए धन्यवाद।"),
    (r"\bKya main aapko do minute mein apni service samjha sakta hoon\??", "क्या मैं आपको दो मिनट में अपनी service समझा सकता हूँ?"),
    (r"\bAapko kis tarah ki solution ki zaroorat hai\??", "आपको किस तरह की solution की ज़रूरत है?"),
    (r"\bSamajh gaya\.?\s*Main aapko details share kar deta hoon\.?", "समझ गया। मैं आपको details share कर देता हूँ।"),
    (r"\bNamaste ji\b", "नमस्ते जी"), (r"\bnamaste ji\b", "नमस्ते जी"),
    (r"\bNamaste\b", "नमस्ते"), (r"\bnamaste\b", "नमस्ते"),
    (r"\bdhanyavaad\b", "धन्यवाद"), (r"\bdhanyavad\b", "धन्यवाद"),
    (r"\bshukriya\b", "शुक्रिया"), (r"\bSamajh gaya\b", "समझ गया"),
    (r"\bHaan ji\b", "हाँ जी"), (r"\bNahi ji\b", "नहीं जी"),
]

PHONE_ALIASES = ["phone", "mobile", "mobile_number", "phone_number", "contact_number", "whatsapp", "number", "mob", "phone_no", "phoneno", "mobile_no", "mobileno", "contact", "contact_no", "contactno", "telephone", "tel", "cell", "cell_phone", "cellphone", "calling_number", "parent_mobile", "parent_phone", "customer_phone", "client_phone"]
NAME_ALIASES = ["contact_name", "name", "person_name", "owner_name", "principal_name", "doctor_name", "lead_name"]
BUSINESS_NAME_ALIASES = ["business_name", "company", "company_name", "school_name", "school", "institution", "clinic_name", "agency_name", "organization", "firm"]
CITY_ALIASES = ["city", "location", "district", "address"]
LANGUAGE_ALIASES = ["language", "lang", "preferred_language"]
SEGMENT_ALIASES = ["business_segment", "segment", "industry", "business_type", "type"]
DNC_ALIASES = ["do_not_call", "dnc", "opt_out", "opted_out", "dnd", "blocked"]
CONSENT_ALIASES = ["consent", "permission", "authorized", "opt_in", "optin"]
CAMPAIGN_ALIASES = ["campaign_id", "campaign", "campaign_name"]


def _get_raw_fee_demo_html() -> str:
    path = DASHBOARD_DIR / "fee_demo.html"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return "<!doctype html><html><body><h1>EduUPPSTAIRS Fee Demo</h1><p><a id=\"templateLink\" href=\"#\">Download CSV template</a></p></body></html>"


_FEE_DEMO_HTML = _get_raw_fee_demo_html()


# ============================================================
# HELPER FUNCTIONS
# ============================================================
def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any, default: str = "") -> str:
    if value is None or str(value).strip().lower() in {"nan", "none", "nat"}:
        return default
    return str(value).strip()[:MAX_TEXT_FIELD_CHARS]


def normalize_phone(value: Any) -> str:
    if value is None or str(value).strip().lower() in {"nan", "none"}:
        return ""
    raw = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value).strip()
    if any(sep in raw for sep in ["/", ",", ";", "\n", "|"]):
        parts = re.split(r"[/,;\n|]", raw)
        raw = parts[0].strip() if parts else raw
    raw = re.sub(r"\.0+$", "", raw).replace("\ufeff", "").strip()
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("0") and digits[1] in "6789":
        return "+91" + digits[1:]
    if len(digits) == 13 and digits.startswith("910") and digits[3] in "6789":
        return "+91" + digits[3:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return "+" + digits
    if has_plus and 8 <= len(digits) <= 15:
        return "+" + digits
    return "+91" + digits if (len(digits) == 10 and digits[0] in "6789") else ("+" + digits if (8 <= len(digits) <= 15 and has_plus) else "")


def normalize_segment(value: Any, default: str = "schools") -> str:
    raw = clean_text(value, "").lower().strip()
    key = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    aliases = {
        "school": "schools", "education": "schools", "schools-education": "schools",
        "clinic": "clinics", "doctor": "clinics", "clinics-doctors": "clinics",
        "realestate": "real-estate", "real-estate-business": "real-estate",
        "retail": "retail", "ecommerce": "retail", "e-commerce": "retail",
        "agency": "agencies", "service-business": "agencies", "agencies-service-businesses": "agencies",
    }
    result = aliases.get(key, key)
    return result if result in VALID_SEGMENTS else default


def find_column(columns: list[str], aliases: list[str]) -> str | None:
    norm = {re.sub(r"[^a-z0-9]", "", c.replace("\ufeff", "").lower()): c for c in columns}
    for alias in aliases:
        k = re.sub(r"[^a-z0-9]", "", alias.lower())
        if k in norm:
            return norm[k]
    return None


def get_plivo_client() -> Any:
    missing = [k for k, v in [("PLIVO_AUTH_ID", PLIVO_AUTH_ID), ("PLIVO_AUTH_TOKEN", PLIVO_AUTH_TOKEN), ("PLIVO_PHONE_NUMBER", PLIVO_PHONE_NUMBER)] if not v]
    if missing:
        raise RuntimeError("Missing Render environment variables: " + ", ".join(missing))
    if not re.match(r"^https://[A-Za-z0-9.-]+(?::\d+)?$", PUBLIC_BASE_URL) or "localhost" in PUBLIC_BASE_URL.lower() or "127.0.0.1" in PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL must be the public HTTPS URL of this Render service.")
    if plivo is None:
        raise RuntimeError(f"plivo library is not available: {PLIVO_IMPORT_ERROR}")
    return plivo.RestClient(PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN)


def require_sales_api_key(request: Request) -> str:
    if not SALES_API_KEY:
        raise HTTPException(503, "SALES_API_KEY is not configured in Render environment")
    provided = request.headers.get("X-Admin-Key", "").strip()
    if not provided:
        raise HTTPException(401, "Unauthorized")
    tenant_keys_raw = os.getenv("TENANT_API_KEYS", "").strip()
    if tenant_keys_raw:
        for pair in tenant_keys_raw.split(","):
            if ":" in pair:
                t_id, t_key = pair.split(":", 1)
                if t_key.strip() and secrets.compare_digest(provided, t_key.strip()):
                    return re.sub(r"[^a-zA-Z0-9_-]", "", t_id.strip()) or "default"
    if not secrets.compare_digest(provided, SALES_API_KEY):
        raise HTTPException(401, "Unauthorized")
    raw_tenant = request.headers.get("X-Tenant-ID") or request.query_params.get("tenant_id") or "default"
    return re.sub(r"[^a-zA-Z0-9_-]", "", str(raw_tenant).strip()) or "default"


def _sync_persistence() -> None:
    try:
        CONTACTS.sync_from_db()
        CAMPAIGNS.sync_from_db()
        CALLBACKS.sync_from_db()
        for c in CONTACTS.values():
            if isinstance(c, dict) and c.get("phone"):
                CALLBACK_BY_PHONE[c["phone"]] = c
    except Exception as exc:
        logger.error("Failed to retrieve persisted records from database: %s", exc)
        raise HTTPException(503, f"Database retrieval error: {exc}")


def public_url(path: str) -> str:
    return f"{PUBLIC_BASE_URL}{path}"


def xml_response(body: str) -> Response:
    return Response(content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>', media_type="application/xml")


async def read_request_params(request: Request) -> dict[str, str]:
    params = {str(k): str(v) for k, v in request.query_params.items()}
    if request.method.upper() == "POST":
        try:
            form = await request.form()
            params.update({str(k): str(v) for k, v in form.items()})
        except Exception:
            pass
    return params


def _check_and_record_nonce(nonce: str) -> bool:
    if not nonce: return True
    now_ts = datetime.now(timezone.utc).timestamp()
    with NONCE_LOCK:
        for k in [k for k, ts in RECENT_NONCES.items() if now_ts - ts > 900]:
            RECENT_NONCES.pop(k, None)
        if nonce in RECENT_NONCES:
            return False
        RECENT_NONCES[nonce] = now_ts
        if len(RECENT_NONCES) > 10000:
            RECENT_NONCES.pop(next(iter(RECENT_NONCES)), None)
        return True


async def verify_plivo_webhook(request: Request, params: dict[str, str]) -> None:
    if not REQUIRE_PLIVO_SIGNATURES: return
    webhook_url = f"{PUBLIC_BASE_URL}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "")
    sigs_v3 = [x.strip() for x in (request.headers.get("X-Plivo-Signature-V3", "") + "," + request.headers.get("X-Plivo-Signature-Ma-V3", "")).split(",") if x.strip()]
    nonce_v3 = request.headers.get("X-Plivo-Signature-V3-Nonce", "")
    if nonce_v3 and not _check_and_record_nonce(nonce_v3):
        raise HTTPException(403, "Replayed Plivo webhook nonce detected")
    if sigs_v3 and nonce_v3 and PLIVO_AUTH_TOKEN:
        base = webhook_url
        if request.method.upper() == "POST" and params:
            post_items = "".join(f"{k}{params[k]}" for k in sorted(params.keys()) if k not in request.query_params)
            if post_items:
                base = f"{webhook_url}.{post_items}"
        expected = base64.b64encode(hmac.new(PLIVO_AUTH_TOKEN.encode("utf-8"), f"{base}.{nonce_v3}".encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
        if any(secrets.compare_digest(sig, expected) for sig in sigs_v3):
            return
    sigs_v2 = [x.strip() for x in (request.headers.get("X-Plivo-Signature-V2", "") + "," + request.headers.get("X-Plivo-Signature-Ma-V2", "")).split(",") if x.strip()]
    nonce_v2 = request.headers.get("X-Plivo-Signature-V2-Nonce", "")
    if sigs_v2 and nonce_v2 and PLIVO_AUTH_TOKEN:
        val = getattr(getattr(plivo, "utils", None), "validate_signature", None)
        if val and any(val(webhook_url, nonce_v2, s, PLIVO_AUTH_TOKEN) for s in sigs_v2):
            return
    raise HTTPException(403, "Invalid Plivo webhook signature")


def check_calling_hours_allowed() -> tuple[bool, str]:
    if not ENFORCE_CALLING_HOURS: return True, ""
    hour = datetime.now(timezone(timedelta(hours=5, minutes=30))).hour
    if not (CALLING_HOURS_START_IST <= hour < CALLING_HOURS_END_IST):
        return False, f"TRAI Compliance: Outbound telemarketing restricted outside {CALLING_HOURS_START_IST:02d}:00 to {CALLING_HOURS_END_IST:02d}:00 IST. Pass 'force_off_hours': true to test."
    return True, ""


async def _pace_call_dispatch() -> None:
    global _LAST_CALL_DISPATCH_TIME
    with _PACING_LOCK:
        now_ts = time.monotonic()
        wait_for = max(0.0, (_LAST_CALL_DISPATCH_TIME + CALL_PACING_DELAY_SECONDS) - now_ts)
        _LAST_CALL_DISPATCH_TIME = now_ts + wait_for
    if wait_for > 0:
        await asyncio.sleep(wait_for)


def stream_audio_file(file_path: Path, request: Request) -> Response:
    if not file_path.is_file(): raise HTTPException(404, "Audio file not found")
    total_size = file_path.stat().st_size
    headers = {"Content-Type": "audio/wav", "Accept-Ranges": "bytes", "Cache-Control": "public, max-age=86400"}
    if request.method.upper() == "HEAD":
        headers["Content-Length"] = str(total_size)
        return Response(status_code=200, headers=headers)
    range_header = request.headers.get("range")
    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            end = min(int(m.group(2)) if m.group(2) else total_size - 1, total_size - 1)
            chunk_len = end - start + 1
            headers.update({"Content-Range": f"bytes {start}-{end}/{total_size}", "Content-Length": str(chunk_len)})
            with open(str(file_path), "rb") as f:
                f.seek(start)
                return Response(f.read(chunk_len), status_code=206, headers=headers)
    headers["Content-Length"] = str(total_size)
    with open(str(file_path), "rb") as f:
        return Response(f.read(), status_code=200, headers=headers)


# ============================================================
# AUDIO SYNTHESIS & SPEECH ACTION (POLLY & GEMINI)
# ============================================================
def prepare_natural_speech_text(text: str) -> str:
    cleaned = text.strip()
    if sum(1 for ch in cleaned if "\u0900" <= ch <= "\u097f") > 3:
        return cleaned
    formatted = cleaned
    for pattern, replacement in HINDI_PHRASE_MAP:
        formatted = re.sub(pattern, replacement, formatted, flags=re.IGNORECASE)
    return formatted


def _write_gemini_wav(text: str, output_path: Path) -> None:
    if not GEMINI_API_KEY: raise RuntimeError("GEMINI_API_KEY is not configured")
    if genai is None: raise RuntimeError(f"google-genai is not installed: {GEMINI_IMPORT_ERROR}")
    client = genai.Client(api_key=GEMINI_API_KEY)
    voice = GEMINI_TTS_VOICE or "Algenib"
    is_hindi = any("\u0900" <= ch <= "\u097f" for ch in text) or any(w in text.lower().split() for w in ("namaste", "dhanyavaad", "kya", "aapko", "samajh", "hoon", "bol", "ji"))
    lang = "hi-IN" if is_hindi else "en-IN"
    style = "warm, respectful, natural professional Indian conversational Hindi, clear pronunciation of Namaste" if is_hindi else "warm, respectful, professional Indian English"
    payload_input = [{"type": "user_input", "content": [{"type": "text", "text": text, "annotations": [{"type": "speech_metadata", "language_code": lang, "style": style}]}]}]
    try:
        interaction = client.interactions.create(
            model=GEMINI_TTS_MODEL,
            input=payload_input,
            response_format={"type": "audio", "mime_type": "audio/wav"},
            generation_config={"speech_config": [{"voice": voice}]},
        )
    except Exception:
        if voice != "Algenib":
            interaction = client.interactions.create(
                model=GEMINI_TTS_MODEL,
                input=payload_input,
                response_format={"type": "audio", "mime_type": "audio/wav"},
                generation_config={"speech_config": [{"voice": "Algenib"}]},
            )
        else:
            raise
    data = getattr(getattr(interaction, "output_audio", None), "data", None)
    if not data: raise RuntimeError("Gemini returned no audio data")
    raw = base64.b64decode(data) if isinstance(data, str) else bytes(data)
    if len(raw) >= 44 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        output_path.write_bytes(raw)
    else:
        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000); wf.writeframes(raw)


async def ensure_gemini_audio(text: str) -> str | None:
    if not GEMINI_API_KEY or not text.strip(): return None
    digest = hashlib.sha256((GEMINI_TTS_MODEL + "|" + (GEMINI_TTS_VOICE or "default") + "|" + text).encode("utf-8")).hexdigest()[:40]
    path = AUDIO_DIR / f"{digest}.wav"
    if path.exists() and path.stat().st_size > 44:
        GENERATED_AUDIO_CACHE[digest] = path
        return digest
    acquired = await asyncio.to_thread(AUDIO_GENERATION_LOCK.acquire, True)
    try:
        if path.exists(): return digest
        wav_files = [f for f in AUDIO_DIR.glob("*.wav") if f.stem not in VALID_SEGMENTS]
        if len(wav_files) > MAX_AUDIO_CACHE_FILES:
            wav_files.sort(key=lambda p: p.stat().st_mtime)
            for p in wav_files[:20]:
                GENERATED_AUDIO_CACHE.pop(p.stem, None); p.unlink(missing_ok=True)
        await asyncio.to_thread(_write_gemini_wav, text, path)
        GENERATED_AUDIO_CACHE[digest] = path
        return digest
    except Exception as exc:
        logger.warning("Gemini TTS synthesis failed: %s", exc)
        path.unlink(missing_ok=True)
        return None
    finally:
        if acquired: AUDIO_GENERATION_LOCK.release()


async def _spoken_action(text: str, segment: str = "schools") -> str:
    natural_text = prepare_natural_speech_text(text)
    if os.getenv("PREFER_GEMINI_TTS_AUDIO", "true").strip().lower() == "true" and GEMINI_API_KEY:
        try:
            audio_id = await asyncio.wait_for(ensure_gemini_audio(natural_text), timeout=GEMINI_TTS_RESPONSE_TIMEOUT_SECONDS)
            if audio_id:
                return f"<Play>{_xml_escape(public_url(f'/audio/generated/{audio_id}.wav'))}</Play>"
        except Exception as exc:
            logger.warning("Gemini audio fallback to Polly.Aditi: %s", exc)
    is_hindi = any("\u0900" <= ch <= "\u097f" for ch in natural_text) or any(w in natural_text.lower().split() for w in ("namaste", "dhanyavaad", "kya", "aapko", "samajh", "hoon", "bol", "ji"))
    lang = "hi-IN" if is_hindi else "en-IN"
    return f'<Speak voice="Polly.Aditi" language="{lang}"><prosody rate="95%">{_xml_escape(natural_text)}</prosody></Speak>'


def _build_conversational_greeting(contact: dict[str, Any]) -> str:
    segment = contact.get("business_segment", "schools")
    biz = contact.get("business_name") or contact.get("school_name", "")
    lead = contact.get("name", "")
    has_name = lead and lead not in {"School Contact", "Lead Contact", "Doctor / Clinic Contact", "Contact"}
    target = biz if biz else ("school" if segment == "schools" else "business")

    if segment == "schools":
        prefix = f"नमस्ते {lead} जी! " if has_name else "नमस्ते जी! "
        return f"{prefix}मैं DigiViewer से बोल रहा हूँ। {target} के EduUPPSTAIRS School ERP और Fee CRM के बारे में क्या अभी दो मिनट बात करना convenient रहेगा?"
    elif segment == "clinics":
        prefix = f"नमस्ते Dr. {lead} जी! " if has_name else "नमस्ते जी! "
        return f"{prefix}मैं DigiViewer से बोल रहा हूँ। {target} के appointment inquiry और patient follow-up के बारे में क्या अभी दो मिनट बात करना convenient रहेगा?"
    elif segment == "real-estate":
        prefix = f"नमस्ते {lead} जी! " if has_name else "नमस्ते जी! "
        return f"{prefix}मैं DigiViewer से बोल रहा हूँ। Real estate property inquiry follow-up और lead qualification के बारे में क्या अभी दो मिनट बात करना convenient रहेगा?"
    elif segment == "retail":
        prefix = f"नमस्ते {lead} जी! " if has_name else "नमस्ते जी! "
        return f"{prefix}मैं DigiViewer से बोल रहा हूँ। Retail sales inquiry और customer follow-up के बारे में क्या अभी दो मिनट बात करना convenient रहेगा?"
    elif segment == "agencies":
        prefix = f"नमस्ते {lead} जी! " if has_name else "नमस्ते जी! "
        return f"{prefix}मैं DigiViewer से बोल रहा हूँ। Client inquiry qualification और business follow-ups के बारे में क्या अभी दो मिनट बात करना convenient रहेगा?"
    else:
        prefix = f"नमस्ते {lead} जी! " if has_name else "नमस्ते जी! "
        return f"{prefix}मैं DigiViewer से बोल रहा हूँ। क्या अभी दो मिनट बात करना convenient रहेगा, ताकि हम आपके business के लिए relevant solution समझ सकें?"


async def _conversation_xml(context_id: str, spoken_text: str, segment: str = "schools", language: str = "") -> str:
    """Speak one short turn, then collect the caller's next utterance.

    Plivo GetInput is turn-based (not full-duplex). Keep synthesis wait bounded so a
    slow TTS response cannot make the caller wait several seconds before every turn.
    """
    action = _xml_escape(public_url(f"/sales/conversation-turn?context_id={context_id}"))
    if not language:
        language = "hi-IN" if any("\u0900" <= ch <= "\u097f" for ch in spoken_text) else "en-IN"
    speech = await _spoken_action(spoken_text, segment=segment)
    fallback_text = "माफ़ कीजिए जी, आपकी आवाज़ साफ़ नहीं आई। कृपया एक बार फिर बताइए।" if language == "hi-IN" else "Sorry, I could not hear you clearly. Please say that again."
    fallback = f'<Speak voice="Polly.Aditi" language="{language}"><prosody rate="95%">{_xml_escape(fallback_text)}</prosody></Speak>'
    return f'<GetInput inputType="speech" speechModel="phone_call" action="{action}" method="POST" language="{language}" speechEndTimeout="1" executionTimeout="12">{speech}</GetInput>{fallback}<Hangup/>'


# ============================================================
# FASTAPI APP & ROUTES
# ============================================================
app = FastAPI(title="DigiViewer AI Sales Caller", version=APP_VERSION)

if DASHBOARD_DIR.exists() and (DASHBOARD_DIR / "index.html").is_file():
    app.mount("/dashboard/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="dashboard_static")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "digiviewer-ai-sales-caller", "version": APP_VERSION, "segments": list(VALID_SEGMENTS)}


@app.api_route("/audio/{kind}.wav", methods=["GET", "HEAD"], include_in_schema=False)
async def audio_file(kind: str, request: Request):
    if kind not in VALID_SEGMENTS and kind not in {"outbound", "inbound"}: raise HTTPException(404, "Audio not found")
    seg = "schools" if kind in {"outbound", "inbound"} else kind
    path = AUDIO_DIR / f"{kind}.wav"
    if not path.exists():
        await asyncio.to_thread(_write_gemini_wav, SEGMENT_ANNOUNCEMENTS.get(seg, SEGMENT_ANNOUNCEMENTS["schools"]), path)
    return stream_audio_file(path, request)


@app.api_route("/audio/generated/{audio_id}.wav", methods=["GET", "HEAD"], include_in_schema=False)
async def generated_audio_file(audio_id: str, request: Request):
    if not re.fullmatch(r"[a-f0-9]{40}", audio_id): raise HTTPException(404, "Invalid audio ID")
    path = GENERATED_AUDIO_CACHE.get(audio_id, AUDIO_DIR / f"{audio_id}.wav")
    return stream_audio_file(path, request)


# ============================================================
# CONTACT INGESTION & PERSISTENCE
# ============================================================
@app.post("/sales/import")
async def import_excel(
    request: Request,
    file: UploadFile = File(...),
    business_segment: str = "schools",
    campaign_id: str = "",
):
    tenant_id = require_sales_api_key(request)
    _sync_persistence()
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(400, "Upload .xlsx, .xls or .csv")
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    if not raw or not raw.strip():
        raise HTTPException(400, "Uploaded file is empty.")
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "Spreadsheet is too large")
    is_csv = file.filename.lower().endswith(".csv")
    if is_csv and b"\x00" in raw[:1024]:
        raise HTTPException(400, "Malformed CSV file: binary or corrupted content detected.")

    temp_path = Path("/tmp") / f"{uuid.uuid4()}-{Path(file.filename).name}"
    temp_path.write_bytes(raw)
    try:
        df = pd.read_csv(temp_path, encoding="utf-8-sig", on_bad_lines="error") if is_csv else pd.read_excel(temp_path)
    except Exception as exc1:
        try:
            df = pd.read_csv(temp_path, encoding="latin1", on_bad_lines="error") if is_csv else pd.read_excel(temp_path)
        except Exception as exc2:
            raise HTTPException(400, f"Malformed or unreadable spreadsheet file: {exc1 or exc2}")
    finally:
        temp_path.unlink(missing_ok=True)

    cols = [str(c) for c in df.columns]
    phone_col = find_column(cols, PHONE_ALIASES)
    if not phone_col:
        raise HTTPException(400, f"No phone column found. Supported: {', '.join(PHONE_ALIASES)}")
    if df.empty:
        raise HTTPException(400, "Uploaded file contains no lead rows.")

    name_col, biz_col = find_column(cols, NAME_ALIASES), find_column(cols, BUSINESS_NAME_ALIASES)
    city_col, lang_col, seg_col = find_column(cols, CITY_ALIASES), find_column(cols, LANGUAGE_ALIASES), find_column(cols, SEGMENT_ALIASES)
    dnc_col, consent_col = find_column(cols, DNC_ALIASES), find_column(cols, CONSENT_ALIASES)
    camp_col = find_column(cols, CAMPAIGN_ALIASES)

    default_seg = business_segment if business_segment in VALID_SEGMENTS else "schools"
    default_name = "School Contact" if default_seg == "schools" else ("Doctor / Clinic Contact" if default_seg == "clinics" else "Lead Contact")
    target_campaign_id = clean_text(campaign_id or request.query_params.get("campaign_id") or request.headers.get("X-Campaign-ID") or "")

    imported, invalid, seen, duplicates_skipped = [], [], set(), 0
    async with IMPORT_LOCK:
        existing = {
            (c.get("campaign_id", ""), c.get("phone"))
            for c in CONTACTS.values()
            if c.get("tenant_id", "default") == tenant_id
        }
        avail = max(0, MAX_TOTAL_CONTACTS - len(CONTACTS))
        for _, row in df.iterrows():
            raw_phone_val = clean_text(row.get(phone_col))
            phone = normalize_phone(row.get(phone_col))
            row_seg = normalize_segment(row.get(seg_col), default_seg) if seg_col and clean_text(row.get(seg_col)) else default_seg
            row_camp = clean_text(row.get(camp_col)) if camp_col and clean_text(row.get(camp_col)) else target_campaign_id
            is_dnc = (dnc_col and str(row.get(dnc_col, "")).strip().lower() in {"1", "true", "yes", "y", "dnd", "blocked"}) or \
                     (consent_col and str(row.get(consent_col, "")).strip().lower() in {"0", "false", "no", "n"})

            contact = {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "campaign_id": row_camp,
                "name": clean_text(row.get(name_col)) or default_name,
                "business_name": clean_text(row.get(biz_col)),
                "school_name": clean_text(row.get(biz_col)),
                "city": clean_text(row.get(city_col)),
                "language": clean_text(row.get(lang_col), "Hindi/Hinglish"),
                "business_segment": row_seg,
                "business_segment_label": SEGMENT_LABELS.get(row_seg, "Business"),
                "phone": phone,
                "raw_phone": raw_phone_val,
                "do_not_call": is_dnc,
                "status": "invalid" if not phone else ("opted_out" if is_dnc else "ready"),
                "error": ("" if phone else ("Missing phone number" if not raw_phone_val else f"Invalid phone number: {raw_phone_val}")),
                "created_at": now(),
            }
            if not phone:
                invalid.append(contact)
                continue
            dedup_key = (row_camp, phone)
            if dedup_key in seen or dedup_key in existing:
                duplicates_skipped += 1
                continue
            if len(imported) >= avail:
                continue
            seen.add(dedup_key)
            existing.add(dedup_key)
            CONTACTS[contact["id"]] = CALLBACK_BY_PHONE[phone] = contact
            imported.append(contact)

    tenant_contacts = [
        c for c in CONTACTS.values()
        if c.get("tenant_id", "default") == tenant_id and (not target_campaign_id or c.get("campaign_id", "") == target_campaign_id)
    ]
    return {
        "status": "imported",
        "persisted": True,
        "tenant_id": tenant_id,
        "campaign_id": target_campaign_id,
        "rows": len(df),
        "imported": len(imported),
        "invalid": len(invalid),
        "duplicates_skipped": duplicates_skipped,
        "contacts_count": len(tenant_contacts),
        "contacts": imported,
        "invalid_rows": invalid[:50],
    }


@app.post("/sales/import-batch")
async def import_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    segments: str = Form("[]"),
    campaign_id: str = Form(""),
):
    tenant_id = require_sales_api_key(request)
    if not files or len(files) > 10:
        raise HTTPException(400, "Upload 1 to 10 CSV or Excel files")
    try:
        seg_list = json.loads(segments or "[]")
    except Exception:
        seg_list = []
    target_campaign_id = clean_text(campaign_id or request.query_params.get("campaign_id") or request.headers.get("X-Campaign-ID") or "")
    results, tot_imp, tot_inv, tot_dup, all_invalid = [], 0, 0, 0, []
    for idx, f in enumerate(files):
        seg = seg_list[idx] if idx < len(seg_list) and seg_list[idx] in VALID_SEGMENTS else "schools"
        res = await import_excel(request, f, seg, campaign_id=target_campaign_id)
        results.append({
            "filename": f.filename,
            "business_segment": seg,
            "imported": res["imported"],
            "invalid": res["invalid"],
            "duplicates_skipped": res.get("duplicates_skipped", 0),
            "rows": res["rows"],
        })
        tot_imp += res["imported"]
        tot_inv += res["invalid"]
        tot_dup += res.get("duplicates_skipped", 0)
        all_invalid.extend(res.get("invalid_rows", []))
    tenant_contacts = [
        c for c in CONTACTS.values()
        if c.get("tenant_id", "default") == tenant_id and (not target_campaign_id or c.get("campaign_id", "") == target_campaign_id)
    ]
    return {
        "status": "imported",
        "persisted": True,
        "tenant_id": tenant_id,
        "campaign_id": target_campaign_id,
        "files": results,
        "imported": tot_imp,
        "invalid": tot_inv,
        "duplicates_skipped": tot_dup,
        "contacts_count": len(tenant_contacts),
        "contacts": tenant_contacts,
        "invalid_rows": all_invalid[:50],
    }


@app.get("/sales/contacts")
async def contacts(request: Request, campaign_id: str = "", business_segment: str = ""):
    tenant_id = require_sales_api_key(request)
    _sync_persistence()
    target_camp = clean_text(campaign_id or request.headers.get("X-Campaign-ID") or "")
    target_seg = normalize_segment(business_segment, "all") if business_segment and business_segment != "all" else "all"
    filtered = [
        c for c in CONTACTS.values()
        if c.get("tenant_id", "default") == tenant_id
        and (not target_camp or c.get("campaign_id", "") == target_camp)
        and (target_seg == "all" or c.get("business_segment", "schools") == target_seg)
    ]
    return {
        "count": len(filtered),
        "contacts": filtered,
        "tenant_id": tenant_id,
        "campaign_id": target_camp,
        "persisted": True,
    }


# ============================================================
# CAMPAIGN ENGINE
# ============================================================
async def place_sales_call(campaign_id: str, contact_id: str) -> None:
    campaign = CAMPAIGNS.get(campaign_id)
    if not campaign:
        return
    contact = CONTACTS.get(contact_id)
    result = campaign["results"].get(contact_id)
    if not result:
        return
    if not contact:
        result.update({"status": "failed_terminal", "error": "Contact not found", "updated_at": now()})
        CAMPAIGNS.save(campaign_id)
        return
    result["attempts"] = int(result.get("attempts", 0)) + 1
    context_id = str(uuid.uuid4())
    CALL_CONTEXTS[context_id] = {
        "context_id": context_id, "campaign_id": campaign_id, "contact_id": contact_id,
        "tenant_id": contact.get("tenant_id", "default"),
        "phone": contact["phone"], "attempt": result["attempts"], "mode": campaign.get("outbound_mode", "conversation"),
        "business_segment": contact.get("business_segment", "schools"), "contact": dict(contact),
        "history": [], "callback": False, "created_at": now(),
    }
    result.update({"status": "placing", "context_id": context_id, "updated_at": now()})
    CAMPAIGNS.save(campaign_id)
    try:
        await _pace_call_dispatch()
        client = get_plivo_client()
        resp = await asyncio.to_thread(
            client.calls.create, from_=PLIVO_PHONE_NUMBER, to_=contact["phone"],
            answer_url=public_url(f"/sales/answer?context_id={context_id}"), answer_method="GET",
            fallback_url=public_url(f"/sales/fallback?context_id={context_id}"), fallback_method="GET",
            hangup_url=public_url(f"/sales/hangup?context_id={context_id}"), hangup_method="GET",
        )
        ruuid = resp.get("request_uuid") if isinstance(resp, dict) else getattr(resp, "request_uuid", None)
        if ruuid: result["request_uuid"] = str(ruuid)
        if result.get("status") == "placing": result["status"] = "queued"
        result["updated_at"] = now()
        if campaign_id in CAMPAIGNS:
            CAMPAIGNS.save(campaign_id)
    except Exception as exc:
        err_msg = str(exc)
        logger.exception("Plivo call creation failed for %s: %s", contact["phone"], err_msg)
        result.update({"status": "failed_terminal", "error": err_msg[:300], "updated_at": now()})
        if campaign is not None and campaign_id in CAMPAIGNS:
            campaign["error"] = f"Call failed: {err_msg[:250]}"
            CAMPAIGNS.save(campaign_id)
        CALL_CONTEXTS.pop(context_id, None)


async def run_campaign(campaign_id: str) -> None:
    campaign = CAMPAIGNS.get(campaign_id)
    if not campaign:
        return
    campaign["status"], campaign["started_at"] = "running", campaign.get("started_at") or now()
    CAMPAIGNS.save(campaign_id)
    q = asyncio.Queue()
    for cid in campaign["contact_ids"]: await q.put(cid)
    retry_tasks = set()

    async def worker():
        while True:
            try: cid = q.get_nowait()
            except asyncio.QueueEmpty: return
            try:
                if campaign_id not in CAMPAIGNS:
                    return
                res = campaign["results"][cid]
                if res.get("status") not in {"completed", "failed_terminal"}:
                    res["status"] = "pending"
                    await place_sales_call(campaign_id, cid)
                    waited = 0
                    while res.get("status") in {"placing", "queued"} and waited < CALL_WAIT_TIMEOUT_SECONDS and campaign_id in CAMPAIGNS:
                        await asyncio.sleep(0.2); waited += 0.2
                    if res.get("status") == "retry_scheduled" and campaign_id in CAMPAIGNS:
                        async def delayed_requeue(c):
                            await asyncio.sleep(campaign["delay_seconds"]); await q.put(c)
                        t = asyncio.create_task(delayed_requeue(cid))
                        retry_tasks.add(t); t.add_done_callback(retry_tasks.discard)
            except Exception as e:
                if campaign_id in CAMPAIGNS:
                    campaign["results"][cid].update({"status": "failed_terminal", "error": str(e), "updated_at": now()})
                    CAMPAIGNS.save(campaign_id)
            finally:
                q.task_done()

    while True:
        workers = [worker() for _ in range(min(MAX_CONCURRENT_CALLS, max(1, q.qsize())))]
        if workers: await asyncio.gather(*workers)
        if not q.empty() or retry_tasks: await asyncio.sleep(0.1); continue
        break
    if campaign_id in CAMPAIGNS:
        campaign["status"], campaign["completed_at"] = "completed", now()
        CAMPAIGNS.save(campaign_id)


@app.post("/sales/campaigns/reset")
async def reset_campaign(request: Request):
    tenant_id = require_sales_api_key(request)
    _sync_persistence()
    async with CAMPAIGN_LOCK:
        for cid, c in CAMPAIGNS.items():
            if c.get("tenant_id", "default") == tenant_id and c.get("status") in {"queued", "running"}:
                c["status"] = "interrupted"
                c["error"] = "Reset by user"
                CAMPAIGNS.save(cid)
                task = CAMPAIGN_TASKS.pop(cid, None)
                if task:
                    task.cancel()
        for ctx_id in [k for k, v in CALL_CONTEXTS.items() if v.get("tenant_id", "default") == tenant_id]:
            CALL_CONTEXTS.pop(ctx_id, None)
    return {"ok": True, "message": "All campaigns have been reset."}


@app.post("/sales/test-call")
async def test_single_call(request: Request):
    tenant_id = require_sales_api_key(request)
    body = await request.json()
    raw_phone = body.get("phone", "")
    phone = normalize_phone(raw_phone)
    if not phone:
        raise HTTPException(400, f"Invalid phone number: '{raw_phone}'. Use a valid 10-digit or E.164 number.")
    seg = normalize_segment(body.get("business_segment"), "schools")
    mode = "announcement" if body.get("mode") == "announcement" else "conversation"
    client = get_plivo_client()

    context_id = str(uuid.uuid4())
    contact = {"id": f"test-{uuid.uuid4()}", "tenant_id": tenant_id, "name": body.get("name", "Test Contact"), "business_name": body.get("business_name", ""), "phone": phone, "business_segment": seg}
    CALL_CONTEXTS[context_id] = {
        "context_id": context_id, "campaign_id": "test-call", "contact_id": contact["id"],
        "tenant_id": tenant_id, "phone": phone, "attempt": 1, "mode": mode, "business_segment": seg,
        "contact": contact, "history": [], "callback": False, "created_at": now(),
    }
    try:
        resp = await asyncio.to_thread(
            client.calls.create, from_=PLIVO_PHONE_NUMBER, to_=phone,
            answer_url=public_url(f"/sales/answer?context_id={context_id}"), answer_method="GET",
            fallback_url=public_url(f"/sales/fallback?context_id={context_id}"), fallback_method="GET",
            hangup_url=public_url(f"/sales/hangup?context_id={context_id}"), hangup_method="GET",
        )
        ruuid = resp.get("request_uuid") if isinstance(resp, dict) else getattr(resp, "request_uuid", None)
        return {"status": "queued", "message": "Plivo accepted call request.", "request_uuid": str(ruuid or ""), "phone": phone, "segment": seg, "mode": mode}
    except Exception as exc:
        CALL_CONTEXTS.pop(context_id, None)
        logger.exception("Test call failed for %s: %s", phone, exc)
        raise HTTPException(502, f"Plivo rejected call: {str(exc)}")


@app.post("/sales/campaigns/start-all")
async def start_all_contacts(request: Request):
    tenant_id = require_sales_api_key(request)
    _sync_persistence()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    mode = "announcement" if payload.get("mode") == "announcement" else "conversation"
    raw_seg = payload.get("business_segment")
    seg = normalize_segment(raw_seg, "all") if raw_seg and raw_seg != "all" else "all"
    target_camp_id = clean_text(payload.get("campaign_id") or request.query_params.get("campaign_id") or request.headers.get("X-Campaign-ID") or "")

    if not payload.get("force_off_hours", False):
        allowed, reason = check_calling_hours_allowed()
        if not allowed:
            raise HTTPException(400, reason)

    missing_call_config = [k for k, v in [("PLIVO_AUTH_ID", PLIVO_AUTH_ID), ("PLIVO_AUTH_TOKEN", PLIVO_AUTH_TOKEN), ("PLIVO_PHONE_NUMBER", PLIVO_PHONE_NUMBER)] if not v]
    if missing_call_config:
        raise HTTPException(503, "Calling is not configured in Render. Missing: " + ", ".join(missing_call_config))
    if not PUBLIC_BASE_URL or not re.match(r"^https://[A-Za-z0-9.-]+(?::\d+)?$", PUBLIC_BASE_URL) or "localhost" in PUBLIC_BASE_URL.lower():
        raise HTTPException(503, "PUBLIC_BASE_URL must be the public HTTPS URL of this Render service.")

    async with CAMPAIGN_LOCK:
        active_camps = [
            c for c in CAMPAIGNS.values()
            if c.get("tenant_id", "default") == tenant_id
            and c.get("status") in {"queued", "running"}
            and (not target_camp_id or c.get("source_campaign_id", "") == target_camp_id)
        ]
        if active_camps:
            raise HTTPException(409, "A campaign is already running")

        tenant_contacts = [
            (cid, c) for cid, c in CONTACTS.items()
            if c.get("tenant_id", "default") == tenant_id
        ]
        if not tenant_contacts:
            raise HTTPException(400, "No callable leads found: No leads have been uploaded yet.")

        camp_contacts = [
            (cid, c) for cid, c in tenant_contacts
            if not target_camp_id or c.get("campaign_id", "") == target_camp_id
        ]
        if not camp_contacts:
            raise HTTPException(400, f"No callable leads found for campaign '{target_camp_id}'.")

        seg_contacts = [
            (cid, c) for cid, c in camp_contacts
            if seg == "all" or c.get("business_segment", "schools") == seg
        ]
        if not seg_contacts:
            avail_segs: dict[str, int] = {}
            for _, c in camp_contacts:
                s = c.get("business_segment", "schools")
                avail_segs[s] = avail_segs.get(s, 0) + 1
            avail_summary = ", ".join(f"{k} ({v})" for k, v in avail_segs.items())
            raise HTTPException(
                400,
                f"No callable leads found for the selected segment '{seg}'. "
                f"Uploaded leads ({len(camp_contacts)}) belong to: {avail_summary}. "
                f"Select 'All imported segments' or the matching segment to start calling.",
            )

        cids = [
            cid for cid, c in seg_contacts
            if normalize_phone(c.get("phone")) and not c.get("do_not_call", False)
        ]
        if not cids:
            dnc_count = sum(1 for _, c in seg_contacts if c.get("do_not_call", False))
            inv_count = sum(1 for _, c in seg_contacts if not normalize_phone(c.get("phone")))
            raise HTTPException(
                400,
                f"No callable leads found: {len(seg_contacts)} lead(s) exist for this campaign, "
                f"but none are eligible ({dnc_count} opted-out/do-not-call, {inv_count} invalid/missing phone numbers).",
            )

        camp_id = target_camp_id if (target_camp_id and target_camp_id not in CAMPAIGNS) else str(uuid.uuid4())
        CAMPAIGNS[camp_id] = {
            "id": camp_id,
            "tenant_id": tenant_id,
            "source_campaign_id": target_camp_id,
            "name": f"campaign-{seg}-{mode}",
            "business_segment": seg,
            "outbound_mode": mode,
            "status": "queued",
            "retry_count": DEFAULT_RETRY_COUNT,
            "delay_seconds": RETRY_DELAY_SECONDS,
            "max_concurrent_calls": MAX_CONCURRENT_CALLS,
            "contact_ids": cids,
            "created_at": now(),
            "started_at": None,
            "results": {cid: {"status": "pending", "attempts": 0} for cid in cids},
        }
        CAMPAIGN_TASKS[camp_id] = asyncio.create_task(run_campaign(camp_id))
        return {
            "status": "started",
            "campaign_id": camp_id,
            "source_campaign_id": target_camp_id,
            "tenant_id": tenant_id,
            "total_contacts": len(cids),
            "contact_ids": cids,
            "business_segment": seg,
            "outbound_mode": mode,
        }


# ============================================================
# TELEPHONY WEBHOOKS (ANSWER, TURN, HANGUP, CALLBACKS)
# ============================================================
@app.api_route("/sales/answer", methods=["GET", "POST"], include_in_schema=False)
async def sales_answer(context_id: str, request: Request):
    """Handle the Plivo answer webhook and begin the correct call mode."""
    context = CALL_CONTEXTS.get(context_id)
    if not context:
        raise HTTPException(404, "Call context not found")

    params = await read_request_params(request)
    await verify_plivo_webhook(request, params)
    context["call_uuid"] = params.get("CallUUID", "")
    answered_at = now()

    campaign_id = context.get("campaign_id", "")
    campaign = CAMPAIGNS.get(campaign_id)
    if campaign and isinstance(campaign.get("results"), dict):
        result = campaign["results"].setdefault(context.get("contact_id", ""), {})
        result.update({
            "answered": True,
            "answered_at": answered_at,
            "call_uuid": context["call_uuid"],
            "updated_at": answered_at,
        })
        CAMPAIGNS.save(campaign_id)

    contact_id = context.get("contact_id", "")
    stored_contact = CONTACTS.get(contact_id, {}) if contact_id else {}
    context_contact = context.get("contact") or {}
    # Prefer the saved contact, but retain the in-memory contact for test calls
    # and newly created leads that are not stored in CONTACTS.
    contact = dict(context_contact)
    if isinstance(stored_contact, dict):
        contact.update(stored_contact)

    if contact_id and contact_id in CONTACTS:
        CONTACTS[contact_id].update({
            "call_answered": True,
            "call_answered_at": answered_at,
            "last_call_status": "answered",
        })
        CONTACTS.save(contact_id)

    segment = contact.get("business_segment") or context.get("business_segment") or "schools"
    mode = str(context.get("mode") or "conversation").strip().lower()

    # Conversation is the safe default. Only explicitly selected announcement
    # mode should speak once and hang up without listening for the lead.
    if mode != "announcement":
        context["mode"] = "conversation"
        context["contact"] = contact
        context["history"] = context.get("history") or []
        context["no_input"] = 0
        context["turns"] = int(context.get("turns", 0))
        greeting = _build_conversational_greeting(contact)
        return xml_response(
            await _conversation_xml(context_id, greeting, segment=segment)
        )

    announcement = SEGMENT_ANNOUNCEMENTS.get(
        segment, SEGMENT_ANNOUNCEMENTS.get("other", SEGMENT_ANNOUNCEMENTS["schools"])
    )
    return xml_response(f"{await _spoken_action(announcement, segment=segment)}<Hangup/>")

@app.api_route("/sales/fallback", methods=["GET", "POST"], include_in_schema=False)
async def sales_fallback(context_id: str, request: Request):
    context = CALL_CONTEXTS.get(context_id)
    if not context: raise HTTPException(404, "Call context not found")
    seg = (CONTACTS.get(context.get("contact_id", ""), {})).get("business_segment", "schools")
    return xml_response(f"{await _spoken_action(SEGMENT_ANNOUNCEMENTS.get(seg, SEGMENT_ANNOUNCEMENTS['schools']), segment=seg)}<Hangup/>")


@app.api_route("/sales/inbound", methods=["GET", "POST"], include_in_schema=False)
async def inbound_callback(request: Request):
    params = await read_request_params(request)
    await verify_plivo_webhook(request, params)
    caller = normalize_phone(params.get("From") or params.get("from") or params.get("Caller") or "")
    call_uuid = params.get("CallUUID") or params.get("call_uuid") or ""
    rec_time = now()
    matched = CALLBACK_BY_PHONE.get(caller)

    cb = next((x for x in CALLBACKS if call_uuid and x.get("call_uuid") == call_uuid), None)
    if not cb:
        cb = {
            "id": str(uuid.uuid4()), "phone": caller or "unknown",
            "tenant_id": matched.get("tenant_id", "default") if matched else "default",
            "business_name": (matched.get("business_name") or matched.get("school_name", "")) if matched else "",
            "school_name": matched.get("school_name", "") if matched else "",
            "contact_name": matched.get("name", "") if matched else "",
            "business_segment": matched.get("business_segment", "other") if matched else "other",
            "city": matched.get("city", "") if matched else "", "contact_id": matched.get("id") if matched else None,
            "call_uuid": call_uuid, "status": "callback_received", "received_at": rec_time,
        }
        CALLBACKS.append(cb)
        if len(CALLBACKS) > MAX_CALLBACKS: del CALLBACKS[:-MAX_CALLBACKS]

    if matched:
        matched.update({"status": "callback_received", "callback_received_at": rec_time, "callback_call_uuid": call_uuid})
        if matched.get("id") in CONTACTS:
            CONTACTS.save(matched["id"])

    prior_ctx = cb.get("context_id")
    if prior_ctx and prior_ctx in CALL_CONTEXTS:
        return xml_response(await _conversation_xml(prior_ctx, "Hello ji, aapka callback receive hua. Main aapki kaise help kar sakta hoon?", segment=cb.get("business_segment", "other")))

    context_id = str(uuid.uuid4())
    seg = matched.get("business_segment", "other") if matched else "other"
    contact = matched or {"phone": caller, "name": "", "business_name": "", "school_name": "", "business_segment": "other"}
    CALL_CONTEXTS[context_id] = {
        "context_id": context_id, "phone": caller, "contact_id": matched.get("id") if matched else None,
        "tenant_id": matched.get("tenant_id", "default") if matched else "default",
        "contact": dict(contact), "business_segment": seg, "mode": "conversation", "callback": True,
        "history": [], "callback_id": cb["id"], "call_uuid": call_uuid, "turns": 0, "created_at": now(),
    }
    cb["context_id"] = context_id
    CALLBACKS.save_item(cb)

    if matched:
        biz = matched.get("business_name") or matched.get("school_name") or "aapke business"
        lead = matched.get("name")
        lead_part = f"{lead} जी, " if lead and lead not in {"School Contact", "Lead Contact", "Doctor / Clinic Contact", "Contact"} else ""
        if seg == "schools":
            greeting = f"नमस्ते {lead_part}{biz} के EduUPPSTAIRS School ERP और fee reminder automation के सिलसिले में callback के लिए धन्यवाद! मैं आपकी कैसे सहायता कर सकता हूँ?"
        elif seg == "clinics":
            prefix = f"Dr. {lead} जी, " if lead and "dr" not in lead.lower() else (f"{lead} जी, " if lead else "")
            greeting = f"नमस्ते {prefix}{biz} के patient appointment reminders और inquiry follow-up automation के सिलसिले में callback के लिए धन्यवाद! मैं आपकी कैसे सहायता कर सकता हूँ?"
        elif seg == "real-estate":
            greeting = f"नमस्ते {lead_part}{biz} के property buyer qualification और site visit scheduling automation के सिलसिले में callback के लिए धन्यवाद! मैं आपकी कैसे सहायता कर सकता हूँ?"
        elif seg == "retail":
            greeting = f"नमस्ते {lead_part}{biz} के sales inquiry follow-up और customer engagement automation के सिलसिले में callback के लिए धन्यवाद! मैं आपकी कैसे सहायता कर सकता हूँ?"
        elif seg == "agencies":
            greeting = f"नमस्ते {lead_part}{biz} के client lead qualification और discovery call automation के सिलसिले में callback के लिए धन्यवाद! मैं आपकी कैसे सहायता कर सकता हूँ?"
        else:
            greeting = f"नमस्ते {lead_part}{biz} से related आपके callback के लिए धन्यवाद! मैं आपकी कैसे सहायता कर सकता हूँ?"
    else:
        greeting = "नमस्ते जी! DigiViewer से बोल रहा हूँ। आपके callback के लिए धन्यवाद! आप please अपना नाम और business बताइए, ताकि मैं आपके business के according सही जानकारी दे सकूँ?"

    return xml_response(await _conversation_xml(context_id, greeting, segment=seg))


@app.api_route("/sales/conversation-turn", methods=["GET", "POST"], include_in_schema=False)
async def conversation_turn(context_id: str, request: Request):
    context = CALL_CONTEXTS.get(context_id)
    if not context:
        return xml_response(f"{await _spoken_action('Sorry ji, session khatam ho gaya hai. Dhanyavaad.')}<Hangup/>")
    params = await read_request_params(request)
    await verify_plivo_webhook(request, params)
    speech = clean_text(params.get("Speech") or params.get("speech") or params.get("Transcription") or "", "")
    seg = context.get("business_segment", "other")

    if not speech:
        context["no_input"] = int(context.get("no_input", 0)) + 1
        if context["no_input"] >= 2:
            CALL_CONTEXTS.pop(context_id, None)
            return xml_response(f"{await _spoken_action('Koi baat nahi ji. Aapka samay dene ke liye dhanyavaad. Namaste.', segment=seg)}<Hangup/>")
        return xml_response(await _conversation_xml(context_id, "Sorry ji, aapki aawaaz nahi aayi. Kya aap dobara keh sakte hain?", segment=seg))

    context["history"].append({"role": "user", "text": speech})
    contact = dict(context.get("contact") or {})
    contact["business_segment"] = seg

    try:
        from agent import generate_sales_reply
        answer = await asyncio.wait_for(
            asyncio.to_thread(generate_sales_reply, contact, context["history"][:-1], speech, "conversation", bool(context.get("callback"))),
            timeout=SALES_AI_RESPONSE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("AI generation fallback: %s", exc)
        answer = {"reply": "Samajh gaya ji. Main request note kar leta hoon aur hamari team aapse connect karegi. Dhanyavaad.", "outcome": "question_for_human"}

    reply = clean_text(answer.get("reply"), "Main aapke liye hamari team se follow-up arrange kar deta hoon.")
    context["history"].append({"role": "model", "text": reply})
    context["turns"] = int(context.get("turns", 0)) + 1

    if context.get("callback_id"):
        cb = next((x for x in CALLBACKS if x.get("id") == context["callback_id"]), None)
        if cb:
            cb.update({"status": answer.get("outcome", "callback_in_progress"), "conversation_turns": context["turns"]})
            CALLBACKS.save_item(cb)

    if answer.get("outcome") in {"do_not_call", "not_interested", "end", "wrong_person"} or context["turns"] >= 8:
        cid = contact.get("id")
        if answer.get("outcome") == "do_not_call" and cid in CONTACTS:
            CONTACTS[cid]["do_not_call"], CONTACTS[cid]["status"] = True, "opted_out"
            CONTACTS.save(cid)
        CALL_CONTEXTS.pop(context_id, None)
        return xml_response(f"{await _spoken_action(reply, segment=seg)}<Hangup/>")

    return xml_response(await _conversation_xml(context_id, reply, segment=seg))


@app.api_route("/sales/inbound-hangup", methods=["GET", "POST"], include_in_schema=False)
async def inbound_hangup(request: Request):
    params = await read_request_params(request)
    await verify_plivo_webhook(request, params)
    item = next((x for x in CALLBACKS if x.get("call_uuid") == params.get("CallUUID")), None)
    if item:
        item.update({"status": "callback_completed", "duration": params.get("Duration"), "hangup_cause": params.get("HangupCauseName"), "ended_at": now()})
        CALLBACKS.save_item(item)
        CALL_CONTEXTS.pop(item.get("context_id", ""), None)
    return {"ok": True}


@app.api_route("/sales/hangup", methods=["GET", "POST"], include_in_schema=False)
async def sales_hangup(context_id: str, request: Request):
    params = await read_request_params(request)
    await verify_plivo_webhook(request, params)
    ctx = CALL_CONTEXTS.get(context_id)
    if not ctx: return {"ok": True, "ignored": True}
    camp_id = ctx.get("campaign_id", "")
    camp = CAMPAIGNS.get(camp_id)
    res = camp.get("results", {}).get(ctx.get("contact_id", "")) if camp else None
    if not res or res.get("context_id") != context_id: return {"ok": True, "stale": True}
    status = (params.get("CallStatus") or "completed").lower()
    res.update({"telephony_status": status, "call_uuid": params.get("CallUUID") or res.get("call_uuid"), "hangup_cause": params.get("HangupCauseName"), "duration": params.get("Duration"), "updated_at": now()})
    retryable = status in {"no-answer", "busy", "timeout", "cancel"}
    res["status"] = "retry_scheduled" if retryable and int(res.get("attempts", 0)) < (1 + int(camp.get("retry_count", 0))) else ("failed_terminal" if retryable else "completed")
    CAMPAIGNS.save(camp_id)
    CALL_CONTEXTS.pop(context_id, None)
    return {"ok": True, "status": status}


@app.api_route("/sales/status", methods=["GET", "POST"], include_in_schema=False)
async def sales_status(request: Request):
    params = await read_request_params(request)
    await verify_plivo_webhook(request, params)
    return {"ok": True}


# ============================================================
# DASHBOARD & DIAGNOSTICS
# ============================================================
@app.get("/sales/diagnostics", include_in_schema=False)
async def sales_diagnostics(request: Request):
    require_sales_api_key(request)
    url_ok = bool(PUBLIC_BASE_URL and re.match(r"^https://[A-Za-z0-9.-]+(?::\d+)?$", PUBLIC_BASE_URL) and "localhost" not in PUBLIC_BASE_URL.lower())
    checks = [
        {"name": "Admin API key", "ok": bool(SALES_API_KEY), "detail": "Configured" if SALES_API_KEY else "Missing"},
        {"name": "Plivo Auth ID", "ok": bool(PLIVO_AUTH_ID), "detail": "Configured" if PLIVO_AUTH_ID else "Missing"},
        {"name": "Plivo Auth Token", "ok": bool(PLIVO_AUTH_TOKEN), "detail": "Configured" if PLIVO_AUTH_TOKEN else "Missing"},
        {"name": "Plivo caller ID", "ok": bool(PLIVO_PHONE_NUMBER), "detail": "Configured" if PLIVO_PHONE_NUMBER else "Missing"},
        {"name": "Public webhook URL", "ok": url_ok, "detail": "Valid HTTPS" if url_ok else "Invalid HTTPS URL"},
        {"name": "Gemini API key", "ok": bool(GEMINI_API_KEY), "detail": "Configured" if GEMINI_API_KEY else "Missing"},
        {"name": "Speech playback mode", "ok": True, "detail": "Polly.Aditi Indian Hindi fallback active"},
    ]
    return {"ready_for_plivo_submission": all(x["ok"] for x in checks[:5]), "checks": checks}


@app.get("/sales/dashboard-data", include_in_schema=False)
async def dashboard_data(request: Request, campaign_id: str = ""):
    tenant_id = require_sales_api_key(request)
    _sync_persistence()
    target_camp = clean_text(campaign_id or request.headers.get("X-Campaign-ID") or "")

    tenant_contacts = [
        c for c in CONTACTS.values()
        if c.get("tenant_id", "default") == tenant_id
        and (not target_camp or c.get("campaign_id", "") == target_camp)
    ]
    eligible_count = sum(1 for c in tenant_contacts if normalize_phone(c.get("phone")) and not c.get("do_not_call", False))
    segment_counts: dict[str, int] = {}
    for c in tenant_contacts:
        if normalize_phone(c.get("phone")) and not c.get("do_not_call", False):
            s = c.get("business_segment", "schools")
            segment_counts[s] = segment_counts.get(s, 0) + 1

    tenant_camps = [
        c for c in CAMPAIGNS.values()
        if c.get("tenant_id", "default") == tenant_id
        and (not target_camp or c.get("id") == target_camp or c.get("source_campaign_id") == target_camp)
    ]
    camp = next((c for c in reversed(tenant_camps) if c.get("status") in {"queued", "running"}), None) or (tenant_camps[-1] if tenant_camps else None)

    done, failed, answered = 0, 0, 0
    ans_contacts, call_rows = [], []
    if camp:
        vals = camp.get("results", {})
        done = sum(1 for r in vals.values() if r.get("status") == "completed")
        failed = sum(1 for r in vals.values() if r.get("status") == "failed_terminal")
        answered = sum(1 for r in vals.values() if r.get("answered"))
        for cid in camp.get("contact_ids", []):
            c = CONTACTS.get(cid, {})
            r = vals.get(cid, {})
            row = {
                "contact_id": cid,
                "name": c.get("name", "Contact"),
                "business_name": c.get("business_name", ""),
                "school_name": c.get("school_name", ""),
                "phone": c.get("phone", ""),
                "business_segment": c.get("business_segment", "schools"),
                "status": r.get("status", "pending"),
                "answered": bool(r.get("answered")),
                "answered_at": r.get("answered_at"),
                "error": r.get("error"),
            }
            call_rows.append(row)
            if row["answered"]:
                ans_contacts.append(row)
        camp = {
            "id": camp["id"],
            "name": camp.get("name"),
            "business_segment": camp.get("business_segment"),
            "outbound_mode": camp.get("outbound_mode"),
            "status": camp.get("status"),
            "done": done,
            "failed": failed,
            "answered": answered,
            "total": len(camp.get("contact_ids", [])),
            "error": camp.get("error"),
        }

    tenant_callbacks = [cb for cb in CALLBACKS if cb.get("tenant_id", "default") == tenant_id]
    return {
        "persisted": True,
        "tenant_id": tenant_id,
        "campaign_id": target_camp,
        "contacts_count": len(tenant_contacts),
        "eligible_count": eligible_count,
        "segment_counts": segment_counts,
        "callbacks_count": len(tenant_callbacks),
        "completed": done,
        "failed": failed,
        "answered_count": answered,
        "answered_contacts": ans_contacts,
        "call_rows": call_rows,
        "campaign": camp,
        "contacts": tenant_contacts,
        "callbacks": tenant_callbacks[-100:],
    }


@app.get("/sales/callbacks", summary="VIEW CALLBACKS")
async def callbacks(request: Request):
    tenant_id = require_sales_api_key(request)
    _sync_persistence()
    tenant_callbacks = [cb for cb in CALLBACKS if cb.get("tenant_id", "default") == tenant_id]
    return {"count": len(tenant_callbacks), "callbacks": tenant_callbacks}


@app.get("/sales/campaigns/{campaign_id}", include_in_schema=False)
async def campaign_detail(campaign_id: str, request: Request):
    tenant_id = require_sales_api_key(request)
    _sync_persistence()
    c = CAMPAIGNS.get(campaign_id)
    if not c or c.get("tenant_id", "default") != tenant_id:
        raise HTTPException(404, "Campaign not found")
    return c


# ============================================================
# EDUUPPSTAIRS FEE-DEMO WORKSPACE
# ============================================================
@app.post("/sales/fee-demos", include_in_schema=False)
async def create_fee_demo(request: Request):
    require_sales_api_key(request)
    try: body = await request.json()
    except Exception: body = {}
    school = clean_text(body.get("school_name"), "Seven Stars Academy") or "Seven Stars Academy"
    token = secrets.token_urlsafe(32)
    students = [{"student_id": f"SSA-{i:03d}", "name": f"Demo Student {i:02d}", "class_name": f"Class {((i - 1) % 10) + 1}", "due": 4000, "paid": 0 if i % 4 == 0 else (2000 if i % 4 == 1 else (4000 if i % 4 == 2 else 1000)), "balance": 4000 - (0 if i % 4 == 0 else (2000 if i % 4 == 1 else (4000 if i % 4 == 2 else 1000))), "parent_phone": ""} for i in range(1, 51)]
    FEE_DEMOS[token] = {"school_name": school, "created_at": datetime.now(timezone.utc), "expires_at": datetime.now(timezone.utc) + timedelta(hours=FEE_DEMO_HOURS), "students": students, "payments": []}
    return {"school_name": school, "url": public_url(f"/fees-demo/{token}"), "expires_at": FEE_DEMOS[token]["expires_at"].isoformat(), "duration_hours": FEE_DEMO_HOURS, "students": len(students)}


def _get_fee_demo(token: str) -> dict[str, Any]:
    demo = FEE_DEMOS.get(token)
    if not demo or datetime.now(timezone.utc) >= demo["expires_at"]:
        FEE_DEMOS.pop(token, None)
        raise HTTPException(410 if demo else 404, "Demo link is invalid or expired.")
    return demo


@app.get("/fees-demo/{token}", include_in_schema=False)
async def fee_demo_page(token: str):
    _get_fee_demo(token)
    html = _FEE_DEMO_HTML.replace('id="templateLink" class="btn" href="#"', f'id="templateLink" class="btn" href="/fees-demo/api/{token}/template"')
    return Response(content=html, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/fees-demo/api/{token}/data", include_in_schema=False)
async def fee_demo_data(token: str):
    demo = _get_fee_demo(token)
    s = demo["students"]
    return {"school_name": demo["school_name"], "expires_at": demo["expires_at"].isoformat(), "students": s, "payments": demo["payments"], "summary": {"expected": sum(x["due"] for x in s), "collected": sum(x["paid"] for x in s), "outstanding": sum(x["balance"] for x in s)}}


@app.get("/fees-demo/api/{token}/template", include_in_schema=False)
async def fee_demo_template(token: str):
    _get_fee_demo(token)
    return Response(content="student_id,name,class_name,due,paid,parent_phone\nSSA-001,Student Example,Class 1,4000,0,+919876543210\n", media_type="text/csv", headers={"Content-Disposition": "attachment; filename=fee_upload_template.csv"})


@app.post("/fees-demo/api/{token}/preview-import", include_in_schema=False)
async def fee_demo_preview_import(token: str, file: UploadFile = File(...)):
    _get_fee_demo(token)
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024: raise HTTPException(413, "Maximum size is 5 MB.")
    try:
        df = pd.read_csv(io.BytesIO(raw), dtype=str) if file.filename.lower().endswith(".csv") else pd.read_excel(io.BytesIO(raw), dtype=str)
    except Exception as exc: raise HTTPException(400, f"Cannot read spreadsheet: {exc}")

    aliases = {str(c).strip().lower().replace(" ", "_"): c for c in df.columns}
    def col(*names): return next((aliases[n] for n in names if n in aliases), None)
    sid, nm, cls = col("student_id", "studentid", "id"), col("name", "student_name"), col("class_name", "class", "grade")
    due_col, paid_col, ph_col = col("due", "fee", "total_fee"), col("paid", "amount_paid", "collected"), col("parent_phone", "phone", "mobile")
    if not (sid and nm and cls and due_col and paid_col): raise HTTPException(400, "Missing required columns: student_id, name, class_name, due, paid")

    rows, seen, errors = [], set(), 0
    for _, row in df.iterrows():
        try:
            s_id, name, c_name = clean_text(row.get(sid)), clean_text(row.get(nm)), clean_text(row.get(cls))
            due = float(str(row.get(due_col, "0")).replace(",", "").replace("₹", "").strip())
            paid = float(str(row.get(paid_col, "0")).replace(",", "").replace("₹", "").strip())
            if not due.is_integer() or not paid.is_integer() or due < 0 or paid < 0 or paid > due or not (s_id and name and c_name) or s_id.lower() in seen: raise ValueError()
            seen.add(s_id.lower())
            rows.append({"student_id": s_id, "name": name, "class_name": c_name, "parent_phone": normalize_phone(row.get(ph_col, "")), "due": int(due), "paid": int(paid), "balance": int(due - paid), "error": ""})
        except Exception:
            errors += 1
            rows.append({"student_id": clean_text(row.get(sid)), "name": clean_text(row.get(nm)), "class_name": clean_text(row.get(cls)), "due": 0, "paid": 0, "balance": 0, "error": "Invalid values or paid exceeds fee"})

    import_id = secrets.token_urlsafe(18)
    valid = errors == 0 and bool(rows) and len(rows) <= 5000
    FEE_IMPORT_PREVIEWS[import_id] = {"token": token, "rows": [{k: v for k, v in r.items() if k != "error"} for r in rows] if valid else [], "valid": valid, "created_at": datetime.now(timezone.utc)}
    return {"import_id": import_id, "valid": valid, "valid_rows": len(rows) - errors, "errors": errors, "rows": rows[:100]}


@app.post("/fees-demo/api/{token}/confirm-import", include_in_schema=False)
async def fee_demo_confirm_import(token: str, request: Request):
    demo = _get_fee_demo(token)
    try: body = await request.json(); iid = str(body.get("import_id", ""))
    except Exception: raise HTTPException(400, "Invalid payload")
    prev = FEE_IMPORT_PREVIEWS.pop(iid, None)
    if not prev or prev.get("token") != token or not prev.get("valid"): raise HTTPException(400, "Import preview invalid or expired.")
    demo["students"], demo["payments"] = prev["rows"], []
    return {"imported": len(demo["students"])}


@app.get("/fees-demo/api/{token}/calling-status", include_in_schema=False)
async def fee_demo_calling_status(token: str):
    demo = _get_fee_demo(token)
    valid_nums = sum(1 for s in demo["students"] if normalize_phone(s.get("parent_phone")))
    calls_used = len(demo.get("call_log", []))
    configured = bool(PLIVO_AUTH_ID and PLIVO_AUTH_TOKEN and PLIVO_PHONE_NUMBER and PUBLIC_BASE_URL.startswith("https://"))
    return {"configured": configured, "parent_numbers_available": valid_nums, "calls_used": calls_used, "calls_limit": FEE_DEMO_CALL_LIMIT, "ready": configured and valid_nums > 0 and calls_used < FEE_DEMO_CALL_LIMIT}


@app.post("/fees-demo/api/{token}/call-parent", include_in_schema=False)
async def fee_demo_call_parent(token: str, request: Request):
    demo = _get_fee_demo(token)
    try: body = await request.json()
    except Exception: raise HTTPException(400, "Invalid payload")
    if body.get("consent_confirmed") is not True: raise HTTPException(400, "Consent required")
    calls = demo.setdefault("call_log", [])
    if len(calls) >= FEE_DEMO_CALL_LIMIT: raise HTTPException(429, "Demo call limit reached")
    s = next((x for x in demo["students"] if x.get("student_id") == str(body.get("student_id"))), None)
    if not s or not normalize_phone(s.get("parent_phone")): raise HTTPException(400, "Student has no valid phone")
    client = get_plivo_client()
    call_id = secrets.token_urlsafe(24)
    ans_url = public_url(f"/fees-demo/call-answer/{call_id}")
    FEE_CALL_CONTEXTS[call_id] = {"token": token, "name": s["name"], "balance": s["balance"]}
    resp = await asyncio.to_thread(client.calls.create, from_=PLIVO_PHONE_NUMBER, to_=s["parent_phone"], answer_url=ans_url, answer_method="GET")
    ruuid = resp.get("request_uuid", "") if isinstance(resp, dict) else getattr(resp, "request_uuid", "")
    calls.append({"student_id": s["student_id"], "status": "queued", "request_uuid": str(ruuid)})
    return {"status": "queued", "calls_used": len(calls), "request_uuid": str(ruuid)}


@app.api_route("/fees-demo/call-answer/{call_id}", methods=["GET", "POST"], include_in_schema=False)
async def fee_demo_call_answer(call_id: str, request: Request):
    ctx = FEE_CALL_CONTEXTS.get(call_id)
    if not ctx: raise HTTPException(404, "Call context not found")
    demo = FEE_DEMOS.get(ctx["token"])
    if not demo or datetime.now(timezone.utc) >= demo["expires_at"]:
        return xml_response(f"{await _spoken_action('This demo has expired.')}<Hangup/>")
    speech = f"नमस्ते। यह {demo['school_name']} की ओर से फीस रिमाइंडर है। {ctx['name']} की फीस में {int(ctx['balance'])} रुपये बाकी दर्ज हैं। धन्यवाद।"
    return xml_response(f"{await _spoken_action(speech, segment='schools')}<Hangup/>")


@app.api_route("/fees-demo/call-status/{call_id}", methods=["GET", "POST"], include_in_schema=False)
async def fee_demo_call_status(call_id: str, request: Request):
    return {"ok": True}


@app.post("/fees-demo/api/{token}/payments", include_in_schema=False)
async def fee_demo_payment(token: str, request: Request):
    demo = _get_fee_demo(token)
    try: body = await request.json(); sid, amt = str(body.get("student_id")), float(body.get("amount", 0))
    except Exception: raise HTTPException(400, "Invalid payment")
    if not amt.is_integer() or amt <= 0: raise HTTPException(400, "Enter positive amount")
    s = next((x for x in demo["students"] if x["student_id"] == sid), None)
    if not s or amt > s["balance"]: raise HTTPException(400, "Payment exceeds balance")
    s["paid"] += int(amt); s["balance"] -= int(amt)
    p = {"receipt_no": f"DEMO-{datetime.now(timezone.utc).strftime('%y%m%d')}-{secrets.token_hex(3).upper()}", "student_id": sid, "student_name": s["name"], "amount": int(amt), "method": clean_text(body.get("method"), "Cash"), "note": clean_text(body.get("note")), "created_at": now()}
    demo["payments"].append(p)
    return {"payment": p, "student": s}


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
@app.get("/dashboard/", include_in_schema=False)
async def dashboard_view():
    for p in [DASHBOARD_DIR / "index.html", ROOT_INDEX_HTML]:
        if p.is_file(): return Response(content=p.read_text(encoding="utf-8"), media_type="text/html", headers={"Cache-Control": "no-store"})
    return Response(content="<html><body><h1>DigiViewer Sales Desk</h1></body></html>", media_type="text/html")


def main() -> None:
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="info")


if __name__ == "__main__":
    main()