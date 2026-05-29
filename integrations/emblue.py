import requests

EMBLUE_URL = "https://api.embluemail.com/v2.3/integrations/35497/execute/es9qIONUOjRqSbsvey+fZoNnKFUO1eDzN2uIiBBpD2PJod9bMNfP3GyBk1bmhB4hAFM2Vxyq/85yo7+GVaJcbM4Xx1V7QA=="

def trigger_sms_flow(jugador, flow="default"):
    payload = {
        "telefono": jugador.get("telefono", ""),
        "nombre": jugador.get("nombre", ""),
        "email": jugador.get("email", ""),
        "flow": flow
    }

    try:
        r = requests.post(
            EMBLUE_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15
        )

        if r.status_code not in (200, 201):
            return {"ok": False, "message": f"emBlue failed ({r.status_code}): {r.text}"}

        return {"ok": True, "message": r.text}

    except Exception as e:
        return {"ok": False, "message": str(e)}
