# Super7 CRM — App Flask local

## Instalación (5 minutos)

```bash
# 1. Crear entorno virtual en VS Code (terminal integrada)
python -m venv venv
venv\Scripts\activate        # Windows

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Copiar los módulos del CRM (core/ y campaigns/) a esta carpeta
#    La estructura final debe quedar así:
#
#    super7_flask/
#    ├── server.py
#    ├── requirements.txt
#    ├── credentials.json       ← de Google Cloud
#    ├── config.py              ← con tus API keys
#    ├── templates/
#    │   └── index.html
#    ├── core/
│    │   ├── sheets.py
#    │   ├── ontraport.py
#    │   └── emblue.py
#    └── campaigns/
#        └── orchestrator.py

# 4. Correr la app
python server.py
```

## Abrir en el navegador

```
http://localhost:5000
```

## Activar datos reales (quitar el modo demo)

En `server.py`, descomentar las líneas al inicio:

```python
from core.sheets import leer_jugadores, filtrar_elegibles_caba
from campaigns.orchestrator import campaña_ftd, ...
```

Y en cada endpoint (`/api/stats`, `/api/jugadores`, etc.)
reemplazar `JUGADORES_DEMO` por `leer_jugadores()`.

## Pantallas disponibles

| Pantalla    | Qué hace                                              |
|-------------|-------------------------------------------------------|
| Panel       | Métricas, segmentos, gráfico de inactividad, alertas  |
| Jugadores   | Filtros, tabla completa, exportar CSV                 |
| Campañas    | Editar mensajes SMS, vista previa, ejecutar/simular   |
