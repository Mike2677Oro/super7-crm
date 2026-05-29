"""
ontraport.py — Super7 CRM
Integración con Ontraport. Fuente única de verdad para tags y contactos.

FLUJO (según documentación oficial api.ontraport.com/doc):
  1. POST /Contacts/saveorupdate  → crear/actualizar contacto → contact_id
     Content-Type: application/x-www-form-urlencoded
     Payload: URL-encoded form data

  2. GET  /Tags?search=<nombre>   → resolver tag_name → tag_id numérico
     La respuesta tiene {"data": [{"tag_id": "4", "tag_name": "push2_mdz", ...}]}

  3. PUT  /objects/tag            → aplicar tag al contacto
     Content-Type: application/x-www-form-urlencoded
     Payload: objectID=<contact_id>&add_list=<tag_id>

LÓGICA DE TAGS:
  0 cargas        → push0_<jur>
  1 carga         → push1_<jur>
  2 cargas        → push2_<jur>
  3 cargas        → push3_<jur>
  4+ cargas       → push4_<jur>
  jur CABA → caba | jur MDZ/Mendoza → mdz
"""

import logging
import os
import requests

# ─── Credenciales ──────────────────────────────────────────────────────────────

ONTRAPORT_API_KEY = os.environ.get("ONTRAPORT_API_KEY", "6RdAMjA2dHj57Rf")
ONTRAPORT_APP_ID  = os.environ.get("ONTRAPORT_APP_ID",  "2_234387_4mGjbnJ3e")
BASE_URL          = "https://api.ontraport.com/1"
TIMEOUT           = 15

# Headers base — sin Content-Type, requests lo setea solo según el método
BASE_HEADERS = {
    "Api-Key":    ONTRAPORT_API_KEY,
    "Api-Appid":  ONTRAPORT_APP_ID,
}

# Headers para requests con body (POST/PUT)
FORM_HEADERS = {
    **BASE_HEADERS,
    "Content-Type": "application/x-www-form-urlencoded",
}

# ─── Logger ────────────────────────────────────────────────────────────────────

logger = logging.getLogger("ontraport")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] ONTRAPORT — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ─── Caché de tag_ids ──────────────────────────────────────────────────────────

_tag_id_cache: dict[str, int] = {}

# ─── Lógica de tags ────────────────────────────────────────────────────────────

def compute_tag(jugador: dict) -> str | None:
    """
    Calcula el tag correcto para un jugador.
      0 cargas → push0_<jur> | 1 → push1 | 2 → push2 | 3 → push3 | 4+ → push4
      jur CABA → caba | jur MDZ/Mendoza → mdz
    """
    if not jugador:
        return None

    jur_raw = str(jugador.get("jurisdiccion") or "").strip().upper()
    if jur_raw == "CABA":
        jur = "caba"
    elif jur_raw in ("MDZ", "MENDOZA"):
        jur = "mdz"
    else:
        logger.warning("compute_tag: jurisdicción desconocida '%s' nrodoc=%s",
                       jur_raw, jugador.get("nrodoc"))
        return None

    level = min(int(jugador.get("total_cargas") or 0), 4)
    tag   = f"push{level}_{jur}"
    logger.info("compute_tag: nrodoc=%s cargas=%s jur=%s → %s",
                jugador.get("nrodoc"), jugador.get("total_cargas"), jur, tag)
    return tag


# ─── PASO 1: Crear / actualizar contacto ───────────────────────────────────────

