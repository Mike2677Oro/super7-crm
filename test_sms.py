import integrations.emblue as emblue

class FakeResponse:
    def __init__(self, status_code=200, text='{"ok":true}'):
        self.status_code = status_code
        self.text = text
    def json(self):
        return {"ok": True}


def fake_post(url, json=None, headers=None, timeout=None):
    print("FAKE requests.post called:", url)
    print("payload:", json)
    return FakeResponse(200, '{"ok":true}')

# Monkeypatch requests.post used by emblue
emblue.requests.post = fake_post

jugador = {"telefono": "5491123456789", "nombre": "Prueba", "email": "test@example.com"}
ok = emblue.trigger_sms_flow(jugador, flow="default")
print("trigger_sms_flow returned:", ok)
