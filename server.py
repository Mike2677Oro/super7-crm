"""
server.py — Super7 CRM v5
python server.py → http://localhost:5000
"""

from integrations.ontraport import tag_contact, compute_tag
from integrations.emblue import trigger_sms_flow
from flask import Flask, render_template, jsonify, request, send_file, session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import pandas as pd, numpy as np, io, os, uuid, json
from datetime import datetime
import requests

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_OK = True
except ImportError:
    GSPREAD_OK = False

from database import (
    init_db, upsert_jugadores, get_jugadores, get_jugadores_ids, get_jugador_perfil,
    get_stats, get_filtros_disponibles, get_churn_panel,
    get_bonos_stats, save_bono,
    save_campana, get_campanas, update_metricas,
    save_webhook_event, get_metricas_globales, get_metricas_campana,
    save_journey, get_journeys, get_journey, toggle_journey, delete_journey,
    save_auto, get_autos, toggle_auto, delete_auto,
    export_jugadores, calcular_churn,
    save_grupo, get_grupos, get_grupo, delete_grupo, update_grupo,
    save_bono_ext, get_bonos_ext, delete_bono_ext,
    save_reporte, get_reportes,
    save_vip_player, get_vip_players, get_vip_player, update_vip_player,
    save_vip_note, get_vip_notes, save_vip_task, get_vip_tasks,
    save_host, get_hosts, get_financial_data, save_financial_data,
    get_usuario_by_username, get_usuario_by_id, get_usuarios,
    create_usuario, update_usuario, update_ultimo_login, delete_usuario,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
app.secret_key = os.environ.get("CRM_SECRET_KEY", "super7-crm-secret-2025-xK9!mP")
os.makedirs("uploads", exist_ok=True)
init_db()

# ── Auth helpers ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "No autenticado", "auth_required": True}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "No autenticado", "auth_required": True}), 401
        if session.get("user_rol") != "admin":
            return jsonify({"error": "Se requiere rol admin"}), 403
        return f(*args, **kwargs)
    return decorated

# ── Config ──────────────────────────────────────────────────
ONTRAPORT_API_KEY = os.environ.get("ONTRAPORT_API_KEY", "6RdAMjA2dHj57Rf")
ONTRAPORT_APP_ID  = os.environ.get("ONTRAPORT_APP_ID",  "2_234387_4mGjbnJ3e")

EMBLUE_API_KEY    = os.environ.get("EMBLUE_API_KEY", "")
EMBLUE_BASE_URL   = os.environ.get("EMBLUE_BASE_URL", "https://api.embluemail.com")

GS_CREDENTIALS_FILE = os.environ.get("GS_CREDENTIALS_FILE", "credentials.json")

GS_SHEET_CABA = os.environ.get("GS_SHEET_CABA", "1tzr_kwKzl2k1tnSq4p81W5nIFCsIjn0DesuLJRtYzDM")
GS_SHEET_MDZ  = os.environ.get("GS_SHEET_MDZ",  "1yAkNbfJhpyPsJixhlULeY4LlLQsuR5lLe3kax1J-9qk")

GS_TAB_CABA = os.environ.get("GS_TAB_CABA", "Jugadores")
GS_TAB_MDZ  = os.environ.get("GS_TAB_MDZ",  "Jugadores")

archivo_estado = {"archivos": {}, "cargado_en": None}
# ── Parseo ───────────────────────────────────────────────────
def p_monto(v):
    if v is None or str(v).strip() in ("","nan","None"): return 0.0
    try: return float(str(v).replace("$","").replace(".","").replace(",",".").strip())
    except: return 0.0

def p_fecha(v):
    if v is None or str(v).strip() in ("","nan","None"): return None
    for f in ("%d/%m/%Y %H:%M","%d/%m/%Y","%Y-%m-%d %H:%M:%S","%Y-%m-%d","%m/%d/%Y %H:%M","%m/%d/%Y"):
        try: return datetime.strptime(str(v).strip(),f).strftime("%Y-%m-%d %H:%M")
        except: pass
    return str(v)

def p_tel(t):
    if not t: return ""
    s = str(t).replace("+","").replace("-","").replace(" ","").replace("(","").replace(")","").strip()
    if s.startswith("549"): return s
    if s.startswith("54"):  return s
    if s.startswith("0"):   s = s[1:]
    return f"549{s}" if len(s)>=10 else s

def d_inact(fs):
    if not fs: return 999
    try: return (datetime.now()-datetime.strptime(fs[:16],"%Y-%m-%d %H:%M")).days
    except: return 999

def compute_push_tag(jugador):
    """Delegado a integrations.ontraport.compute_tag (única fuente de verdad)."""
    return compute_tag(jugador)

def leer_df(b, fn):
    ext = fn.rsplit(".",1)[-1].lower()
    if ext=="csv":
        for sep,enc in [(";","utf-8"),(",","utf-8"),(";","latin-1"),(",","latin-1")]:
            try:
                df=pd.read_csv(io.BytesIO(b),sep=sep,encoding=enc)
                if df.shape[1]>2: return df
            except: pass
        raise ValueError("No se pudo leer el CSV")
    return pd.read_excel(io.BytesIO(b),engine="openpyxl")

def procesar(df, jur):
    df.columns=[str(c).strip() for c in df.columns]
    df=df.where(pd.notna(df),None)
    out=[]
    for _,row in df.iterrows():
        # Columnas de totales
        tc      = int(row.get("Total Cargas") or 0)
        tr      = int(row.get("Total Retiros") or 0)  # cantidad de retiros (no monto)
        # Última carga
        m_ult   = p_monto(row.get("M_Ult_Carga"))     # monto última carga
        f_ult   = p_fecha(row.get("F_Ult_Carga"))     # fecha última carga
        # MUC_AUX se ignora explícitamente
        # Totales derivados
        mt      = round(m_ult * tc, 2)                # monto total estimado
        mp      = m_ult                               # monto promedio = última carga
        cargas  = [{"n":1,"fecha":f_ult,"monto":m_ult}] if (f_ult or m_ult) else []
        # Fechas
        fa      = p_fecha(row.get("Fecha Act"))        # fecha de activación
        fa_ult  = p_fecha(row.get("Fecha_Ult_Activ")) # última actividad → días inactivo
        j={
            "nrodoc":str(row.get("nrodoc") or uuid.uuid4().hex[:8]),"jurisdiccion":jur,
            "grupo":str(row.get("Grupo") or ""),"subgrupo":str(row.get("SubGrupo") or ""),
            "estado":str(row.get("Estado") or ""),"fecha_alta":p_fecha(row.get("Fecha Alta")),
            "fecha_act":fa,"usuario":str(row.get("Usuario") or ""),
            "nombre":str(row.get("Nombre") or ""),"apellido":str(row.get("Apellido") or ""),
            "sexo":str(row.get("Sexo") or ""),"edad":int(row["Edad"]) if row.get("Edad") is not None else 0,
            "telefono":p_tel(row.get("Telefono")),"email":str(row.get("Mail") or ""),
            "localidad":str(row.get("Localidad") or ""),"provincia":str(row.get("Provincia") or ""),
            "profesion":str(row.get("Profesion") or ""),"total_cargas":tc,"total_retiros":tr,
            "monto_total":mt,"monto_prom":mp,"cargas":cargas,"ftd":tc>=1,
            "dias_inactivo":d_inact(fa_ult)
        }
        j["churn_score"]=calcular_churn(j)
        out.append(j)
    return out

def desc_f(f):
    parts=[]
    mn=str(f.get("min_cargas","")); mx=str(f.get("max_cargas",""))
    if mn not in ("","0","None"): parts.append(f"{mn}"+(f"–{mx}" if mx not in ("","0") else "+")+  " cargas")
    for k,l in [("grupo","Grupo"),("subgrupo","Sub"),("provincia","Prov")]:
        v=f.get(k,"")
        if v: parts.append(f"{l}: {v}")
    if f.get("jur"): parts.append(f"Jur: {f['jur']}")
    return " · ".join(parts) if parts else "Sin filtros"

def gs_ok(jur): return bool(GS_SHEET_CABA if jur=="CABA" else GS_SHEET_MDZ)

def leer_sheets(sid,tab):
    if not GSPREAD_OK: raise RuntimeError("Instalar gspread google-auth")
    sc=["https://www.googleapis.com/auth/spreadsheets.readonly","https://www.googleapis.com/auth/drive.readonly"]
    gs_json = os.environ.get("GS_CREDENTIALS_JSON")
    if gs_json:
        import json as _j
        creds=Credentials.from_service_account_info(_j.loads(gs_json),scopes=sc)
    elif os.path.exists(GS_CREDENTIALS_FILE):
        creds=Credentials.from_service_account_file(GS_CREDENTIALS_FILE,scopes=sc)
    else:
        raise FileNotFoundError("No se encontró credentials.json ni GS_CREDENTIALS_JSON")
    gc=gspread.authorize(creds)
    return pd.DataFrame(gc.open_by_key(sid).worksheet(tab).get_all_records())

# ── Rutas ────────────────────────────────────────────────────

@app.route("/")
def index(): return render_template("index.html")

@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files: return jsonify({"error":"No se recibió archivo"}),400
    f=request.files["file"]; jur=request.form.get("jur","").upper().strip()
    if jur not in ("CABA","MDZ"): return jsonify({"error":"Indicar CABA o MDZ"}),400
    if f.filename.rsplit(".",1)[-1].lower() not in ("csv","xlsx","xls"): return jsonify({"error":"Solo CSV o Excel"}),400
    try:
        df=leer_df(f.read(),f.filename); nuevos=procesar(df,jur)
        stats=upsert_jugadores(nuevos,"excel",jur)
        archivo_estado["archivos"][jur]={"nombre":f.filename,"fuente":"excel"}
        archivo_estado["cargado_en"]=datetime.now().strftime("%d/%m/%Y %H:%M")
        return jsonify({"ok":True,"jur":jur,"archivo":f.filename,**stats})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/upload-sheets", methods=["POST"])
def upload_sheets():
    data=request.get_json() or {}; jur=data.get("jur","ambas").upper()
    if not GSPREAD_OK: return jsonify({"error":"Instalar gspread google-auth"}),400
    res={}
    for j in (["CABA","MDZ"] if jur=="AMBAS" else [jur]):
        if not gs_ok(j): res[j]={"error":f"GS_SHEET_{j} no configurado"}; continue
        try:
            sid=GS_SHEET_CABA if j=="CABA" else GS_SHEET_MDZ
            tab=GS_TAB_CABA   if j=="CABA" else GS_TAB_MDZ
            df=leer_sheets(sid,tab); nuevos=procesar(df,j); stats=upsert_jugadores(nuevos,"sheets",j)
            archivo_estado["archivos"][j]={"nombre":f"Google Sheets ({tab})","fuente":"sheets"}
            archivo_estado["cargado_en"]=datetime.now().strftime("%d/%m/%Y %H:%M")
            res[j]={"ok":True,**stats}
        except Exception as e: res[j]={"error":str(e)}
    return jsonify({"ok":all("ok" in v for v in res.values()),"resultados":res})

@app.route("/api/refresh", methods=["POST"])
def refresh():
    if not archivo_estado["archivos"]: return jsonify({"error":"Sin datos"}),400
    res={}
    for jur,info in archivo_estado["archivos"].items():
        if info.get("fuente")=="sheets":
            try:
                sid=GS_SHEET_CABA if jur=="CABA" else GS_SHEET_MDZ
                tab=GS_TAB_CABA   if jur=="CABA" else GS_TAB_MDZ
                df=leer_sheets(sid,tab); nuevos=procesar(df,jur); stats=upsert_jugadores(nuevos,"sheets",jur)
                archivo_estado["cargado_en"]=datetime.now().strftime("%d/%m/%Y %H:%M")
                res[jur]={"ok":True,**stats}
            except Exception as e: res[jur]={"error":str(e)}
        else: res[jur]={"ok":False,"info":"Subí el archivo de nuevo"}
    return jsonify({"ok":any(v.get("ok") for v in res.values()),"resultados":res})

@app.route("/api/estado-archivos")
@login_required
def estado_archivos():
    from database import get_conn
    with get_conn() as conn:
        total=conn.execute("SELECT COUNT(*) FROM jugadores").fetchone()[0]
        caba=conn.execute("SELECT COUNT(*) FROM jugadores WHERE jurisdiccion='CABA'").fetchone()[0]
        mdz=conn.execute("SELECT COUNT(*) FROM jugadores WHERE jurisdiccion='MDZ'").fetchone()[0]
    return jsonify({"archivos":archivo_estado["archivos"],"cargado_en":archivo_estado["cargado_en"],
                    "total":total,"caba":caba,"mdz":mdz,
                    "gs_caba_ok":gs_ok("CABA"),"gs_mdz_ok":gs_ok("MDZ"),"gspread_ok":GSPREAD_OK})

@app.route("/api/reset-jugadores", methods=["POST"])
@login_required
def reset_jugadores():
    from database import get_conn
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM eventos_jugador")
            conn.execute("DELETE FROM jugadores")
        global archivo_estado
        archivo_estado["archivos"] = {}
        archivo_estado["cargado_en"] = ""
        return jsonify({"ok": True, "mensaje": "Base de datos limpiada. Sincroniza nuevamente desde Google Sheets."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/stats")
@login_required
def api_stats():
    jur=request.args.get("jur","")
    try:
        s=get_stats(jur); s.setdefault("sin_datos",s.get("total",0)==0); return jsonify(s)
    except Exception as e: return jsonify({"sin_datos":True,"error":str(e)})

@app.route("/api/jugadores", methods=["POST"])
@login_required
def api_jugadores():
    f=request.get_json() or {}
    if f.get("ids_only"):
        return jsonify({"ids": get_jugadores_ids(f)})
    return jsonify(get_jugadores(f,int(f.get("page",1)),int(f.get("per_page",50))))

@app.route("/api/jugadores/<nrodoc>")
@login_required
def api_jugador(nrodoc):
    j=get_jugador_perfil(nrodoc)
    return jsonify(j) if j else (jsonify({"error":"No encontrado"}),404)

@app.route("/api/filtros-disponibles")
@login_required
def api_filtros():
    return jsonify(get_filtros_disponibles(request.args.get("jur","")))

@app.route("/api/exportar", methods=["POST"])
@login_required
def api_exportar():
    f=request.get_json() or {}; result=export_jugadores(f)
    rows=[{"Jurisdiccion":j["jurisdiccion"],"DNI":j["nrodoc"],"Usuario":j["usuario"],
           "Nombre":j["nombre"],"Apellido":j["apellido"],"Email":j["email"],
           "Telefono":j["telefono"],"Grupo":j["grupo"],"SubGrupo":j["subgrupo"],
           "Estado":j["estado"],"Provincia":j["provincia"],"TotalCargas":j["total_cargas"],
           "MontoTotal":j["monto_total"],"MontoProm":j["monto_prom"],
           "DiasInactivo":j["dias_inactivo"],"ChurnScore":j["churn_score"],
           "FechaAlta":j["fecha_alta"],"UltActividad":j["fecha_act"]} for j in result]
    buf=io.BytesIO(); pd.DataFrame(rows).to_csv(buf,index=False,encoding="utf-8-sig"); buf.seek(0)
    return send_file(buf,mimetype="text/csv",as_attachment=True,download_name=f"super7_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")

@app.route("/api/churn")
@login_required
def api_churn():
    return jsonify(get_churn_panel(request.args.get("jur","")))

@app.route("/api/bonos")
@login_required
def api_bonos():
    return jsonify(get_bonos_stats(request.args.get("jur","")))

@app.route("/api/bonos", methods=["POST"])
@login_required
def api_bono_create():
    data=request.get_json() or {}
    try: return jsonify({"ok":True,"id":save_bono(data)})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/preview-campana", methods=["POST"])
def preview_campana():
    data=request.get_json() or {}
    filtros=data.get("filtros",{})
    canal=data.get("canal","sms")
    selected_tag=str(data.get("email_tag","") or "").strip().lower()
    result=get_jugadores(filtros,1,9999)["jugadores"]
    aptos=[j for j in result if (canal=="sms" and j.get("telefono")) or (canal=="email" and j.get("email")) or canal=="ambos"]
    email_aptos=[j for j in aptos if j.get("email")] if canal in ("email","ambos") else []

    tag_counts={}
    if canal in ("email","ambos"):
        if selected_tag:
            tag_counts[selected_tag] = len(email_aptos)
        else:
            for j in email_aptos:
                tag = compute_push_tag(j)
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1

    muestra=[]
    for j in aptos[:15]:
        muestra.append({
            "nrodoc": j.get("nrodoc"),
            "nombre": j.get("nombre"),
            "apellido": j.get("apellido"),
            "jurisdiccion": j.get("jurisdiccion"),
            "total_cargas": j.get("total_cargas", 0),
            "tag": selected_tag if selected_tag and canal in ("email","ambos") and j.get("email") else (compute_push_tag(j) if canal in ("email","ambos") and j.get("email") else None)
        })

    return jsonify({
        "total": len(result),
        "aptos": len(aptos),
        "sin_email": sum(1 for j in result if not j.get("email")),
        "sin_tel": sum(1 for j in result if not j.get("telefono")),
        "caba": sum(1 for j in aptos if j.get("jurisdiccion") == "CABA"),
        "mdz": sum(1 for j in aptos if j.get("jurisdiccion") == "MDZ"),
        "email_tag": selected_tag,
        "tag_counts": tag_counts,
        "muestra": muestra
    })

@app.route("/api/enviar", methods=["POST"])
def api_enviar():
    try:
        data=request.get_json() or {}
        filtros=data.get("filtros",{})
        canal=data.get("canal","sms")
        sms_flow=data.get("sms_flow","")
        email_tag=str(data.get("email_tag","") or "").strip().lower()
        nombre_c=data.get("nombre_campana",f"Campaña {datetime.now().strftime('%d/%m %H:%M')}")
        dry_run=data.get("dry_run",True)
        schedule_type=data.get("schedule_type","now")
        schedule_dt=data.get("schedule_dt")
        schedule_rec=data.get("schedule_rec")

        # ── Validaciones básicas ──────────────────────────────────────────
        if canal in ("sms", "ambos") and not sms_flow:
            return jsonify({"error": "Seleccioná un SMS Flow para el canal SMS"}), 400
        if canal in ("email", "ambos") and not email_tag:
            # email_tag puede omitirse — en ese caso se calcula por jugador
            pass

        selected = data.get("selected_players") or []
        if selected:
            jugadores = []
            for s in selected:
                j = get_jugador_perfil(str(s))
                if j:
                    jugadores.append(j)
                else:
                    # try as int string
                    j = get_jugador_perfil(str(int(float(s)))) if str(s).replace('.','',1).isdigit() else None
                    if j:
                        jugadores.append(j)
        else:
            jugadores = get_jugadores(filtros,1,999999)["jugadores"]

        import logging
        log = logging.getLogger("api_enviar")
        log.info("api_enviar: canal=%s email_tag=%s sms_flow=%s dry_run=%s jugadores=%d",
                 canal, email_tag, sms_flow, dry_run, len(jugadores))

        enviados = errores = skipped = 0
        error_details = []

        if not dry_run:
            for j in jugadores:
                nrodoc = j.get("nrodoc", "?")

                # ── Canal SMS → emBlue ───────────────────────────────────────
                if canal in ("sms", "ambos"):
                    if j.get("telefono"):
                        result = trigger_sms_flow(jugador=j, flow=sms_flow)
                        if result.get("ok"):
                            enviados += 1
                            log.info("SMS OK: nrodoc=%s flow=%s", nrodoc, sms_flow)
                        else:
                            errores += 1
                            reason = result.get("message", "Error desconocido")
                            log.error("SMS ERROR: nrodoc=%s — %s", nrodoc, reason)
                            error_details.append({"nrodoc": nrodoc, "canal": "sms", "reason": reason})
                    else:
                        skipped += 1
                        log.warning("SMS SKIP: nrodoc=%s sin teléfono", nrodoc)

                # ── Canal Email → Ontraport ──────────────────────────────────
                if canal in ("email", "ambos"):
                    if j.get("email"):
                        # Tag explícito tiene prioridad; si no, calcular por jugador
                        tag = email_tag if email_tag else compute_tag(j)
                        if not tag:
                            errores += 1
                            reason = (f"No se pudo calcular tag: "
                                      f"jur={j.get('jurisdiccion')}, cargas={j.get('total_cargas')}")
                            log.error("EMAIL TAG ERROR: nrodoc=%s — %s", nrodoc, reason)
                            error_details.append({"nrodoc": nrodoc, "canal": "email", "reason": reason})
                        else:
                            result = tag_contact(jugador=j, tag=tag)
                            if result.get("ok"):
                                enviados += 1
                                log.info("EMAIL OK: nrodoc=%s contact_id=%s tag=%s",
                                         nrodoc, result.get("contact_id"), result.get("tag"))
                            else:
                                errores += 1
                                reason = result.get("message", "Error desconocido")
                                log.error("EMAIL ERROR: nrodoc=%s tag=%s — %s", nrodoc, tag, reason)
                                error_details.append({"nrodoc": nrodoc, "canal": "email",
                                                      "tag": tag, "reason": reason})
                    else:
                        skipped += 1
                        log.warning("EMAIL SKIP: nrodoc=%s sin email", nrodoc)

        else:
            # Dry run — sólo contar
            for j in jugadores:
                has_contact = (
                    (canal in ("sms", "ambos") and j.get("telefono")) or
                    (canal in ("email", "ambos") and j.get("email"))
                )
                if has_contact:
                    enviados += 1
                else:
                    skipped += 1

        log.info("api_enviar RESULT: enviados=%d errores=%d skipped=%d dry_run=%s",
                 enviados, errores, skipped, dry_run)

        meta = {
            "email_tag": email_tag,
            "sms_flow": sms_flow,
            "schedule": {"type": schedule_type, "dt": schedule_dt, "rec": schedule_rec}
        }
        if selected:
            filtros_guardado = {"selected_players": selected, "_meta": meta}
        else:
            filtros_guardado = dict(filtros) if isinstance(filtros, dict) else {}
            filtros_guardado["_meta"] = meta

        cid = str(uuid.uuid4())[:8]

        save_campana({
            "id": cid,
            "nombre": nombre_c,
            "canal": canal,
            "estado": "enviada" if not dry_run else "simulada",
            "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "total": len(jugadores),
            "enviados": enviados,
            "errores": errores,
            "dry_run": 1 if dry_run else 0,
            "filtros_json": json.dumps(filtros_guardado),
            "filtros_desc": desc_f(filtros) if not selected else f"Seleccion manual: {len(selected)} jugadores",
            "caba": sum(1 for j in jugadores if j["jurisdiccion"] == "CABA"),
            "mdz": sum(1 for j in jugadores if j["jurisdiccion"] == "MDZ"),
            "mensaje_sms": data.get("sms_flow", "")
        })

        return jsonify({
            "ok": True,
            "id": cid,
            "enviados": enviados,
            "errores": errores,
            "skipped": skipped,
            "dry_run": dry_run,
            "errors": error_details[:10]
        })
    except Exception as e:
        print(f"ERROR en api_enviar: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Error interno: {str(e)}"}), 500
            

@app.route("/api/campanas")
def api_campanas():
    return jsonify(get_campanas(request.args.get("jur","")))

@app.route("/api/campanas/<cid>/metricas", methods=["POST"])
def api_metricas(cid):
    data=request.get_json() or {}
    from database import get_conn
    with get_conn() as conn:
        c=conn.execute("SELECT enviados FROM campanas WHERE id=?",(cid,)).fetchone()
    if not c: return jsonify({"error":"No encontrada"}),404
    data["enviados_ref"]=c["enviados"] or 1
    update_metricas(cid,data); return jsonify({"ok":True})

# ── Journeys ─────────────────────────────────────────────────

@app.route("/api/journeys")
def api_journeys():
    return jsonify(get_journeys())

@app.route("/api/journeys", methods=["POST"])
def api_journey_create():
    data=request.get_json() or {}
    try: return jsonify({"ok":True,"id":save_journey(data)})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/journeys/<int:jid>")
def api_journey_get(jid):
    j=get_journey(jid)
    return jsonify(j) if j else (jsonify({"error":"No encontrado"}),404)

@app.route("/api/journeys/<int:jid>", methods=["PUT"])
def api_journey_update(jid):
    data=request.get_json() or {}; data["id"]=jid
    try: return jsonify({"ok":True,"id":save_journey(data)})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/journeys/<int:jid>/toggle", methods=["POST"])
def api_journey_toggle(jid):
    data=request.get_json() or {}
    toggle_journey(jid,data.get("activo",True)); return jsonify({"ok":True})

@app.route("/api/journeys/<int:jid>", methods=["DELETE"])
def api_journey_delete(jid):
    delete_journey(jid); return jsonify({"ok":True})

# ── Automatizaciones ─────────────────────────────────────────

@app.route("/api/automatizaciones")
def api_autos():
    return jsonify(get_autos())

@app.route("/api/automatizaciones", methods=["POST"])
def api_auto_create():
    data=request.get_json() or {}
    try: return jsonify({"ok":True,"id":save_auto(data)})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/api/automatizaciones/<int:aid>/toggle", methods=["POST"])
def api_auto_toggle(aid):
    data=request.get_json() or {}
    toggle_auto(aid,data.get("activa",True)); return jsonify({"ok":True})

@app.route("/api/automatizaciones/<int:aid>", methods=["DELETE"])
def api_auto_delete(aid):
    delete_auto(aid); return jsonify({"ok":True})

# ── Métricas / Webhooks ──────────────────────────────────────

@app.route("/api/metricas")
def api_metricas_globales():
    try:
        return jsonify(get_metricas_globales())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/metricas/campana/<cid>")
def api_metricas_campana(cid):
    try:
        data = get_metricas_campana(cid)
        if not data:
            return jsonify({"error": "Campaña no encontrada"}), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/metricas/test-evento", methods=["POST"])
def api_test_evento():
    data = request.get_json(silent=True) or {}
    save_webhook_event(
        evento=data.get("evento", "open"),
        tag=data.get("tag", ""),
        contact_id=str(data.get("contact_id", "test")),
        email=data.get("email", ""),
        url=data.get("url", ""),
        raw=data,
    )
    return jsonify({"ok": True})

@app.route("/api/webhook/ontraport", methods=["POST"])
def webhook_ontraport():
    """
    Receptor de webhooks de Ontraport en tiempo real.
    Configurar en Ontraport: Admin → Integrations → Webhooks
    URL: https://<tu-dominio>/api/webhook/ontraport
    Eventos: Email Opened, Link Clicked, Unsubscribed, Transaction
    """
    import logging
    log = logging.getLogger("webhook")

    data = request.get_json(silent=True) or request.form.to_dict()
    log.info("WEBHOOK recibido: %s", data)

    raw_evt = str(data.get("event_type") or data.get("type") or "").lower()
    evt_map = {
        "email_opened": "open", "email_open": "open", "open": "open",
        "link_clicked": "click", "email_click": "click", "click": "click",
        "unsubscribed": "unsub", "unsubscribe": "unsub",
        "hard_bounce": "bounce", "soft_bounce": "bounce", "bounce": "bounce",
        "transaction": "conversion", "purchase": "conversion", "product_purchase": "conversion",
    }
    evento = evt_map.get(raw_evt)
    if not evento:
        return jsonify({"ok": True, "skipped": True, "reason": f"event_type '{raw_evt}' not mapped"})

    save_webhook_event(
        evento=evento,
        tag=str(data.get("tag") or data.get("tag_name") or ""),
        contact_id=str(data.get("contact_id") or data.get("id") or ""),
        email=str(data.get("email") or data.get("contact_email") or ""),
        url=str(data.get("url") or data.get("link_url") or ""),
        raw=data,
    )
    return jsonify({"ok": True, "evento": evento})

# ── Integraciones ────────────────────────────────────────────

# ── Asistente de IA ──────────────────────────────────────────

import database as _db_module
from integrations.ai_assistant import chat as ai_chat

_ai_history = {}  # session_id → lista de mensajes

AI_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "integrations", "ai_config.json")

def _load_ai_key() -> str:
    # 1. Try config file
    try:
        with open(AI_CONFIG_PATH, "r") as f:
            key = json.load(f).get("openai_api_key", "").strip()
            if key:
                return key
    except Exception:
        pass
    # 2. Fallback to variable in ai_assistant.py
    try:
        from integrations.ai_assistant import OPENAI_API_KEY
        if OPENAI_API_KEY:
            return OPENAI_API_KEY
    except Exception:
        pass
    return ""

def _load_ai_model() -> str:
    try:
        with open(AI_CONFIG_PATH, "r") as f:
            return json.load(f).get("model", "gpt-4o-mini")
    except Exception:
        return "gpt-4o-mini"

def _save_ai_key(key: str, model: str = "gpt-4o-mini"):
    cfg = {"openai_api_key": key, "model": model}
    with open(AI_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


@app.route("/api/ai/key", methods=["GET"])
def api_ai_key_get():
    key = _load_ai_key()
    # Devolver solo si está configurada (nunca el valor real al frontend)
    return jsonify({"configured": bool(key), "preview": ("sk-..."+key[-4:]) if len(key)>8 else ""})


@app.route("/api/ai/key", methods=["POST"])
def api_ai_key_set():
    data  = request.get_json(silent=True) or {}
    key   = (data.get("key") or "").strip()
    model = (data.get("model") or "gpt-4o-mini").strip()
    if not key.startswith("sk-"):
        return jsonify({"error": "La API key debe empezar con sk-"}), 400
    _save_ai_key(key, model)
    return jsonify({"ok": True, "preview": "sk-..."+key[-4:], "model": model})


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    try:
        data    = request.get_json(silent=True) or {}
        session = data.get("session_id", "default")
        message = (data.get("message") or "").strip()

        if not message:
            return jsonify({"error": "Mensaje vacío"}), 400

        # API key siempre desde el archivo de config del servidor
        api_key = _load_ai_key()
        if not api_key:
            return jsonify({"error": "API Key de OpenAI no configurada. Ingresála en Ajustes del asistente."}), 400

        if session not in _ai_history:
            _ai_history[session] = []

        _ai_history[session].append({"role": "user", "content": message})
        history = _ai_history[session][-20:]

        model  = _load_ai_model()
        result = ai_chat(history, api_key, _db_module, model=model)

        if result.get("error"):
            return jsonify({"error": result["error"]}), 500

        _ai_history[session].append({"role": "assistant", "content": result["reply"]})

        return jsonify({
            "reply":      result["reply"],
            "tool_calls": result.get("tool_calls", []),
            "actions":    result.get("actions", []),
            "session_id": session,
        })
    except Exception as e:
        import traceback, logging
        logging.getLogger("api_ai_chat").error(traceback.format_exc())
        return jsonify({"error": f"Error interno: {str(e)}"}), 500


@app.route("/api/ai/reset", methods=["POST"])
def api_ai_reset():
    data    = request.get_json(silent=True) or {}
    session = data.get("session_id", "default")
    _ai_history.pop(session, None)
    return jsonify({"ok": True})


@app.route("/api/ai/descargar/<filename>")
def api_ai_descargar(filename):
    import re
    if not re.match(r'^[\w\-\.]+\.xlsx$', filename):
        return jsonify({"error": "Archivo inválido"}), 400
    path = os.path.join("uploads", filename)
    if not os.path.exists(path):
        return jsonify({"error": "Archivo no encontrado"}), 404
    return send_file(path, as_attachment=True, download_name=filename)



# ── Grupos ────────────────────────────────────────────────────────────────

@app.route("/api/grupos", methods=["GET"])
def api_grupos_get():
    import json as _j
    jur = request.args.get('jur', '').strip().upper()
    try:
        grupos = get_grupos()
        if not jur:
            return jsonify(grupos)
        # Paso 1: separar grupos con jur almacenada vs sin ella
        result, need_check = [], {}
        for g in grupos:
            try: stored = (_j.loads(g.get('filtros_json') or '{}').get('jur') or '').upper()
            except: stored = ''
            if stored == jur:
                result.append(g)
            elif not stored:
                nrodocs = _j.loads(g.get('nrodocs_json') or '[]')
                if not nrodocs:
                    result.append(g)   # grupo vacío → visible en todas las vistas
                else:
                    need_check[g['id']] = (g, nrodocs[:100])
        # Paso 2: batch-check jugadores para grupos sin jur almacenada
        if need_check:
            all_nd = list({n for _, nds in need_check.values() for n in nds})
            from database import get_conn
            with get_conn() as conn:
                if all_nd:
                    ph = ','.join('?'*len(all_nd))
                    jur_set = {r[0] for r in conn.execute(
                        f"SELECT nrodoc FROM jugadores WHERE nrodoc IN ({ph}) AND UPPER(jurisdiccion)=?",
                        all_nd+[jur]).fetchall()}
                else:
                    jur_set = set()
            for g, nds in need_check.values():
                if any(str(n) in jur_set for n in nds):
                    result.append(g)
        return jsonify(result)
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/grupos", methods=["POST"])
def api_grupos_post():
    data = request.get_json(silent=True) or {}
    try: return jsonify({"ok": True, "id": save_grupo(data)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/grupos/<int:gid>", methods=["GET"])
def api_grupo_get(gid):
    import json as _j
    g = get_grupo(gid)
    if not g: return jsonify({"error": "No encontrado"}), 404
    g["nrodocs"] = _j.loads(g.get("nrodocs_json") or "[]")
    # nrodocs_only=1 devuelve solo los IDs sin cargar perfiles (para acceso rápido)
    if request.args.get("nrodocs_only") == "1":
        g["jugadores"] = []
        return jsonify(g)
    jugs = [get_jugador_perfil(str(d)) for d in g["nrodocs"][:200]]
    g["jugadores"] = [j for j in jugs if j]
    return jsonify(g)

@app.route("/api/grupos/<int:gid>", methods=["PUT"])
def api_grupo_put(gid):
    data = request.get_json(silent=True) or {}
    try: update_grupo(gid, data); return jsonify({"ok": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/grupos/<int:gid>", methods=["DELETE"])
def api_grupo_delete(gid):
    delete_grupo(gid); return jsonify({"ok": True})

# ── Bonos extendidos ──────────────────────────────────────────────────────

@app.route("/api/bonos-ext", methods=["GET"])
def api_bonos_ext_get():
    return jsonify(get_bonos_ext(request.args.get("tipo", "")))

@app.route("/api/bonos-ext", methods=["POST"])
def api_bonos_ext_post():
    data = request.get_json(silent=True) or {}
    try: return jsonify({"ok": True, "id": save_bono_ext(data)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/bonos-ext/<int:bid>", methods=["DELETE"])
def api_bonos_ext_delete(bid):
    delete_bono_ext(bid); return jsonify({"ok": True})

# ── Reportes ──────────────────────────────────────────────────────────────

@app.route("/api/reportes", methods=["GET"])
def api_reportes_get():
    return jsonify(get_reportes())

@app.route("/api/reportes", methods=["POST"])
def api_reportes_post():
    data = request.get_json(silent=True) or {}
    try: return jsonify({"ok": True, "id": save_reporte(data)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/reportes/generar", methods=["POST"])
def api_reportes_generar():
    data = request.get_json(silent=True) or {}
    tipo = data.get("tipo", "completo")
    jur  = data.get("jur", "")
    dias = int(data.get("dias", 30))
    try:
        import pandas as pd, io as _io, os as _os
        from datetime import datetime as _dt, timedelta as _td
        buf    = _io.BytesIO()
        writer = pd.ExcelWriter(buf, engine="openpyxl")
        sheets = []
        if tipo in ("jugadores", "completo"):
            jugs = get_jugadores({"jur": jur}, 1, 9999)["jugadores"]
            if jugs: pd.DataFrame(jugs).to_excel(writer, sheet_name="Jugadores", index=False); sheets.append("Jugadores")
        if tipo in ("churn", "completo"):
            panel = get_churn_panel(jur)
            for lbl, rows in [("Churn Alto", panel["alto"]), ("Churn Medio", panel["medio"]), ("Churn Bajo", panel["bajo"])]:
                if rows: pd.DataFrame(rows).to_excel(writer, sheet_name=lbl, index=False); sheets.append(lbl)
        if tipo in ("campanas", "completo"):
            camps = get_campanas(jur, 500)
            if dias:
                cutoff = (_dt.now() - _td(days=dias)).strftime("%Y-%m-%d")
                camps  = [c for c in camps if (c.get("fecha", "")) >= cutoff]
            if camps: pd.DataFrame(camps).to_excel(writer, sheet_name="Campañas", index=False); sheets.append("Campañas")
        if tipo in ("bonos", "completo"):
            bonos = get_bonos_ext()
            if bonos: pd.DataFrame(bonos).to_excel(writer, sheet_name="Bonos", index=False); sheets.append("Bonos")
        writer.close(); buf.seek(0)
        _os.makedirs("uploads", exist_ok=True)
        fname = f"reporte_{tipo}_{_dt.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        fpath = _os.path.join("uploads", fname)
        with open(fpath, "wb") as f: f.write(buf.read())
        return jsonify({"ok": True, "filename": fname, "download_url": f"/api/ai/descargar/{fname}", "hojas": sheets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── VIP Management ────────────────────────────────────────────────────────

@app.route("/api/vip", methods=["GET"])
def api_vip_get():
    try: return jsonify(get_vip_players())
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/vip", methods=["POST"])
def api_vip_post():
    data = request.get_json(silent=True) or {}
    try: return jsonify({"ok": True, "id": save_vip_player(data)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/vip/<int:vid>", methods=["GET"])
def api_vip_detail(vid):
    v = get_vip_player(vid)
    if not v: return jsonify({"error": "No encontrado"}), 404
    v["notas"] = get_vip_notes(vid)
    v["tareas"] = get_vip_tasks(vid)
    return jsonify(v)

@app.route("/api/vip/<int:vid>", methods=["PUT"])
def api_vip_put(vid):
    data = request.get_json(silent=True) or {}
    try: update_vip_player(vid, data); return jsonify({"ok": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/vip/<int:vid>/notas", methods=["POST"])
def api_vip_nota(vid):
    data = request.get_json(silent=True) or {}
    data["vip_id"] = vid
    try: return jsonify({"ok": True, "id": save_vip_note(data)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/vip/<int:vid>/tareas", methods=["POST"])
def api_vip_tarea(vid):
    data = request.get_json(silent=True) or {}
    data["vip_id"] = vid
    try: return jsonify({"ok": True, "id": save_vip_task(data)})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/hosts", methods=["GET"])
def api_hosts_get():
    try: return jsonify(get_hosts())
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/hosts", methods=["POST"])
def api_hosts_post():
    data = request.get_json(silent=True) or {}
    try: return jsonify({"ok": True, "id": save_host(data)})
    except Exception as e: return jsonify({"error": str(e)}), 500

# ── Datos Financieros ─────────────────────────────────────────────────────

@app.route("/api/financiero/<nrodoc>", methods=["GET"])
def api_financiero_get(nrodoc):
    try: return jsonify(get_financial_data(nrodoc))
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/api/financiero/<nrodoc>", methods=["POST"])
def api_financiero_post(nrodoc):
    data = request.get_json(silent=True) or {}
    data["nrodoc"] = nrodoc
    try: save_financial_data(data); return jsonify({"ok": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

# ── Integraciones ─────────────────────────────────────────────────────────

@app.route("/api/integraciones/meta/pixel", methods=["POST"])
def api_meta_pixel():
    """Registra un evento del Pixel de META recibido desde el frontend"""
    data = request.get_json(silent=True) or {}
    import logging
    logging.getLogger("meta_pixel").info("Pixel event: %s", data)
    return jsonify({"ok": True, "event": data.get("event_name")})

@app.route("/api/integraciones/config", methods=["GET"])
def api_integraciones_config():
    """Devuelve el estado actual de las integraciones configuradas"""
    from integrations.ontraport import ONTRAPORT_API_KEY
    return jsonify({
        "ontraport": {"configurado": bool(ONTRAPORT_API_KEY), "app_id": "2_234387_..."},
        "emblue":    {"configurado": True},
        "meta_pixel": {"configurado": False, "pixel_id": ""},
        "biblioteca_juegos": {"configurado": False, "db_path": ""},
    })


# ── HTTP Proxy (para integración HTTP Request) ────────────────────────────────
HTTP_CFG_PATH = os.path.join(os.path.dirname(__file__), "integrations", "http_config.json")

def _load_http_cfg():
    try:
        with open(HTTP_CFG_PATH, "r", encoding="utf-8") as f: return json.load(f)
    except: return {"configs": [], "webhooks": []}

def _save_http_cfg(data):
    os.makedirs(os.path.dirname(HTTP_CFG_PATH), exist_ok=True)
    with open(HTTP_CFG_PATH, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)

@app.route("/api/http-config", methods=["GET"])
def api_http_cfg_get():
    return jsonify(_load_http_cfg())

@app.route("/api/http-config", methods=["POST"])
def api_http_cfg_post():
    import uuid as _uuid
    data = request.get_json(silent=True) or {}
    cfg  = _load_http_cfg()
    entry = {
        "id":         data.get("id") or _uuid.uuid4().hex[:8],
        "nombre":     (data.get("nombre") or "").strip(),
        "url":        (data.get("url") or "").strip(),
        "method":     data.get("method", "POST"),
        "auth_type":  data.get("auth_type", "none"),
        "auth_value": (data.get("auth_value") or "").strip(),
        "headers":    data.get("headers", "{}"),
        "body":       data.get("body", ""),
        "creado_en":  datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    if not entry["nombre"] or not entry["url"]:
        return jsonify({"error": "Nombre y URL son requeridos"}), 400
    # Update if exists, otherwise append
    idx = next((i for i,c in enumerate(cfg["configs"]) if c["id"]==entry["id"]), None)
    if idx is not None: cfg["configs"][idx] = entry
    else: cfg["configs"].append(entry)
    _save_http_cfg(cfg)
    return jsonify({"ok": True, "id": entry["id"]})

@app.route("/api/http-config/<cid>", methods=["DELETE"])
def api_http_cfg_del(cid):
    cfg = _load_http_cfg()
    cfg["configs"] = [c for c in cfg["configs"] if c["id"] != cid]
    _save_http_cfg(cfg)
    return jsonify({"ok": True})

@app.route("/api/http-config/webhooks", methods=["POST"])
def api_http_webhooks_post():
    import uuid as _uuid
    data = request.get_json(silent=True) or {}
    cfg  = _load_http_cfg()
    wh = {
        "id":     _uuid.uuid4().hex[:8],
        "url":    (data.get("url") or "").strip(),
        "events": data.get("events", []),
        "activo": True,
        "creado_en": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    if not wh["url"]: return jsonify({"error": "URL requerida"}), 400
    cfg.setdefault("webhooks", []).append(wh)
    _save_http_cfg(cfg)
    return jsonify({"ok": True, "id": wh["id"]})

@app.route("/api/http-config/webhooks/<wid>", methods=["DELETE"])
def api_http_webhooks_del(wid):
    cfg = _load_http_cfg()
    cfg["webhooks"] = [w for w in cfg.get("webhooks",[]) if w["id"] != wid]
    _save_http_cfg(cfg)
    return jsonify({"ok": True})

@app.route("/api/proxy-http", methods=["POST"])
def api_proxy_http():
    """Proxy para HTTP Request desde el frontend — evita CORS"""
    import requests as req
    data    = request.get_json(silent=True) or {}
    url     = data.get("url", "")
    method  = data.get("method", "POST").upper()
    headers = data.get("headers", {})
    body    = data.get("body", "")
    if not url or not url.startswith("http"):
        return jsonify({"ok": False, "error": "URL inválida"}), 400
    try:
        r = req.request(method, url, headers=headers,
                        data=body if body else None, timeout=15)
        try:    resp_body = r.json()
        except: resp_body = r.text[:2000]
        return jsonify({"ok": r.ok, "status": r.status_code, "response": resp_body})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── Auth endpoints ───────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data     = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "Usuario y contraseña requeridos"}), 400
    u = get_usuario_by_username(username)
    if not u or not check_password_hash(u["password_hash"], password):
        return jsonify({"error": "Usuario o contraseña incorrectos"}), 401
    session.permanent = True
    session["user_id"]  = u["id"]
    session["username"] = u["username"]
    session["user_rol"] = u["rol"]
    session["nombre"]   = u["nombre"] or u["username"]
    update_ultimo_login(u["id"])
    return jsonify({"ok": True, "user": {
        "id": u["id"], "username": u["username"],
        "nombre": u["nombre"] or u["username"],
        "rol": u["rol"], "email": u.get("email","")
    }})

@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/auth/me")
def api_auth_me():
    uid = session.get("user_id")
    if not uid:
        return jsonify({"authenticated": False}), 401
    u = get_usuario_by_id(uid)
    if not u or not u.get("activo"):
        session.clear()
        return jsonify({"authenticated": False}), 401
    return jsonify({"authenticated": True, "user": {
        "id": u["id"], "username": u["username"],
        "nombre": u["nombre"] or u["username"],
        "rol": u["rol"], "email": u.get("email",""),
        "ultimo_login": u.get("ultimo_login","")
    }})

# ── Gestión de usuarios (solo admin) ─────────────────────────

@app.route("/api/usuarios", methods=["GET"])
@admin_required
def api_usuarios_get():
    return jsonify(get_usuarios())

@app.route("/api/usuarios", methods=["POST"])
@admin_required
def api_usuarios_post():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "Usuario y contraseña requeridos"}), 400
    if len(password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres"}), 400
    existing = get_usuario_by_username(username)
    if existing:
        return jsonify({"error": f"El usuario '{username}' ya existe"}), 400
    uid = create_usuario({
        "username":      username,
        "email":         data.get("email",""),
        "nombre":        data.get("nombre",""),
        "password_hash": generate_password_hash(password),
        "rol":           data.get("rol","operador"),
    })
    return jsonify({"ok": True, "id": uid})

@app.route("/api/usuarios/<int:uid>", methods=["PUT"])
@admin_required
def api_usuario_put(uid):
    data = request.get_json(silent=True) or {}
    upd = {k: data[k] for k in ["email","nombre","rol","activo"] if k in data}
    if "password" in data and data["password"].strip():
        if len(data["password"].strip()) < 6:
            return jsonify({"error": "Contraseña demasiado corta (mínimo 6 caracteres)"}), 400
        upd["password_hash"] = generate_password_hash(data["password"].strip())
    update_usuario(uid, upd)
    return jsonify({"ok": True})

@app.route("/api/usuarios/<int:uid>", methods=["DELETE"])
@admin_required
def api_usuario_del(uid):
    if uid == session.get("user_id"):
        return jsonify({"error": "No podés eliminar tu propio usuario"}), 400
    delete_usuario(uid)
    return jsonify({"ok": True})

@app.route("/api/auth/cambiar-password", methods=["POST"])
@login_required
def api_cambiar_password():
    data    = request.get_json(silent=True) or {}
    actual  = (data.get("actual") or "").strip()
    nueva   = (data.get("nueva") or "").strip()
    if not actual or not nueva:
        return jsonify({"error": "Completá ambos campos"}), 400
    if len(nueva) < 6:
        return jsonify({"error": "La nueva contraseña debe tener al menos 6 caracteres"}), 400
    u = get_usuario_by_id(session["user_id"])
    if not u or not check_password_hash(u["password_hash"], actual):
        return jsonify({"error": "Contraseña actual incorrecta"}), 401
    update_usuario(session["user_id"], {"password_hash": generate_password_hash(nueva)})
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("\n  Super7 CRM v5  —  http://localhost:5000\n")
    app.run(debug=True, use_reloader=False, port=5000)