def create_or_update_contact(jugador: dict) -> dict:
    """
    POST /Contacts/saveorupdate
    Content-Type: application/x-www-form-urlencoded
    Deduplica por email. Retorna {"ok": True, "contact_id": int}
    """
    email = str(jugador.get("email") or "").strip()
    if not email:
        msg = f"nrodoc={jugador.get('nrodoc')} sin email — no se puede crear contacto"
        logger.warning(msg)
        return {"ok": False, "message": msg}

    # Payload como dict — requests lo codifica como form-urlencoded
    payload = {
        "email":      email,
        "firstname":  str(jugador.get("nombre")   or "").strip(),
        "lastname":   str(jugador.get("apellido")  or "").strip(),
        "sms_number": str(jugador.get("telefono")  or "").strip(),
    }
    # Limpiar valores vacíos para no pisar datos existentes
    payload = {k: v for k, v in payload.items() if v}

    logger.info("PASO 1 — saveorupdate: nrodoc=%s email=%s",
                jugador.get("nrodoc"), email)

    try:
        r = requests.post(
            f"{BASE_URL}/Contacts/saveorupdate",
            headers=FORM_HEADERS,
            data=payload,       # ← form-urlencoded, NO json=
            timeout=TIMEOUT,
        )
    except requests.exceptions.Timeout:
        msg = "Timeout en /Contacts/saveorupdate"
        logger.error(msg)
        return {"ok": False, "message": msg}
    except requests.exceptions.RequestException as e:
        msg = f"Error de red /Contacts/saveorupdate: {e}"
        logger.error(msg)
        return {"ok": False, "message": msg}

    logger.info("PASO 1 — HTTP %d | %s", r.status_code, r.text[:400])

    if r.status_code not in (200, 201):
        msg = f"/Contacts/saveorupdate HTTP {r.status_code}: {r.text[:300]}"
        logger.error(msg)
        return {"ok": False, "message": msg}

    try:
        body = r.json()
    except ValueError:
        msg = f"Respuesta no-JSON: {r.text[:200]}"
        logger.error(msg)
        return {"ok": False, "message": msg}

    # Extraer contact_id — Ontraport puede devolver en distintos niveles
    data = body.get("data") or {}
    if isinstance(data, list) and data:
        data = data[0]

    contact_id = (
        data.get("id")
        or data.get("contact_id")
        or (data.get("attrs") or {}).get("id")
        or body.get("id")
    )

    if not contact_id:
        msg = f"Ontraport no devolvió contact_id. Body: {body}"
        logger.error(msg)
        return {"ok": False, "message": msg}

    contact_id = int(contact_id)
    logger.info("PASO 1 — OK contact_id=%d email=%s", contact_id, email)
    return {"ok": True, "contact_id": contact_id}


# ─── PASO 2: Resolver tag_name → tag_id numérico ───────────────────────────────

