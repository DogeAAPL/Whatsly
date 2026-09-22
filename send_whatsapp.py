"""
Envío semanal automatizado de WhatsApp (WhatsApp Cloud API - Meta)

Lógica:
  - Cada contacto tiene una CARPETA (en attachments/<contact_key>/) donde
    tú arrastras, cada semana, las fotos y/o videos que quieras enviarle.
  - Si esa carpeta está VACÍA esa semana -> el script NO envía absolutamente
    nada a ese contacto (ni texto, ni archivo). Se salta por completo.
  - Si tiene uno o más archivos -> se envía un mensaje de plantilla por
    cada archivo (imagen, video o documento), con el texto de "message"
    como cuerpo.

Por qué un mensaje por archivo: en WhatsApp Cloud API cada mensaje de
plantilla solo admite UN adjunto en el header, y el tipo de header
(imagen / video / documento) debe coincidir con una plantilla ya aprobada
para ese tipo. Por eso necesitas 3 plantillas aprobadas (una por tipo de
adjunto) — ver README, sección "Plantillas".

Variables de entorno requeridas (GitHub Secrets):
  WHATSAPP_TOKEN            -> token permanente (System User)
  WHATSAPP_PHONE_ID         -> Phone Number ID de tu número conectado
  WHATSAPP_LANG             -> idioma de las plantillas (ej: "es")
  WHATSAPP_TEMPLATE_IMAGE   -> nombre de la plantilla con header de imagen
  WHATSAPP_TEMPLATE_VIDEO   -> nombre de la plantilla con header de video
  WHATSAPP_TEMPLATE_DOC     -> nombre de la plantilla con header de documento
"""

import json
import os
import sys
from pathlib import Path

import requests

API_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{API_VERSION}"

TOKEN = os.environ["WHATSAPP_TOKEN"]
PHONE_ID = os.environ["WHATSAPP_PHONE_ID"]
TEMPLATE_LANG = os.environ.get("WHATSAPP_LANG", "es")

TEMPLATES = {
    "image": os.environ.get("WHATSAPP_TEMPLATE_IMAGE", "recordatorio_semanal_imagen"),
    "video": os.environ.get("WHATSAPP_TEMPLATE_VIDEO", "recordatorio_semanal_video"),
    "document": os.environ.get("WHATSAPP_TEMPLATE_DOC", "recordatorio_semanal_documento"),
}

HEADERS = {"Authorization": f"Bearer {TOKEN}"}

BASE_DIR = Path(__file__).parent
CONTACTS_FILE = BASE_DIR / "contacts.json"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".3gp"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}

MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".mp4": "video/mp4", ".3gp": "video/3gpp",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def classify(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in DOC_EXT:
        return "document"
    return None  # tipo no soportado, se ignora


def list_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    # ignora archivos ocultos como .gitkeep
    return sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))


def upload_media(file_path: Path, media_type: str) -> str:
    url = f"{GRAPH_URL}/{PHONE_ID}/media"
    mime_type = MIME_BY_EXT.get(file_path.suffix.lower(), "application/octet-stream")

    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, mime_type)}
        data = {"messaging_product": "whatsapp", "type": mime_type}
        resp = requests.post(url, headers=HEADERS, data=data, files=files, timeout=120)

    resp.raise_for_status()
    media_id = resp.json()["id"]
    print(f"    Subido: {file_path.name} ({media_type}) -> media_id={media_id}")
    return media_id


def build_payload(contact: dict, media_type: str, media_id: str, file_path: Path) -> dict:
    header_param = {"type": media_type, media_type: {"id": media_id}}
    if media_type == "document":
        header_param["document"]["filename"] = file_path.name

    return {
        "messaging_product": "whatsapp",
        "to": contact["phone"],
        "type": "template",
        "template": {
            "name": TEMPLATES[media_type],
            "language": {"code": TEMPLATE_LANG},
            "components": [
                {"type": "header", "parameters": [header_param]},
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": contact["name"]},
                        {"type": "text", "text": contact.get("message", "")},
                    ],
                },
            ],
        },
    }


def send_message(payload: dict) -> None:
    url = f"{GRAPH_URL}/{PHONE_ID}/messages"
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)

    if resp.status_code == 200:
        msg_id = resp.json()["messages"][0]["id"]
        print(f"    Enviado OK -> id={msg_id}")
    else:
        print(f"    ERROR ({resp.status_code}): {resp.text}")


def main() -> int:
    if not CONTACTS_FILE.exists():
        print(f"No se encontró {CONTACTS_FILE}")
        return 1

    contacts = json.loads(CONTACTS_FILE.read_text(encoding="utf-8"))

    had_error = False
    for contact in contacts:
        folder = BASE_DIR / contact["folder"]
        files = list_files(folder)

        print(f"\nContacto: {contact['name']} ({contact['phone']}) - carpeta: {contact['folder']}")

        if contact.get("active", True) is False:
            print("  Contacto pausado desde la app -> no se envía nada esta semana.")
            continue

        if not files:
            print("  Carpeta vacía esta semana -> no se envía nada a este contacto.")
            continue

        for file_path in files:
            media_type = classify(file_path)
            if media_type is None:
                print(f"  Aviso: tipo de archivo no soportado, se ignora: {file_path.name}")
                continue

            print(f"  Procesando {file_path.name} como '{media_type}'")
            try:
                media_id = upload_media(file_path, media_type)
                payload = build_payload(contact, media_type, media_id, file_path)
                send_message(payload)
            except Exception as exc:  # noqa: BLE001
                had_error = True
                print(f"    Excepción con {file_path.name}: {exc}")

    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
