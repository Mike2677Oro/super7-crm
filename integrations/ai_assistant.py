"""
ai_assistant.py — Super7 CRM
Asistente de IA con OpenAI function calling.

El agente tiene acceso a todas las herramientas del CRM:
- Consultar jugadores, churn, campañas, métricas, stats
- Exportar reportes en Excel
- Ejecutar tags en Ontraport
- Analizar segmentos y recomendar acciones

Flujo:
  1. Usuario hace una pregunta en lenguaje natural
  2. OpenAI decide qué tool(s) llamar
  3. El backend ejecuta las tools con datos reales de la DB
  4. OpenAI genera una respuesta estructurada con los datos
  5. El frontend renderiza la respuesta (tablas, gráficos, botones de acción)
"""

import json
import logging
import os
import io
from datetime import datetime

logger = logging.getLogger("ai_assistant")

# ─── API Key ────────────────────────────────────────────────────────────────────
# Configurar via variable de entorno OPENAI_API_KEY o desde el panel de Ajustes del CRM
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
# ────────────────────────────────────────────────────────────────────────────────

# ─── Tools disponibles para el agente ─────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_churn_panel",
            "description": (
                "Obtiene la lista de jugadores en riesgo de churn. "
                "Devuelve jugadores clasificados en riesgo alto (score≥70), "
                "medio (40-69) y bajo (<40). Incluye nombre, cargas, monto, "
                "días inactivo, jurisdicción y churn score."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nivel": {
                        "type": "string",
                        "enum": ["alto", "medio", "bajo", "todos"],
                        "description": "Nivel de riesgo a consultar. 'todos' devuelve los tres niveles."
                    },
                    "jurisdiccion": {
                        "type": "string",
                        "enum": ["CABA", "MDZ", ""],
                        "description": "Filtrar por jurisdicción. Vacío = ambas."
                    },
                    "limite": {
                        "type": "integer",
                        "description": "Máximo de jugadores a devolver por nivel. Default 50.",
                        "default": 50
                    }
                },
                "required": ["nivel"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_jugadores",
            "description": (
                "Filtra y obtiene jugadores de la base de datos. "
                "Usar para: inactivos, churn, sin depósito, top por monto, segmentos específicos, búsqueda por nombre/DNI. "
                "Para 'sin primer depósito' usar sin_ftd=true. "
                "Para 'top por monto' usar orden='monto_desc'. "
                "Para 'más inactivos' usar orden='inactivo_desc'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "jurisdiccion": {"type": "string", "description": "CABA, MDZ o vacío para ambas"},
                    "grupo": {"type": "string", "description": "Grupo del jugador"},
                    "subgrupo": {"type": "string", "description": "Subgrupo del jugador"},
                    "sin_ftd": {"type": "boolean", "description": "true = solo jugadores SIN primer depósito (ftd=0). Usar para 'sin primer depósito', 'sin cargas', 'no depositaron'."},
                    "con_ftd": {"type": "boolean", "description": "true = solo jugadores CON al menos un depósito"},
                    "dias_inactivo_min": {"type": "integer", "description": "Mínimo de días inactivo. Ej: 10 para 'inactivos más de 10 días'"},
                    "dias_inactivo_max": {"type": "integer", "description": "Máximo de días inactivo"},
                    "cargas_min": {"type": "integer", "description": "Mínimo de cargas totales"},
                    "cargas_max": {"type": "integer", "description": "Máximo de cargas totales. 0 = sin cargas"},
                    "churn_min": {"type": "integer", "description": "Mínimo churn score (0-100). 70 = churn alto"},
                    "churn_max": {"type": "integer", "description": "Máximo churn score"},
                    "orden": {
                        "type": "string",
                        "enum": ["churn_desc","monto_desc","monto_asc","cargas_desc","cargas_asc","inactivo_desc","nombre_asc"],
                        "description": "Ordenar por: churn_desc (default), monto_desc (top por monto), cargas_desc (más cargas), inactivo_desc (más inactivos)"
                    },
                    "limite": {"type": "integer", "description": "Máximo de resultados. Default 100. Usar 20 para tops."},
                    "buscar": {"type": "string", "description": "Buscar por nombre, apellido, usuario o email"},
                    "nrodoc": {"type": "string", "description": "DNI exacto del jugador para búsqueda directa"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_stats_globales",
            "description": (
                "Obtiene estadísticas globales del CRM: total jugadores, "
                "FTD, monto total, distribución por jurisdicción, churn promedio, "
                "campañas recientes y métricas de email."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "jurisdiccion": {"type": "string", "description": "CABA, MDZ o vacío para global"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_campanas",
            "description": (
                "Obtiene el historial de campañas ejecutadas con sus métricas: "
                "enviados, aperturas, clicks, conversiones, fechas y tags usados. "
                "Puede filtrar por los últimos N días."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dias": {"type": "integer", "description": "Últimos N días. 0 = todas. Default 30."},
                    "jurisdiccion": {"type": "string", "description": "Filtrar por jurisdicción"},
                    "solo_reales": {"type": "boolean", "description": "Si true, excluye simulaciones. Default true."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_metricas_email",
            "description": (
                "Obtiene métricas globales de email: tasa de apertura, clicks, "
                "conversiones, top tags por performance y timeline de eventos."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "exportar_excel",
            "description": (
                "Genera y exporta un reporte en formato Excel (.xlsx). "
                "Puede incluir múltiples hojas: jugadores, campañas, métricas, churn. "
                "Devuelve un ID de descarga para que el usuario pueda bajar el archivo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {
                        "type": "string",
                        "enum": ["jugadores", "campanas", "churn", "metricas", "completo"],
                        "description": "Tipo de reporte. 'completo' incluye todas las hojas."
                    },
                    "dias": {"type": "integer", "description": "Para campañas: últimos N días. Default 7."},
                    "jurisdiccion": {"type": "string", "description": "Filtrar por jurisdicción"},
                    "nombre_archivo": {"type": "string", "description": "Nombre del archivo sin extensión"}
                },
                "required": ["tipo"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ejecutar_campana",
            "description": (
                "Ejecuta una campaña asignando un tag en Ontraport y/o disparando un flow en emBlue. "
                "Si el usuario especifica jugadores concretos (por nombre o DNI), usar el parámetro 'nrodocs' con sus DNIs. "
                "Solo usar 'segmento' cuando se quiere impactar un grupo amplio sin jugadores específicos. "
                "SIEMPRE simular primero (dry_run=true) y mostrar cuántos jugadores se impactarán antes de ejecutar en real."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tag_ontraport": {"type": "string", "description": "Tag a aplicar en Ontraport (ej: push2_caba)"},
                    "sms_flow": {"type": "string", "description": "Flow de emBlue a disparar"},
                    "nrodocs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lista de DNIs específicos a impactar. USAR ESTO cuando el usuario pide una campaña para uno o varios jugadores específicos. Tiene prioridad absoluta sobre 'segmento'."
                    },
                    "segmento": {
                        "type": "object",
                        "description": "Filtros de segmento. Solo usar cuando NO hay jugadores específicos.",
                        "properties": {
                            "jurisdiccion": {"type": "string"},
                            "churn_min": {"type": "integer"},
                            "dias_inactivo_min": {"type": "integer"},
                            "cargas_max": {"type": "integer"}
                        }
                    },
                    "nombre": {"type": "string", "description": "Nombre de la campaña"},
                    "dry_run": {"type": "boolean", "description": "Si true, simula sin ejecutar. SIEMPRE usar true a menos que el usuario confirme explícitamente."}
                },
                "required": ["nombre", "dry_run"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analizar_segmento",
            "description": (
                "Analiza un segmento de jugadores y devuelve insights: "
                "distribución de cargas, monto promedio, días inactivo promedio, "
                "churn score promedio, y recomendación de tag/acción."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "jurisdiccion": {"type": "string"},
                    "dias_inactivo_min": {"type": "integer"},
                    "churn_min": {"type": "integer"},
                    "grupo": {"type": "string"}
                },
                "required": []
            }
        }
    }
]

# ─── Ejecutor de tools ─────────────────────────────────────────────────────────

def execute_tool(name: str, args: dict, db_module) -> dict:
    """
    Ejecuta una tool del agente con datos reales de la DB.
    db_module es el módulo database importado desde server.py
    """
    try:
        if name == "get_churn_panel":
            return _tool_churn(args, db_module)
        elif name == "get_jugadores":
            return _tool_jugadores(args, db_module)
        elif name == "get_stats_globales":
            return _tool_stats(args, db_module)
        elif name == "get_campanas":
            return _tool_campanas(args, db_module)
        elif name == "get_metricas_email":
            return _tool_metricas(db_module)
        elif name == "exportar_excel":
            return _tool_excel(args, db_module)
        elif name == "ejecutar_campana":
            return _tool_ejecutar(args, db_module)
        elif name == "analizar_segmento":
            return _tool_analizar(args, db_module)
        else:
            return {"error": f"Tool '{name}' no reconocida"}
    except Exception as e:
        logger.error("execute_tool error: %s — %s", name, e, exc_info=True)
        return {"error": str(e)}


def _tool_churn(args, db):
    nivel = args.get("nivel", "todos")
    jur   = args.get("jurisdiccion", "")
    lim   = args.get("limite", 50)

    panel = db.get_churn_panel(jur)

    result = {"resumen": {
        "alto": panel["total_alto"],
        "medio": panel["total_medio"],
        "bajo": panel["total_bajo"],
    }}

    campos = ["nrodoc","nombre","apellido","email","telefono","jurisdiccion",
              "grupo","subgrupo","total_cargas","monto_prom","dias_inactivo","churn_score","usuario"]

    def fmt(jugadores):
        return [
            {k: j.get(k) for k in campos}
            for j in jugadores[:lim]
        ]

    if nivel in ("alto", "todos"):
        result["alto"] = fmt(panel["alto"])
    if nivel in ("medio", "todos"):
        result["medio"] = fmt(panel["medio"])
    if nivel in ("bajo", "todos"):
        result["bajo"] = fmt(panel["bajo"])

    return result


def _tool_jugadores(args, db):
    buscar = args.get("buscar", "")
    nrodoc = args.get("nrodoc", "")

    # Búsqueda exacta por DNI
    if nrodoc:
        nrodoc_clean = str(nrodoc).strip().replace(" ", "")
        j = db.get_jugador_perfil(nrodoc_clean)
        if j:
            campos = ["nrodoc","nombre","apellido","email","telefono","jurisdiccion",
                      "grupo","subgrupo","total_cargas","monto_prom","dias_inactivo",
                      "churn_score","ftd","provincia","usuario","estado","total_retiros"]
            return {"total": 1, "jugadores": [{k: j.get(k) for k in campos}]}
        buscar = buscar or nrodoc_clean

    filtros = {}
    if args.get("jurisdiccion"):      filtros["jur"]        = args["jurisdiccion"]
    if args.get("grupo"):             filtros["grupo"]       = args["grupo"]
    if args.get("subgrupo"):          filtros["subgrupo"]    = args["subgrupo"]
    if args.get("provincia"):         filtros["provincia"]   = args["provincia"]
    # Filtros FTD
    if args.get("sin_ftd"):           filtros["sin_ftd"]     = True
    if args.get("con_ftd"):           filtros["con_ftd"]     = True
    # Días inactivo
    if args.get("dias_inactivo_min") is not None:
        filtros["min_dias"]  = int(args["dias_inactivo_min"])
    if args.get("dias_inactivo_max") is not None:
        filtros["max_dias"]  = int(args["dias_inactivo_max"])
    # Churn
    if args.get("churn_min") is not None: filtros["churn_min"] = float(args["churn_min"])
    if args.get("churn_max") is not None: filtros["churn_max"] = float(args["churn_max"])
    # Cargas (acepta 0)
    if args.get("cargas_min") is not None: filtros["min_cargas"] = int(args["cargas_min"])
    if args.get("cargas_max") is not None: filtros["max_cargas"] = int(args["cargas_max"])
    if buscar:                             filtros["q"]           = buscar.strip()

    lim   = int(args.get("limite", 100))
    orden = args.get("orden", "churn_desc")
    data  = db.get_jugadores(filtros, 1, lim, orden=orden)
    jugs  = data.get("jugadores", [])

    campos = ["nrodoc","nombre","apellido","email","telefono","jurisdiccion",
              "grupo","subgrupo","total_cargas","monto_prom","dias_inactivo",
              "churn_score","ftd","provincia","usuario","estado","total_retiros"]
    return {
        "total":     data.get("total", 0),
        "devueltos": len(jugs),
        "jugadores": [{k: j.get(k) for k in campos} for j in jugs]
    }


def _tool_stats(args, db):
    jur = args.get("jurisdiccion", "")
    s = db.get_stats(jur)
    # Limpiar datos muy pesados
    s.pop("campanas_recientes", None)
    s.pop("dist_monto", None)
    return s


def _tool_campanas(args, db):
    import sqlite3
    dias = args.get("dias", 30)
    solo_reales = args.get("solo_reales", True)
    jur  = args.get("jurisdiccion", "")

    camps = db.get_campanas(jur, limit=200)

    if solo_reales:
        camps = [c for c in camps if not c.get("dry_run")]

    if dias > 0:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
        camps = [c for c in camps if (c.get("fecha") or "") >= cutoff]

    # Resumen agregado
    total_env  = sum(c.get("enviados", 0) for c in camps)
    total_ap   = sum(c.get("aperturas", 0) for c in camps)
    total_cl   = sum(c.get("clicks", 0) for c in camps)
    total_conv = sum(c.get("conversiones", 0) for c in camps)

    return {
        "total_campanas": len(camps),
        "total_enviados": total_env,
        "total_aperturas": total_ap,
        "total_clicks": total_cl,
        "total_conversiones": total_conv,
        "pct_apertura": round(total_ap/total_env*100, 1) if total_env else 0,
        "pct_clicks":   round(total_cl/total_env*100, 1) if total_env else 0,
        "campanas": camps
    }


def _tool_metricas(db):
    return db.get_metricas_globales()


def _tool_excel(args, db):
    import pandas as pd

    tipo   = args.get("tipo", "completo")
    dias   = args.get("dias", 7)
    jur    = args.get("jurisdiccion", "")
    nombre = args.get("nombre_archivo", f"super7_reporte_{datetime.now().strftime('%Y%m%d_%H%M')}")

    buf = io.BytesIO()
    writer = pd.ExcelWriter(buf, engine="openpyxl")

    sheets_created = []

    if tipo in ("jugadores", "completo"):
        data = db.get_jugadores({"jur": jur}, 1, 9999)
        df = pd.DataFrame(data.get("jugadores", []))
        if not df.empty:
            df.to_excel(writer, sheet_name="Jugadores", index=False)
            sheets_created.append("Jugadores")

    if tipo in ("churn", "completo"):
        panel = db.get_churn_panel(jur)
        for nivel, rows in [("Churn Alto", panel["alto"]),
                             ("Churn Medio", panel["medio"]),
                             ("Churn Bajo", panel["bajo"])]:
            if rows:
                pd.DataFrame(rows).to_excel(writer, sheet_name=nivel, index=False)
                sheets_created.append(nivel)

    if tipo in ("campanas", "metricas", "completo"):
        camps = db.get_campanas(jur, limit=500)
        if dias > 0:
            from datetime import datetime as dt, timedelta
            cutoff = (dt.now() - timedelta(days=dias)).strftime("%Y-%m-%d")
            camps  = [c for c in camps if (c.get("fecha") or "") >= cutoff]
        if camps:
            df_c = pd.DataFrame(camps)
            cols = ["nombre","canal","fecha","enviados","aperturas","pct_apertura",
                    "clicks","pct_clicks","conversiones","pct_conv","email_tag","sms_flow","estado"]
            df_c = df_c[[c for c in cols if c in df_c.columns]]
            df_c.to_excel(writer, sheet_name="Campañas", index=False)
            sheets_created.append("Campañas")

    if tipo in ("metricas", "completo"):
        m = db.get_metricas_globales()
        top = m.get("top_tags", [])
        if top:
            pd.DataFrame(top).to_excel(writer, sheet_name="Top Tags", index=False)
            sheets_created.append("Top Tags")

    writer.close()
    buf.seek(0)

    # Guardar en uploads para descarga
    os.makedirs("uploads", exist_ok=True)
    filename = f"{nombre}.xlsx"
    path = os.path.join("uploads", filename)
    with open(path, "wb") as f:
        f.write(buf.read())

    return {
        "ok": True,
        "filename": filename,
        "download_url": f"/api/ai/descargar/{filename}",
        "hojas": sheets_created,
        "mensaje": f"Reporte generado con {len(sheets_created)} hoja(s): {', '.join(sheets_created)}"
    }


def _tool_analizar(args, db):
    filtros = {}
    if args.get("jurisdiccion"):      filtros["jur"]      = args["jurisdiccion"]
    if args.get("dias_inactivo_min"): filtros["min_dias"] = int(args["dias_inactivo_min"])
    if args.get("churn_min"):         filtros["churn_min"]= float(args["churn_min"])
    if args.get("grupo"):             filtros["grupo"]    = args["grupo"]
    data = db.get_jugadores(filtros, 1, 9999)
    jugs = data.get("jugadores", [])

    if not jugs:
        return {"error": "No se encontraron jugadores con esos filtros"}

    cargas   = [j.get("total_cargas", 0) or 0 for j in jugs]
    montos   = [j.get("monto_prom", 0) or 0 for j in jugs]
    inact    = [j.get("dias_inactivo", 0) or 0 for j in jugs]
    churn    = [j.get("churn_score", 0) or 0 for j in jugs]

    # Distribución de cargas para recomendación de tag
    def pct(lst, cond): return round(sum(1 for x in lst if cond(x))/len(lst)*100, 1)

    caba_n = sum(1 for j in jugs if str(j.get("jurisdiccion","")).upper()=="CABA")
    mdz_n  = sum(1 for j in jugs if str(j.get("jurisdiccion","")).upper()=="MDZ")

    cargas_prom = round(sum(cargas)/len(cargas), 1)
    push_level  = min(int(cargas_prom), 4)

    recomendaciones = []
    avg_churn = round(sum(churn)/len(churn), 1)
    avg_inact = round(sum(inact)/len(inact), 1)

    if avg_churn >= 70:
        recomendaciones.append("🔴 Segmento crítico — activar campaña de reactivación urgente")
    elif avg_churn >= 40:
        recomendaciones.append("🟡 Riesgo moderado — campaña de retención preventiva")

    if avg_inact >= 14:
        recomendaciones.append(f"⏰ Inactivos promedio {avg_inact:.0f} días — considerar push de reactivación")

    if caba_n > 0:
        recomendaciones.append(f"📧 Tag sugerido CABA: push{push_level}_caba ({caba_n} jugadores)")
    if mdz_n > 0:
        recomendaciones.append(f"📧 Tag sugerido MDZ: push{push_level}_mdz ({mdz_n} jugadores)")

    return {
        "total_jugadores": len(jugs),
        "caba": caba_n,
        "mdz": mdz_n,
        "cargas_promedio": cargas_prom,
        "monto_promedio": round(sum(montos)/len(montos), 2) if montos else 0,
        "dias_inactivo_promedio": avg_inact,
        "churn_score_promedio": avg_churn,
        "distribucion_push": {
            f"push{i}": pct(cargas, lambda x, i=i: (x==i if i<4 else x>=4))
            for i in range(5)
        },
        "recomendaciones": recomendaciones
    }


def _tool_ejecutar(args, db):
    """Ejecuta una campaña. dry_run=True por defecto por seguridad."""
    from integrations.ontraport import tag_contact, compute_tag
    from integrations.emblue import trigger_sms_flow

    dry_run  = args.get("dry_run", True)
    tag      = args.get("tag_ontraport", "")
    flow     = args.get("sms_flow", "")
    nombre   = args.get("nombre", "Campaña desde asistente")
    nrodocs  = args.get("nrodocs", [])   # DNIs específicos — prioridad máxima
    segmento = args.get("segmento", {})

    # ── Resolución de jugadores ───────────────────────────────────────────────
    jugs = []

    if nrodocs:
        # Modo jugadores específicos — buscar cada DNI exacto
        for dni in nrodocs:
            dni_clean = str(dni).strip().replace(" ", "")
            j = db.get_jugador_perfil(dni_clean)
            if j:
                jugs.append(j)
            else:
                logger.warning("ejecutar_campana: DNI '%s' no encontrado en DB", dni_clean)
    else:
        # Modo segmento — filtros amplios
        filtros = {}
        if segmento.get("jurisdiccion"):      filtros["jur"]       = segmento["jurisdiccion"]
        if segmento.get("churn_min"):         filtros["churn_min"] = float(segmento["churn_min"])
        if segmento.get("dias_inactivo_min"): filtros["min_dias"]  = int(segmento["dias_inactivo_min"])
        if segmento.get("cargas_max"):        filtros["max_cargas"]= int(segmento["cargas_max"])
        data = db.get_jugadores(filtros, 1, 9999)
        jugs = data.get("jugadores", [])

    if not jugs:
        return {
            "dry_run": dry_run,
            "error": "No se encontraron jugadores con los criterios especificados.",
            "jugadores_objetivo": 0
        }

    # ── Helpers ───────────────────────────────────────────────────────────────
    import uuid, json as _json
    from datetime import datetime as _dt

    preview = [f"{j.get('nombre','')} {j.get('apellido','')} (DNI {j.get('nrodoc','')})".strip()
               for j in jugs[:5]]
    if len(jugs) > 5:
        preview.append(f"... y {len(jugs)-5} más")

    caba_n = sum(1 for j in jugs if str(j.get("jurisdiccion","")).upper() == "CABA")
    mdz_n  = sum(1 for j in jugs if str(j.get("jurisdiccion","")).upper() == "MDZ")
    canal  = "ambos" if (tag and flow) else ("email" if tag else "sms")

    if dry_run:
        tags_preview = {j.get("nrodoc",""): (tag if tag else compute_tag(j)) for j in jugs[:3]}
        return {
            "dry_run": True,
            "jugadores_objetivo": len(jugs),
            "jugadores_preview": preview,
            "tag": tag or "calculado por jugador",
            "tags_ejemplo": tags_preview,
            "sms_flow": flow,
            "nrodocs_usados": [j.get("nrodoc") for j in jugs],
            "mensaje": (
                f"Simulación lista: se aplicaría el tag '{tag or 'calculado'}' "
                f"a {len(jugs)} jugador{'es' if len(jugs)>1 else ''}: "
                f"{', '.join(preview[:3])}{'...' if len(jugs)>3 else ''}. "
                f"Confirmá para ejecutar en Ontraport."
            )
        }

    # ── Ejecución real ────────────────────────────────────────────────────────
    enviados = errores = 0
    detalle  = []
    for j in jugs:
        tag_final = tag if tag else compute_tag(j)

        if tag_final and j.get("email"):
            r = tag_contact(jugador=j, tag=tag_final)
            if r.get("ok"): enviados += 1
            else:
                errores += 1
                detalle.append({"nrodoc": j.get("nrodoc"), "error": r.get("message")})

        if flow and j.get("telefono"):
            r = trigger_sms_flow(jugador=j, flow=flow)
            if r.get("ok"): enviados += 1
            else: errores += 1

    # ── Guardar en DB para que aparezca en dashboard e historial ─────────────
    try:
        cid = str(uuid.uuid4())[:8]
        db.save_campana({
            "id":           cid,
            "nombre":       nombre,
            "canal":        canal,
            "estado":       "enviado",
            "fecha":        _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total":        len(jugs),
            "enviados":     enviados,
            "errores":      errores,
            "dry_run":      0,
            "filtros_json": _json.dumps({"nrodocs": [j.get("nrodoc") for j in jugs]}),
            "filtros_desc": f"Asistente IA — {len(jugs)} jugador(es) específico(s)",
            "caba":         caba_n,
            "mdz":          mdz_n,
            "mensaje_sms":  flow,
            "email_tag":    tag,
            "sms_flow":     flow,
        })
        logger.info("Campaña '%s' guardada en DB con id=%s", nombre, cid)
    except Exception as e:
        logger.error("Error guardando campaña en DB: %s", e)

    return {
        "dry_run": False,
        "enviados": enviados,
        "errores": errores,
        "tag": tag,
        "nombre": nombre,
        "jugadores_impactados": len(jugs),
        "detalle_errores": detalle[:5],
        "mensaje": f"Campaña '{nombre}' ejecutada: {enviados} contactos impactados en Ontraport, {errores} errores. Registrada en el historial del CRM."
    }


# ─── Chat con OpenAI ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sos el Asistente de CRM Super7. Tu único rol es ayudar con el CRM de casino.

LÍMITE ESTRICTO: Solo respondés preguntas del CRM — jugadores, campañas, churn, métricas, segmentación, retención, reportes. Fuera de eso respondé: "Solo puedo ayudarte con temas del CRM Super7."

REGLA FUNDAMENTAL — SIEMPRE LLAMAR TOOLS ANTES DE RESPONDER:
Antes de responder CUALQUIER pregunta sobre datos, OBLIGATORIAMENTE llamá la tool correspondiente.
NUNCA respondas con datos inventados. NUNCA digas "no tengo acceso" — siempre tenés acceso via tools.

MAPEO DE PREGUNTAS → TOOLS (seguí esto al pie de la letra):

"Jugadores en churn alto" → get_churn_panel(nivel="alto")
"Inactivos más de N días" → get_jugadores(dias_inactivo_min=N, orden="inactivo_desc")
"Top 20 jugadores por monto" → get_jugadores(orden="monto_desc", limite=20)
"Resumen completo de la base" → get_stats_globales()
"Jugadores sin primer depósito" → get_jugadores(sin_ftd=true, orden="churn_desc")
"Jugadores sin cargas" → get_jugadores(sin_ftd=true)
"Comparar CABA vs Mendoza" → get_stats_globales(jurisdiccion="CABA") + get_stats_globales(jurisdiccion="MDZ")
"Campañas de los últimos 7 días" → get_campanas(dias=7)
"¿Qué segmento activar hoy?" → analizar_segmento() + get_churn_panel(nivel="alto")
"Armar campaña para churn alto CABA" → get_jugadores(jurisdiccion="CABA", churn_min=70) + ejecutar_campana(dry_run=true)
"Ver métricas de email" → get_metricas_email()
"Exportar Excel completo" → exportar_excel(tipo="completo")
"Exportar reporte de churn" → exportar_excel(tipo="churn")
"Exportar campañas" → exportar_excel(tipo="campanas")

HERRAMIENTAS DISPONIBLES:
- get_churn_panel(nivel, jurisdiccion, limite) → jugadores por riesgo: alto≥70, medio 40-69, bajo<40
- get_jugadores(sin_ftd, con_ftd, dias_inactivo_min/max, churn_min/max, cargas_min/max, jurisdiccion, grupo, orden, limite, buscar, nrodoc) → jugadores filtrados y ordenados
- get_stats_globales(jurisdiccion) → totales, FTD, monto, churn, distribución, campañas recientes
- get_campanas(dias, jurisdiccion) → historial de campañas con métricas
- get_metricas_email() → aperturas, clicks, conversiones globales
- exportar_excel(tipo, jurisdiccion, dias) → genera .xlsx descargable
- ejecutar_campana(nrodocs, segmento, tag_ontraport, sms_flow, nombre, dry_run) → simula o ejecuta campaña
- analizar_segmento(jurisdiccion, dias_inactivo_min, churn_min, grupo) → insights y recomendaciones

REGLAS DE FORMATO (OBLIGATORIO):
1. Respondé SIEMPRE en español
2. Tablas: SIEMPRE markdown con pipes | col | col | NUNCA HTML <table>
3. Listas: SIEMPRE guiones - item NUNCA tags <li> o <ul>
4. NUNCA HTML crudo: solo **negrita**, *cursiva*, # encabezados, | tablas |, - listas
5. Tabla de jugadores incluye: | DNI | Nombre | Jurisdicción | Cargas | Monto prom. | Días inactivo | Churn | Tag |
6. Para campañas: dry_run=true primero → mostrar preview → pedir confirmación explícita
7. Para ejecutar_campana con jugadores identificados: SIEMPRE usar parámetro "nrodocs" con lista de DNIs

CONTEXTO DEL NEGOCIO:
- Casino online regulado en CABA y Mendoza (Argentina)
- Tags Ontraport: push0_caba..push4_caba, push0_mdz..push4_mdz (nivel = min(cargas, 4))
- Churn score 0-100: ≥70 alto riesgo, 40-69 medio, <40 bajo
- FTD = First Time Deposit = primer depósito del jugador
- dias_inactivo=999 significa que el jugador NUNCA tuvo actividad (sin FTD generalmente)
"""

def chat(messages: list, openai_api_key: str, db_module, model: str = "gpt-4o-mini") -> dict:
    """
    Ejecuta un turno de conversación con el asistente.
    messages: lista de {"role": "user"|"assistant"|"tool", "content": "..."}
    Retorna: {"reply": str, "tool_calls": list, "actions": list}
    """
    if not openai_api_key:
        openai_api_key = OPENAI_API_KEY
    import requests as req

    headers = {
        "Authorization": f"Bearer {openai_api_key}",
        "Content-Type": "application/json",
    }

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    # Primera llamada a OpenAI
    payload = {
        "model": model,
        "messages": msgs,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.3,
    }

    r = req.post("https://api.openai.com/v1/chat/completions",
                 headers=headers, json=payload, timeout=60)

    if r.status_code != 200:
        logger.error("OpenAI error %d: %s", r.status_code, r.text[:300])
        return {"error": f"OpenAI error {r.status_code}: {r.text[:200]}"}

    resp = r.json()
    msg  = resp["choices"][0]["message"]
    tool_results = []
    actions      = []

    # Si el modelo quiere llamar tools
    if msg.get("tool_calls"):
        tool_msgs = []
        for tc in msg["tool_calls"]:
            fn_name = tc["function"]["name"]
            fn_args = json.loads(tc["function"]["arguments"])

            logger.info("AI tool call: %s(%s)", fn_name, fn_args)
            result = execute_tool(fn_name, fn_args, db_module)

            # Si es un Excel, registrar como acción descargable
            if fn_name == "exportar_excel" and result.get("ok"):
                actions.append({
                    "type": "download",
                    "label": f"⬇ Descargar {result['filename']}",
                    "url": result["download_url"]
                })

            # Si es una campaña simulada, agregar botón de confirmación
            if fn_name == "ejecutar_campana" and result.get("dry_run"):
                actions.append({
                    "type": "confirm_campaign",
                    "label": "🚀 Ejecutar campaña en real",
                    "payload": fn_args
                })

            tool_results.append({
                "name": fn_name,
                "args": fn_args,
                "result": result
            })

            tool_msgs.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False)
            })

        # Segunda llamada con resultados de tools
        msgs2 = msgs + [msg] + tool_msgs
        payload2 = {
            "model": model,
            "messages": msgs2,
            "temperature": 0.3,
        }
        r2   = req.post("https://api.openai.com/v1/chat/completions",
                        headers=headers, json=payload2, timeout=60)
        resp2 = r2.json()
        reply = resp2["choices"][0]["message"]["content"]
    else:
        reply = msg.get("content", "")

    return {
        "reply": reply,
        "tool_calls": tool_results,
        "actions": actions
    }