def get_tag_id(tag_name: str) -> int | None:
    """
    GET /Tags?search=<tag_name>
    Busca el tag por nombre exacto. Usa caché para no repetir por jugador.
    Retorna tag_id numérico o None si no existe.
    """
    tag_name = tag_name.strip().lower()

    if tag_name in _tag_id_cache:
        logger.info("PASO 2 — caché hit: %s → %d", tag_name, _tag_id_cache[tag_name])
        return _tag_id_cache[tag_name]

    logger.info("PASO 2 — buscando tag '%s' en Ontraport", tag_name)

    try:
        r = requests.get(
            f"{BASE_URL}/Tags",
            headers=BASE_HEADERS,
            params={"search": tag_name, "range": 50},
            timeout=TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        logger.error("PASO 2 — error de red: %s", e)
        return None

    logger.info("PASO 2 — HTTP %d | %s", r.status_code, r.text[:400])

    if r.status_code != 200:
        logger.error("PASO 2 — GET /Tags HTTP %d: %s", r.status_code, r.text[:200])
        return None

    try:
        body = r.json()
    except ValueError:
        logger.error("PASO 2 — respuesta no-JSON: %s", r.text[:200])
        return None

    items = body.get("data") or []
    if isinstance(items, dict):
        items = [items]

    for item in items:
        name = str(item.get("tag_name") or "").strip().lower()
        tid  = item.get("tag_id")
        if name == tag_name and tid:
            tag_id = int(tid)
            _tag_id_cache[tag_name] = tag_id
            logger.info("PASO 2 — OK: '%s' → tag_id=%d", tag_name, tag_id)
            return tag_id

    logger.warning(
        "PASO 2 — tag '%s' no encontrado. Tags disponibles: %s",
        tag_name,
        [i.get("tag_name") for i in items]
    )
    return None


# ─── PASO 3: Aplicar tag al contacto ───────────────────────────────────────────

def apply_tag(contact_id: int, tag_id: int, tag_name: str) -> dict:
    """
    PUT /objects/tag
    Content-Type: application/x-www-form-urlencoded
    Aplica el tag por ID numérico. Ontraport dispara la automation asociada.
    """
    # Según docs oficiales (curl example): objectID=0&add_list=3&ids=2
    # objectID = tipo de objeto (0 = Contacts, siempre)
    # ids      = contact_id del jugador
    # add_list = tag_id numérico
    payload = {
        "objectID": "0",           # 0 = tipo Contact
        "ids":      str(contact_id),
        "add_list": str(tag_id),
    }

    logger.info("PASO 3 — apply_tag: contact_id=%d tag_id=%d tag_name=%s payload=%s",
                contact_id, tag_id, tag_name, payload)

    try:
        r = requests.put(
            f"{BASE_URL}/objects/tag",
            headers=FORM_HEADERS,
            data=payload,
            timeout=TIMEOUT,
        )
    except requests.exceptions.Timeout:
        msg = f"Timeout aplicando tag '{tag_name}'"
        logger.error(msg)
        return {"ok": False, "message": msg}
    except requests.exceptions.RequestException as e:
        msg = f"Error de red apply_tag: {e}"
        logger.error(msg)
        return {"ok": False, "message": msg}

    logger.info("PASO 3 — HTTP %d | %s", r.status_code, r.text[:400])

    if r.status_code not in (200, 201):
        msg = f"/objects/tag HTTP {r.status_code}: {r.text[:300]}"
        logger.error(msg)
        return {"ok": False, "message": msg}

    logger.info("PASO 3 — OK: tag '%s' (id=%d) → contact_id=%d", tag_name, tag_id, contact_id)
    return {"ok": True}


# ─── FUNCIÓN PÚBLICA ───────────────────────────────────────────────────────────

def tag_contact(jugador: dict, tag: str | None = None) -> dict:
    """
    Flujo completo:
      1. POST /Contacts/saveorupdate → contact_id
      2. GET  /Tags?search=<name>   → tag_id
      3. PUT  /objects/tag          → aplicar tag → Ontraport dispara automation

    Args:
        jugador: dict del jugador desde la DB
        tag:     nombre del tag (si None, se calcula con compute_tag)

    Retorna:
        {"ok": True,  "contact_id": int, "tag": str, "tag_id": int}
        {"ok": False, "message": str}
    """
    nrodoc = jugador.get("nrodoc", "?")

    tag_name = (tag.strip().lower() if tag else None) or compute_tag(jugador)
    if not tag_name:
        msg = (f"No se pudo determinar tag: nrodoc={nrodoc} "
               f"jur={jugador.get('jurisdiccion')} cargas={jugador.get('total_cargas')}")
        logger.error(msg)
        return {"ok": False, "message": msg}

    logger.info("tag_contact: INICIO nrodoc=%s → tag=%s", nrodoc, tag_name)

    # Paso 1
    r1 = create_or_update_contact(jugador)
    if not r1.get("ok"):
        return {"ok": False, "message": r1["message"]}
    contact_id = r1["contact_id"]

    # Paso 2
    tag_id = get_tag_id(tag_name)
    if tag_id is None:
        msg = (f"Tag '{tag_name}' no existe en Ontraport. "
               f"Crealo primero en Ontraport > Admin > Tags.")
        logger.error(msg)
        return {"ok": False, "message": msg}

    # Paso 3
    r3 = apply_tag(contact_id, tag_id, tag_name)
    if not r3.get("ok"):
        return {"ok": False, "message": r3["message"]}

    logger.info("tag_contact: ÉXITO nrodoc=%s contact_id=%d tag=%s tag_id=%d",
                nrodoc, contact_id, tag_name, tag_id)
    return {"ok": True, "contact_id": contact_id, "tag": tag_name, "tag_id": tag_id}
