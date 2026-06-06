"""
database.py — Super7 CRM v5
PostgreSQL persistente con upsert por DNI
"""

import psycopg2
import psycopg2.extras
import json, os
from datetime import datetime
from contextlib import contextmanager
from werkzeug.security import generate_password_hash

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
if _DATABASE_URL.startswith("postgres://"):
    _DATABASE_URL = _DATABASE_URL.replace("postgres://", "postgresql://", 1)


# ── Compatibility layer (sqlite3 → psycopg2) ─────────────────────────────────

class _Row(dict):
    """Dict that also supports positional [0] access (sqlite3.Row compat)."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _Cur:
    def __init__(self, cur):
        self._c = cur

    def fetchone(self):
        try:
            row = self._c.fetchone()
        except psycopg2.ProgrammingError:
            return None
        return _Row(row) if row is not None else None

    def fetchall(self):
        try:
            return [_Row(r) for r in self._c.fetchall()]
        except psycopg2.ProgrammingError:
            return []

    def __iter__(self):
        yield from self.fetchall()


class _Conn:
    """sqlite3-style interface over a psycopg2 connection."""
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        cur = self._raw.cursor()
        cur.execute(sql, params if params else None)
        return _Cur(cur)

    def executescript(self, sql):
        cur = self._raw.cursor()
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        return _Cur(cur)

    def commit(self):   self._raw.commit()
    def rollback(self): self._raw.rollback()
    def close(self):    self._raw.close()


@contextmanager
def get_conn():
    raw = psycopg2.connect(_DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn = _Conn(raw)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── DB init ───────────────────────────────────────────────────────────────────

_INIT_DDL = """
CREATE TABLE IF NOT EXISTS usuarios (
    id            SERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT DEFAULT '',
    password_hash TEXT NOT NULL,
    nombre        TEXT DEFAULT '',
    rol           TEXT DEFAULT 'operador',
    activo        INTEGER DEFAULT 1,
    created_at    TEXT,
    ultimo_login  TEXT
);
CREATE TABLE IF NOT EXISTS jugadores (
    nrodoc          TEXT PRIMARY KEY,
    jurisdiccion    TEXT,
    grupo           TEXT,
    subgrupo        TEXT,
    estado          TEXT,
    fecha_alta      TEXT,
    fecha_act       TEXT,
    usuario         TEXT,
    nombre          TEXT,
    apellido        TEXT,
    sexo            TEXT,
    edad            INTEGER DEFAULT 0,
    telefono        TEXT,
    email           TEXT,
    localidad       TEXT,
    provincia       TEXT,
    profesion       TEXT,
    total_cargas    INTEGER DEFAULT 0,
    total_retiros   INTEGER DEFAULT 0,
    monto_total     REAL    DEFAULT 0,
    monto_prom      REAL    DEFAULT 0,
    ftd             INTEGER DEFAULT 0,
    dias_inactivo   INTEGER DEFAULT 999,
    cargas_json     TEXT    DEFAULT '[]',
    churn_score     REAL    DEFAULT 0,
    tags_json       TEXT    DEFAULT '[]',
    updated_at      TEXT,
    created_at      TEXT
);
CREATE TABLE IF NOT EXISTS eventos_jugador (
    id          SERIAL PRIMARY KEY,
    nrodoc      TEXT,
    tipo        TEXT,
    descripcion TEXT,
    monto       REAL DEFAULT 0,
    metadata    TEXT DEFAULT '{}',
    fecha       TEXT,
    FOREIGN KEY(nrodoc) REFERENCES jugadores(nrodoc)
);
CREATE TABLE IF NOT EXISTS campanas (
    id              TEXT PRIMARY KEY,
    nombre          TEXT,
    canal           TEXT,
    estado          TEXT DEFAULT 'borrador',
    fecha           TEXT,
    fecha_envio     TEXT,
    total           INTEGER DEFAULT 0,
    enviados        INTEGER DEFAULT 0,
    errores         INTEGER DEFAULT 0,
    dry_run         INTEGER DEFAULT 1,
    filtros_json    TEXT    DEFAULT '{}',
    filtros_desc    TEXT,
    caba            INTEGER DEFAULT 0,
    mdz             INTEGER DEFAULT 0,
    mensaje_sms     TEXT,
    email_tag       TEXT,
    sms_flow        TEXT,
    aperturas       INTEGER DEFAULT 0,
    pct_apertura    REAL    DEFAULT 0,
    clicks          INTEGER DEFAULT 0,
    pct_clicks      REAL    DEFAULT 0,
    conversiones    INTEGER DEFAULT 0,
    pct_conv        REAL    DEFAULT 0,
    revenue         REAL    DEFAULT 0,
    created_at      TEXT
);
CREATE TABLE IF NOT EXISTS journeys (
    id          SERIAL PRIMARY KEY,
    nombre      TEXT,
    descripcion TEXT,
    activo      INTEGER DEFAULT 0,
    nodos_json  TEXT    DEFAULT '[]',
    edges_json  TEXT    DEFAULT '[]',
    ejecutados  INTEGER DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS journey_log (
    id          SERIAL PRIMARY KEY,
    journey_id  INTEGER,
    nrodoc      TEXT,
    nodo_id     TEXT,
    accion      TEXT,
    ok          INTEGER DEFAULT 1,
    fecha       TEXT,
    FOREIGN KEY(journey_id) REFERENCES journeys(id)
);
CREATE TABLE IF NOT EXISTS bonos (
    id          SERIAL PRIMARY KEY,
    nrodoc      TEXT,
    tipo_bono   TEXT,
    monto       REAL,
    fecha_asig  TEXT,
    fecha_exp   TEXT,
    usado       INTEGER DEFAULT 0,
    net_win     REAL    DEFAULT 0,
    campana_id  TEXT,
    FOREIGN KEY(nrodoc) REFERENCES jugadores(nrodoc)
);
CREATE TABLE IF NOT EXISTS automatizaciones (
    id              SERIAL PRIMARY KEY,
    nombre          TEXT,
    activa          INTEGER DEFAULT 1,
    trigger_tipo    TEXT,
    trigger_valor   INTEGER,
    condicion_json  TEXT    DEFAULT '{}',
    canal           TEXT,
    sms_flow        TEXT,
    ontraport_tag   TEXT,
    espera_horas    INTEGER DEFAULT 168,
    ejecutadas      INTEGER DEFAULT 0,
    ultima_ejec     TEXT,
    created_at      TEXT
);
CREATE TABLE IF NOT EXISTS sync_log (
    id           SERIAL PRIMARY KEY,
    fuente       TEXT,
    jur          TEXT,
    insertados   INTEGER DEFAULT 0,
    actualizados INTEGER DEFAULT 0,
    total        INTEGER DEFAULT 0,
    fecha        TEXT
);
CREATE INDEX IF NOT EXISTS idx_jug_jur   ON jugadores(jurisdiccion);
CREATE INDEX IF NOT EXISTS idx_jug_churn ON jugadores(churn_score DESC);
CREATE INDEX IF NOT EXISTS idx_jug_inact ON jugadores(dias_inactivo DESC);
CREATE INDEX IF NOT EXISTS idx_eventos   ON eventos_jugador(nrodoc);
CREATE INDEX IF NOT EXISTS idx_camp_date ON campanas(fecha DESC);
CREATE TABLE IF NOT EXISTS webhook_events (
    id          SERIAL PRIMARY KEY,
    evento      TEXT,
    tag         TEXT,
    contact_id  TEXT,
    email       TEXT,
    url         TEXT,
    campana_id  TEXT,
    raw_json    TEXT,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_wh_tag  ON webhook_events(tag);
CREATE INDEX IF NOT EXISTS idx_wh_evt  ON webhook_events(evento);
CREATE INDEX IF NOT EXISTS idx_wh_camp ON webhook_events(campana_id);
CREATE TABLE IF NOT EXISTS grupos (
    id           SERIAL PRIMARY KEY,
    nombre       TEXT NOT NULL,
    descripcion  TEXT,
    color        TEXT DEFAULT '#7c5cfc',
    icono        TEXT DEFAULT '👥',
    filtros_json TEXT DEFAULT '{}',
    nrodocs_json TEXT DEFAULT '[]',
    tipo         TEXT DEFAULT 'manual',
    total        INTEGER DEFAULT 0,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS bonos_recupero (
    id                    SERIAL PRIMARY KEY,
    nombre                TEXT,
    tipo                  TEXT DEFAULT 'recupero',
    monto                 REAL DEFAULT 0,
    porcentaje            REAL DEFAULT 0,
    condicion             TEXT,
    estado                TEXT DEFAULT 'activo',
    grupo_id              INTEGER,
    tag_ontraport         TEXT,
    jugadores_aplicados   INTEGER DEFAULT 0,
    monto_total_entregado REAL DEFAULT 0,
    created_at            TEXT
);
CREATE TABLE IF NOT EXISTS reportes (
    id           SERIAL PRIMARY KEY,
    nombre       TEXT,
    tipo         TEXT,
    filtros_json TEXT DEFAULT '{}',
    formato      TEXT DEFAULT 'excel',
    frecuencia   TEXT DEFAULT 'manual',
    ultimo_gen   TEXT,
    archivo_url  TEXT,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_bonos ON bonos(nrodoc);
CREATE TABLE IF NOT EXISTS vip_players (
    id                INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nrodoc            TEXT NOT NULL,
    tier              TEXT DEFAULT 'Silver',
    host_id           INTEGER,
    health_score      REAL DEFAULT 50,
    ltv_estimado      REAL DEFAULT 0,
    sentimiento       TEXT DEFAULT 'neutral',
    cumpleanos        TEXT,
    notas_count       INTEGER DEFAULT 0,
    tareas_pendientes INTEGER DEFAULT 0,
    ultimo_contacto   TEXT,
    proxima_accion    TEXT,
    regalo_enviado    INTEGER DEFAULT 0,
    viaje_programado  INTEGER DEFAULT 0,
    activo            INTEGER DEFAULT 1,
    created_at        TEXT,
    updated_at        TEXT
);
CREATE TABLE IF NOT EXISTS vip_notas (
    id      SERIAL PRIMARY KEY,
    vip_id  INTEGER NOT NULL,
    tipo    TEXT DEFAULT 'nota',
    texto   TEXT,
    autor   TEXT DEFAULT 'CRM',
    fecha   TEXT
);
CREATE TABLE IF NOT EXISTS vip_tareas (
    id          SERIAL PRIMARY KEY,
    vip_id      INTEGER NOT NULL,
    titulo      TEXT,
    descripcion TEXT,
    prioridad   TEXT DEFAULT 'media',
    estado      TEXT DEFAULT 'pendiente',
    vencimiento TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS hosts (
    id                SERIAL PRIMARY KEY,
    nombre            TEXT NOT NULL,
    email             TEXT,
    vips_asignados    INTEGER DEFAULT 0,
    performance_score REAL DEFAULT 0,
    activo            INTEGER DEFAULT 1,
    created_at        TEXT
);
CREATE TABLE IF NOT EXISTS datos_financieros (
    id                 SERIAL PRIMARY KEY,
    nrodoc             TEXT NOT NULL UNIQUE,
    total_depositos    REAL DEFAULT 0,
    total_retiros      REAL DEFAULT 0,
    ggr                REAL DEFAULT 0,
    ngr                REAL DEFAULT 0,
    bonos_recibidos    REAL DEFAULT 0,
    bonus_abuse_score  REAL DEFAULT 0,
    deposito_promedio  REAL DEFAULT 0,
    ltv                REAL DEFAULT 0,
    updated_at         TEXT
)
"""


def init_db():
    with get_conn() as conn:
        conn.executescript(_INIT_DDL)
    with get_conn() as conn:
        conn.execute("ALTER TABLE jugadores ADD COLUMN IF NOT EXISTS total_retiros INTEGER DEFAULT 0")
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
        if count == 0:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO usuarios (username,email,nombre,password_hash,rol,activo,created_at) VALUES (?,?,?,?,?,1,?)",
                ("moropeza@ivisa.com.ar", "moropeza@ivisa.com.ar", "Admin",
                 generate_password_hash("CRMSuper72026"), "admin", now)
            )
            print("[DB] Usuario admin creado por defecto")
    print("[DB] PostgreSQL inicializada")


# ────────────────────────────────────────────────────────────
#  CHURN SCORE
# ────────────────────────────────────────────────────────────

def calcular_churn(j: dict) -> float:
    score = 0.0
    dias = j.get("dias_inactivo", 0) or 0
    if   dias >= 60: score += 50
    elif dias >= 30: score += 35
    elif dias >= 14: score += 20
    elif dias >= 7:  score += 10
    tc = j.get("total_cargas", 0) or 0
    if   tc == 0:  score += 30
    elif tc == 1:  score += 15
    elif tc <= 3:  score += 5
    mp = j.get("monto_prom", 0) or 0
    if   mp == 0:     score += 20
    elif mp < 1000:   score += 10
    return min(round(score, 1), 100.0)


# ────────────────────────────────────────────────────────────
#  UPSERT JUGADORES
# ────────────────────────────────────────────────────────────

def upsert_jugadores(jugadores: list[dict], fuente: str, jur: str) -> dict:
    insertados = actualizados = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        for j in jugadores:
            nrodoc = str(j.get("nrodoc") or j.get("id") or "").strip()
            if not nrodoc or nrodoc in ("nan","None",""): continue
            row = {
                "nrodoc": nrodoc,
                "jurisdiccion": (j.get("jurisdiccion") or jur or "").strip(),
                "grupo":        j.get("grupo",""),
                "subgrupo":     j.get("subgrupo",""),
                "estado":       j.get("estado",""),
                "fecha_alta":   j.get("fecha_alta"),
                "fecha_act":    j.get("fecha_act"),
                "usuario":      j.get("usuario",""),
                "nombre":       j.get("nombre",""),
                "apellido":     j.get("apellido",""),
                "sexo":         j.get("sexo",""),
                "edad":         j.get("edad",0),
                "telefono":     j.get("telefono",""),
                "email":        j.get("email",""),
                "localidad":    j.get("localidad",""),
                "provincia":    j.get("provincia",""),
                "profesion":    j.get("profesion",""),
                "total_cargas":  j.get("total_cargas",0),
                "total_retiros": j.get("total_retiros",0),
                "monto_total":   j.get("monto_total",0),
                "monto_prom":   j.get("monto_prom",0),
                "ftd":          1 if j.get("ftd") else 0,
                "dias_inactivo":j.get("dias_inactivo",999),
                "cargas_json":  json.dumps(j.get("cargas",[])),
                "churn_score":  calcular_churn(j),
                "updated_at":   now,
            }
            exists = conn.execute("SELECT nrodoc FROM jugadores WHERE nrodoc=?", (nrodoc,)).fetchone()
            if exists:
                sets = ", ".join(f"{k}=%s" for k in row if k != "nrodoc")
                vals = [row[k] for k in row if k != "nrodoc"] + [nrodoc]
                conn.execute(f"UPDATE jugadores SET {sets} WHERE nrodoc=%s", vals)
                actualizados += 1
            else:
                row["created_at"] = now
                cols   = ", ".join(row.keys())
                params = ", ".join("%s" for _ in row)
                conn.execute(f"INSERT INTO jugadores ({cols}) VALUES ({params})", list(row.values()))
                conn.execute(
                    "INSERT INTO eventos_jugador (nrodoc,tipo,descripcion,fecha) VALUES (?,?,?,?)",
                    (nrodoc, "registro", f"Alta en {jur}", now)
                )
                insertados += 1

        conn.execute(
            "INSERT INTO sync_log (fuente,jur,insertados,actualizados,total,fecha) VALUES (?,?,?,?,?,?)",
            (fuente, jur, insertados, actualizados, insertados+actualizados, now)
        )
    return {"insertados": insertados, "actualizados": actualizados, "total": insertados+actualizados}


# ────────────────────────────────────────────────────────────
#  QUERIES JUGADORES
# ────────────────────────────────────────────────────────────

def _build_where(f: dict):
    conds, params = [], []
    if f.get("jur"):       conds.append("jurisdiccion=?");     params.append(f["jur"])
    if str(f.get("min_cargas","")) not in ("","None"):
        conds.append("total_cargas>=?"); params.append(int(f["min_cargas"]))
    mc = f.get("max_cargas")
    if mc is not None and str(mc) not in ("","None"):
        conds.append("total_cargas<=?"); params.append(int(mc))
    for k,col in [("grupo","grupo"),("subgrupo","subgrupo"),("provincia","provincia"),("estado","estado")]:
        if f.get(k): conds.append(f"{col}=?"); params.append(f[k])
    if f.get("sexo"):      conds.append("UPPER(sexo)=?");      params.append(f["sexo"].upper())
    if f.get("sin_ftd"):   conds.append("ftd=0")
    if f.get("con_ftd"):   conds.append("ftd=1")
    if str(f.get("min_dias","")) not in ("","None"):
        conds.append("dias_inactivo>=?"); params.append(int(f["min_dias"]))
    if str(f.get("max_dias","")) not in ("","0","None"):
        conds.append("dias_inactivo<=?"); params.append(int(f["max_dias"]))
    if str(f.get("churn_min","")) not in ("","None"):
        conds.append("churn_score>=?");  params.append(float(f["churn_min"]))
    if str(f.get("churn_max","")) not in ("","None"):
        conds.append("churn_score<=?");  params.append(float(f["churn_max"]))
    if f.get("q"):
        q = f"%{f['q']}%"
        conds.append("(nombre LIKE ? OR apellido LIKE ? OR email LIKE ? OR usuario LIKE ? OR nrodoc LIKE ?)")
        params.extend([q,q,q,q,q])
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


_ORDER_MAP = {
    "churn_desc":    "churn_score DESC, dias_inactivo DESC",
    "monto_desc":    "monto_prom DESC, total_cargas DESC",
    "monto_asc":     "monto_prom ASC",
    "cargas_desc":   "total_cargas DESC",
    "cargas_asc":    "total_cargas ASC",
    "inactivo_desc": "dias_inactivo DESC",
    "inactivo_asc":  "dias_inactivo ASC",
    "nombre_asc":    "apellido ASC, nombre ASC",
}


def get_jugadores_ids(f: dict) -> list:
    where, params = _build_where(f)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT nrodoc FROM jugadores {where} ORDER BY churn_score DESC, dias_inactivo DESC",
            params
        ).fetchall()
    return [r[0] for r in rows]


def get_jugadores(f: dict, page=1, per_page=50, orden: str = "churn_desc") -> dict:
    where, params = _build_where(f)
    order_clause = _ORDER_MAP.get(orden, _ORDER_MAP["churn_desc"])
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM jugadores {where}", params).fetchone()[0]
        rows  = conn.execute(
            f"SELECT * FROM jugadores {where} ORDER BY {order_clause} LIMIT ? OFFSET ?",
            params + [per_page, (page-1)*per_page]
        ).fetchall()
    jugadores = []
    for r in rows:
        j = dict(r)
        j["cargas"] = json.loads(j.get("cargas_json") or "[]")
        j["ftd"]    = bool(j["ftd"])
        j["tags"]   = json.loads(j.get("tags_json") or "[]")
        jugadores.append(j)
    return {"jugadores": jugadores, "total": total, "pages": max(1, -(-total // per_page))}


def get_jugador_perfil(nrodoc: str) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM jugadores WHERE nrodoc=?", (nrodoc,)).fetchone()
        if not r: return None
        j = dict(r)
        j["cargas"]  = json.loads(j.get("cargas_json") or "[]")
        j["ftd"]     = bool(j["ftd"])
        j["tags"]    = json.loads(j.get("tags_json") or "[]")
        j["eventos"] = [dict(e) for e in conn.execute(
            "SELECT * FROM eventos_jugador WHERE nrodoc=? ORDER BY fecha DESC LIMIT 50", (nrodoc,)
        ).fetchall()]
        j["bonos"]   = [dict(b) for b in conn.execute(
            "SELECT * FROM bonos WHERE nrodoc=? ORDER BY fecha_asig DESC", (nrodoc,)
        ).fetchall()]
        j["campanas_recibidas"] = [dict(c) for c in conn.execute(
            """SELECT c.id,c.nombre,c.canal,c.fecha FROM campanas c
               WHERE COALESCE(NULLIF(filtros_json,'')::json->>'jur','')=%s
                  OR COALESCE(NULLIF(filtros_json,'')::json->>'jur','')=''
               ORDER BY c.fecha DESC LIMIT 10""", (j["jurisdiccion"],)
        ).fetchall()]
        return j


def get_filtros_disponibles(jur="") -> dict:
    w  = "WHERE jurisdiccion=?" if jur else ""
    p  = [jur] if jur else []
    aw = "AND" if jur else "WHERE"
    with get_conn() as conn:
        def dist(col):
            return [r[0] for r in conn.execute(
                f"SELECT DISTINCT {col} FROM jugadores {w} {aw} {col}!='' AND {col} IS NOT NULL ORDER BY {col}",
                p
            ).fetchall() if r[0]]
        return {
            "grupos":     dist("grupo"),
            "subgrupos":  dist("subgrupo"),
            "provincias": dist("provincia"),
            "estados":    dist("estado"),
        }


# ────────────────────────────────────────────────────────────
#  STATS DASHBOARD
# ────────────────────────────────────────────────────────────

def get_stats(jur="") -> dict:
    w  = "WHERE jurisdiccion=?" if jur else ""
    p  = [jur] if jur else []
    aw = "AND" if jur else "WHERE"
    with get_conn() as conn:
        base = conn.execute(
            f"SELECT COUNT(*) t, SUM(ftd) ftd, SUM(monto_total) mt, SUM(total_retiros) tr, "
            f"AVG(CASE WHEN total_cargas>0 THEN monto_prom ELSE NULL END) tp, "
            f"AVG(churn_score) cs FROM jugadores {w}", p).fetchone()
        total, con_ftd = base["t"] or 0, base["ftd"] or 0
        monto_tot   = round(base["mt"] or 0, 2)
        monto_ret   = round(base["tr"] or 0, 2)
        ticket_prom = round(base["tp"] or 0, 0)
        avg_churn   = round(base["cs"] or 0, 1)

        if not total: return {"sin_datos": True}

        ret_d1  = conn.execute(f"SELECT COUNT(*) FROM jugadores {w} {aw} dias_inactivo<=1  AND dias_inactivo<999", p).fetchone()[0]
        ret_d7  = conn.execute(f"SELECT COUNT(*) FROM jugadores {w} {aw} dias_inactivo<=7  AND dias_inactivo<999", p).fetchone()[0]
        ret_d30 = conn.execute(f"SELECT COUNT(*) FROM jugadores {w} {aw} dias_inactivo<=30 AND dias_inactivo<999", p).fetchone()[0]

        caba_n = conn.execute("SELECT COUNT(*) FROM jugadores WHERE jurisdiccion='CABA'").fetchone()[0] if not jur else (total if jur=="CABA" else 0)
        mdz_n  = conn.execute("SELECT COUNT(*) FROM jugadores WHERE jurisdiccion='MDZ'").fetchone()[0]  if not jur else (total if jur=="MDZ"  else 0)

        churn_alto  = conn.execute(f"SELECT COUNT(*) FROM jugadores {w} {aw} churn_score>=70", p).fetchone()[0]
        churn_medio = conn.execute(f"SELECT COUNT(*) FROM jugadores {w} {aw} churn_score>=40 AND churn_score<70", p).fetchone()[0]
        churn_bajo  = conn.execute(f"SELECT COUNT(*) FROM jugadores {w} {aw} churn_score<40", p).fetchone()[0]
        cdb         = conn.execute(f"SELECT COUNT(*) FROM jugadores {w} {aw} subgrupo LIKE '%CdB%'", p).fetchone()[0]

        def grp(col): return [{"g":r[0]or"—","n":r[1]} for r in conn.execute(f"SELECT {col}, COUNT(*) FROM jugadores {w} GROUP BY {col} ORDER BY {col}", p).fetchall()]

        bins  = [("0–3d",0,3),("4–7d",4,7),("8–14d",8,14),("15–30d",15,30),("31–60d",31,60),("60+d",61,9999)]
        inact = [{"r":l,"n":conn.execute(f"SELECT COUNT(*) FROM jugadores {w} {aw} dias_inactivo BETWEEN ? AND ?", p+[lo,hi]).fetchone()[0]} for l,lo,hi in bins]

        rangos_m = [("$0",0,0),("$1–999",1,999),("$1k–4.9k",1000,4999),("$5k–9.9k",5000,9999),("$10k+",10000,9999999)]
        dist_m   = [{"r":lb,"n":conn.execute(f"SELECT COUNT(*) FROM jugadores {w} {aw} monto_prom BETWEEN ? AND ? AND ftd=1", p+[lo,hi]).fetchone()[0]} for lb,lo,hi in rangos_m]

        provs = [{"p":r[0]or"—","n":r[1]} for r in conn.execute(f"SELECT provincia, COUNT(*) n FROM jugadores {w} GROUP BY provincia ORDER BY n DESC LIMIT 8", p).fetchall()]

        _jur_sql = "COALESCE(NULLIF(filtros_json,'')::json->>'jur','')"
        if jur:
            camps = [dict(r) for r in conn.execute(
                f"SELECT * FROM campanas WHERE dry_run=0 AND ({_jur_sql}=%s OR {_jur_sql}='') ORDER BY fecha DESC LIMIT 20",
                (jur,)
            ).fetchall()]
        else:
            camps = [dict(r) for r in conn.execute("SELECT * FROM campanas WHERE dry_run=0 ORDER BY fecha DESC LIMIT 20").fetchall()]

        env_total = sum(c["enviados"] for c in camps)
        return {
            "total":total,"con_ftd":con_ftd,"sin_ftd":total-con_ftd,
            "pct_ftd":round(con_ftd/total*100,1) if total else 0,
            "monto_total":monto_tot,"monto_prom":round(monto_tot/con_ftd,0) if con_ftd else 0,
            "total_retiros":monto_ret,"ticket_prom":ticket_prom,
            "ret_d1":ret_d1,"ret_d7":ret_d7,"ret_d30":ret_d30,
            "pct_ret_d1":round(ret_d1/total*100,1) if total else 0,
            "pct_ret_d7":round(ret_d7/total*100,1) if total else 0,
            "pct_ret_d30":round(ret_d30/total*100,1) if total else 0,
            "cdb":cdb,"caba_n":caba_n,"mdz_n":mdz_n,"avg_churn":avg_churn,
            "churn_alto":churn_alto,"churn_medio":churn_medio,"churn_bajo":churn_bajo,
            "grupos":grp("grupo"),"subgrupos":grp("subgrupo"),"provincias":provs,
            "inactividad":inact,"dist_monto":dist_m,
            "camps_total":len(camps),"total_enviados":env_total,
            "pct_apertura":round(sum(c["aperturas"] for c in camps)/env_total*100,1) if env_total else 0,
            "pct_clicks":round(sum(c["clicks"] for c in camps)/env_total*100,1) if env_total else 0,
            "pct_conv":round(sum(c["conversiones"] for c in camps)/env_total*100,1) if env_total else 0,
            "campanas_recientes":camps[:6],
            "last_sync": dict(conn.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 1").fetchone() or {}),
        }


def get_churn_panel(jur="") -> dict:
    w  = "WHERE jurisdiccion=?" if jur else ""
    p  = [jur] if jur else []
    aw = "AND" if jur else "WHERE"
    cols = "nrodoc,nombre,apellido,jurisdiccion,grupo,total_cargas,monto_prom,dias_inactivo,churn_score,email,telefono,usuario"
    with get_conn() as conn:
        def fetch(cond, extra_p=[]):
            return [dict(r) for r in conn.execute(f"SELECT {cols} FROM jugadores {w} {aw} {cond} ORDER BY churn_score DESC LIMIT 200", p+extra_p).fetchall()]
        alto  = fetch("churn_score>=70")
        medio = fetch("churn_score>=40 AND churn_score<70")
        bajo  = fetch("churn_score<40")
    return {"alto":alto,"medio":medio,"bajo":bajo,"total_alto":len(alto),"total_medio":len(medio),"total_bajo":len(bajo)}


# ────────────────────────────────────────────────────────────
#  CAMPAÑAS
# ────────────────────────────────────────────────────────────

def save_campana(data: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cols = ["id","nombre","canal","estado","fecha","total","enviados","errores","dry_run",
            "filtros_json","filtros_desc","caba","mdz","mensaje_sms","email_tag","sms_flow","created_at"]
    vals = [
        data.get("id"), data.get("nombre"), data.get("canal"), data.get("estado"),
        data.get("fecha"), data.get("total",0), data.get("enviados",0), data.get("errores",0),
        data.get("dry_run",1), data.get("filtros_json","{}"), data.get("filtros_desc",""),
        data.get("caba",0), data.get("mdz",0), data.get("mensaje_sms",""),
        data.get("email_tag",""), data.get("sms_flow",""), now,
    ]
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "id")
    ph = ", ".join("%s" for _ in cols)
    sql = f"INSERT INTO campanas ({','.join(cols)}) VALUES ({ph}) ON CONFLICT (id) DO UPDATE SET {updates}"
    with get_conn() as conn:
        conn.execute(sql, vals)
    return data["id"]

def get_campanas(jur="", limit=100) -> list:
    _jur_sql = "COALESCE(NULLIF(filtros_json,'')::json->>'jur','')"
    with get_conn() as conn:
        if jur:
            rows = conn.execute(
                f"SELECT * FROM campanas WHERE ({_jur_sql}=%s OR {_jur_sql}='' OR filtros_json IS NULL) ORDER BY fecha DESC LIMIT %s",
                (jur, limit)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM campanas ORDER BY fecha DESC LIMIT %s", (limit,)).fetchall()
    return [dict(r) for r in rows]

def update_metricas(cid: str, data: dict):
    env = max(data.get("enviados_ref",1), 1)
    with get_conn() as conn:
        conn.execute("""UPDATE campanas SET aperturas=?,pct_apertura=?,clicks=?,pct_clicks=?,
                        conversiones=?,pct_conv=?,revenue=? WHERE id=?""",
            (data.get("aperturas",0), round(data.get("aperturas",0)/env*100,1),
             data.get("clicks",0),    round(data.get("clicks",0)/env*100,1),
             data.get("conversiones",0), round(data.get("conversiones",0)/env*100,1),
             data.get("revenue",0), cid))


# ────────────────────────────────────────────────────────────
#  MÉTRICAS / WEBHOOK
# ────────────────────────────────────────────────────────────

def save_webhook_event(evento: str, tag: str, contact_id: str,
                       email: str = "", url: str = "", raw: dict = None) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM campanas
               WHERE email_tag=? AND dry_run=0
               ORDER BY fecha DESC LIMIT 1""",
            (tag,)
        ).fetchone()
        campana_id = row["id"] if row else None

        conn.execute(
            """INSERT INTO webhook_events
               (evento,tag,contact_id,email,url,campana_id,raw_json,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (evento, tag, str(contact_id), email, url,
             campana_id, json.dumps(raw or {}), now)
        )
        if campana_id:
            if evento == "open":
                conn.execute("UPDATE campanas SET aperturas=aperturas+1 WHERE id=?", (campana_id,))
            elif evento == "click":
                conn.execute("UPDATE campanas SET clicks=clicks+1 WHERE id=?", (campana_id,))
            elif evento in ("conversion","purchase"):
                conn.execute("UPDATE campanas SET conversiones=conversiones+1 WHERE id=?", (campana_id,))
            row2 = conn.execute(
                "SELECT enviados,aperturas,clicks,conversiones FROM campanas WHERE id=?",
                (campana_id,)
            ).fetchone()
            if row2 and row2["enviados"] > 0:
                env = row2["enviados"]
                conn.execute(
                    """UPDATE campanas SET
                        pct_apertura=?, pct_clicks=?, pct_conv=?
                        WHERE id=?""",
                    (round(row2["aperturas"]/env*100,1),
                     round(row2["clicks"]/env*100,1),
                     round(row2["conversiones"]/env*100,1),
                     campana_id)
                )

def get_metricas_globales() -> dict:
    with get_conn() as conn:
        camps = [dict(r) for r in conn.execute(
            "SELECT * FROM campanas WHERE dry_run=0 ORDER BY fecha DESC"
        ).fetchall()]

        total_env  = sum(c.get("enviados",0) for c in camps)
        total_ap   = sum(c.get("aperturas",0) for c in camps)
        total_cl   = sum(c.get("clicks",0) for c in camps)
        total_conv = sum(c.get("conversiones",0) for c in camps)
        total_rev  = sum(c.get("revenue",0.0) for c in camps)

        eventos_recientes = [dict(r) for r in conn.execute(
            """SELECT evento, COUNT(*) as cnt
               FROM webhook_events
               WHERE created_at >= to_char(NOW() - INTERVAL '30 days', 'YYYY-MM-DD')
               GROUP BY evento ORDER BY cnt DESC"""
        ).fetchall()]

        timeline = [dict(r) for r in conn.execute(
            """SELECT LEFT(created_at,10) as dia, COUNT(*) as cnt
               FROM webhook_events WHERE evento='open'
               AND created_at >= to_char(NOW() - INTERVAL '14 days', 'YYYY-MM-DD')
               GROUP BY LEFT(created_at,10) ORDER BY dia"""
        ).fetchall()]

        top_tags = [dict(r) for r in conn.execute(
            """SELECT tag, COUNT(*) as aperturas
               FROM webhook_events WHERE evento='open'
               GROUP BY tag ORDER BY aperturas DESC LIMIT 10"""
        ).fetchall()]

        return {
            "total_campanas": len(camps),
            "total_enviados": total_env,
            "total_aperturas": total_ap,
            "total_clicks": total_cl,
            "total_conversiones": total_conv,
            "total_revenue": total_rev,
            "pct_apertura": round(total_ap/total_env*100,1) if total_env else 0,
            "pct_clicks":   round(total_cl/total_env*100,1) if total_env else 0,
            "pct_conv":     round(total_conv/total_env*100,1) if total_env else 0,
            "eventos_recientes": eventos_recientes,
            "timeline_opens": timeline,
            "top_tags": top_tags,
            "campanas": camps,
        }

def get_metricas_campana(campana_id: str) -> dict:
    with get_conn() as conn:
        camp = conn.execute("SELECT * FROM campanas WHERE id=?", (campana_id,)).fetchone()
        if not camp:
            return {}
        camp = dict(camp)

        eventos = [dict(r) for r in conn.execute(
            """SELECT evento, COUNT(*) as cnt, MIN(created_at) as primero,
               MAX(created_at) as ultimo
               FROM webhook_events WHERE campana_id=?
               GROUP BY evento""",
            (campana_id,)
        ).fetchall()]

        timeline = [dict(r) for r in conn.execute(
            """SELECT LEFT(created_at,10) as dia, evento, COUNT(*) as cnt
               FROM webhook_events WHERE campana_id=?
               GROUP BY LEFT(created_at,10), evento ORDER BY dia""",
            (campana_id,)
        ).fetchall()]

        top_links = [dict(r) for r in conn.execute(
            """SELECT url, COUNT(*) as clicks
               FROM webhook_events WHERE campana_id=? AND evento='click' AND url!=''
               GROUP BY url ORDER BY clicks DESC LIMIT 10""",
            (campana_id,)
        ).fetchall()]

        return {**camp, "eventos": eventos, "timeline": timeline, "top_links": top_links}

# ────────────────────────────────────────────────────────────
#  JOURNEYS
# ────────────────────────────────────────────────────────────

def save_journey(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        if data.get("id"):
            conn.execute("""UPDATE journeys SET nombre=?,descripcion=?,nodos_json=?,edges_json=?,activo=?,updated_at=? WHERE id=?""",
                (data["nombre"], data.get("descripcion",""), json.dumps(data.get("nodos",[])),
                 json.dumps(data.get("edges",[])), 1 if data.get("activo") else 0, now, data["id"]))
            return data["id"]
        else:
            cur = conn.execute(
                """INSERT INTO journeys (nombre,descripcion,nodos_json,edges_json,activo,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?) RETURNING id""",
                (data["nombre"], data.get("descripcion",""), json.dumps(data.get("nodos",[])),
                 json.dumps(data.get("edges",[])), 0, now, now))
            return cur.fetchone()[0]

def get_journeys() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM journeys ORDER BY id DESC").fetchall()
    result = []
    for r in rows:
        j = dict(r)
        j["nodos"] = json.loads(j.get("nodos_json") or "[]")
        j["edges"] = json.loads(j.get("edges_json") or "[]")
        result.append(j)
    return result

def get_journey(jid: int) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM journeys WHERE id=?", (jid,)).fetchone()
        if not r: return None
        j = dict(r)
        j["nodos"] = json.loads(j.get("nodos_json") or "[]")
        j["edges"] = json.loads(j.get("edges_json") or "[]")
        return j

def toggle_journey(jid: int, activo: bool):
    with get_conn() as conn:
        conn.execute("UPDATE journeys SET activo=? WHERE id=?", (1 if activo else 0, jid))

def delete_journey(jid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM journeys WHERE id=?", (jid,))


# ────────────────────────────────────────────────────────────
#  BONOS
# ────────────────────────────────────────────────────────────

def save_bono(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO bonos (nrodoc,tipo_bono,monto,fecha_asig,fecha_exp,campana_id) VALUES (?,?,?,?,?,?) RETURNING id",
            (data["nrodoc"], data["tipo_bono"], data["monto"], now, data.get("fecha_exp"), data.get("campana_id"))
        )
        return cur.fetchone()[0]

def get_bonos_stats(jur="") -> dict:
    with get_conn() as conn:
        if jur:
            rows = conn.execute("SELECT b.*,j.jurisdiccion,j.nombre,j.apellido FROM bonos b JOIN jugadores j ON b.nrodoc=j.nrodoc WHERE j.jurisdiccion=? ORDER BY b.fecha_asig DESC LIMIT 200", (jur,)).fetchall()
        else:
            rows = conn.execute("SELECT b.*,j.jurisdiccion,j.nombre,j.apellido FROM bonos b JOIN jugadores j ON b.nrodoc=j.nrodoc ORDER BY b.fecha_asig DESC LIMIT 200").fetchall()
        bonos = [dict(r) for r in rows]
        return {
            "bonos": bonos,
            "total_bonos": len(bonos),
            "total_asignado": sum(b["monto"] or 0 for b in bonos),
            "total_usado": sum(b["monto"] or 0 for b in bonos if b["usado"]),
            "roi": sum(b["net_win"] or 0 for b in bonos),
        }


# ────────────────────────────────────────────────────────────
#  AUTOMATIZACIONES
# ────────────────────────────────────────────────────────────

def save_auto(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO automatizaciones
            (nombre,activa,trigger_tipo,trigger_valor,condicion_json,canal,sms_flow,ontraport_tag,espera_horas,created_at)
            VALUES (?,1,?,?,?,?,?,?,?,?) RETURNING id""",
            (data["nombre"], data["trigger_tipo"], data.get("trigger_valor",0),
             json.dumps(data.get("condicion",{})), data["canal"],
             data.get("sms_flow",""), data.get("ontraport_tag",""), data.get("espera_horas",168), now))
        return cur.fetchone()[0]

def get_autos() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM automatizaciones ORDER BY id DESC").fetchall()]

def toggle_auto(aid: int, activa: bool):
    with get_conn() as conn:
        conn.execute("UPDATE automatizaciones SET activa=? WHERE id=?", (1 if activa else 0, aid))

def delete_auto(aid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM automatizaciones WHERE id=?", (aid,))


# ── GRUPOS ────────────────────────────────────────────────────────────────────

def save_grupo(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nrodocs = data.get("nrodocs", [])
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO grupos (nombre,descripcion,color,icono,filtros_json,nrodocs_json,tipo,total,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            (data["nombre"], data.get("descripcion",""), data.get("color","#7c5cfc"),
             data.get("icono","👥"), json.dumps(data.get("filtros",{})),
             json.dumps(nrodocs), data.get("tipo","manual"),
             len(nrodocs), now, now)
        )
        return cur.fetchone()[0]

def get_grupos() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM grupos ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

def get_grupo(gid: int) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM grupos WHERE id=?", (gid,)).fetchone()
        return dict(r) if r else None

def delete_grupo(gid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM grupos WHERE id=?", (gid,))

def update_grupo(gid: int, data: dict):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nrodocs = data.get("nrodocs", [])
    with get_conn() as conn:
        conn.execute(
            """UPDATE grupos SET nombre=?,descripcion=?,color=?,icono=?,
               filtros_json=?,nrodocs_json=?,total=?,updated_at=? WHERE id=?""",
            (data["nombre"], data.get("descripcion",""), data.get("color","#7c5cfc"),
             data.get("icono","👥"), json.dumps(data.get("filtros",{})),
             json.dumps(nrodocs), len(nrodocs), now, gid)
        )

# ── BONOS EXTENDIDOS ──────────────────────────────────────────────────────────

def save_bono_ext(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO bonos_recupero
               (nombre,tipo,monto,porcentaje,condicion,estado,grupo_id,tag_ontraport,created_at)
               VALUES (?,?,?,?,?,?,?,?,?) RETURNING id""",
            (data.get("nombre",""), data.get("tipo","recupero"),
             float(data.get("monto",0)), float(data.get("porcentaje",0)),
             data.get("condicion",""), data.get("estado","activo"),
             data.get("grupo_id"), data.get("tag_ontraport",""), now)
        )
        return cur.fetchone()[0]

def get_bonos_ext(tipo="") -> list:
    with get_conn() as conn:
        if tipo:
            rows = conn.execute("SELECT * FROM bonos_recupero WHERE tipo=? ORDER BY created_at DESC", (tipo,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM bonos_recupero ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

def delete_bono_ext(bid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM bonos_recupero WHERE id=?", (bid,))

# ── REPORTES ──────────────────────────────────────────────────────────────────

def save_reporte(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO reportes (nombre,tipo,filtros_json,formato,frecuencia,created_at)
               VALUES (?,?,?,?,?,?) RETURNING id""",
            (data["nombre"], data["tipo"], json.dumps(data.get("filtros",{})),
             data.get("formato","excel"), data.get("frecuencia","manual"), now)
        )
        return cur.fetchone()[0]

def get_reportes() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM reportes ORDER BY created_at DESC").fetchall()]

def export_jugadores(f: dict) -> list:
    return get_jugadores(f, page=1, per_page=999999)["jugadores"]

# ── VIP MANAGEMENT ────────────────────────────────────────────────────────────

def _ensure_vip_tables():
    pass  # all tables now created in init_db via _INIT_DDL


try:
    _ensure_vip_tables()
except Exception:
    pass

def save_vip_player(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO vip_players
               (nrodoc,tier,host_id,health_score,ltv_estimado,sentimiento,
                cumpleanos,ultimo_contacto,proxima_accion,activo,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,1,?,?) RETURNING id""",
            (data["nrodoc"], data.get("tier","Silver"), data.get("host_id"),
             float(data.get("health_score",50)), float(data.get("ltv_estimado",0)),
             data.get("sentimiento","neutral"), data.get("cumpleanos",""),
             data.get("ultimo_contacto",""), data.get("proxima_accion",""), now, now)
        )
        return cur.fetchone()[0]

def get_vip_players() -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT v.*, j.nombre, j.apellido, j.email, j.jurisdiccion,
                   j.total_cargas, j.monto_prom, j.dias_inactivo, j.churn_score,
                   h.nombre as host_nombre
            FROM vip_players v
            LEFT JOIN jugadores j ON j.nrodoc = v.nrodoc
            LEFT JOIN hosts h ON h.id = v.host_id
            ORDER BY v.health_score DESC
        """).fetchall()
        return [dict(r) for r in rows]

def get_vip_player(vid: int) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("""
            SELECT v.*, j.nombre, j.apellido, j.email, j.telefono,
                   j.jurisdiccion, j.total_cargas, j.monto_prom,
                   j.dias_inactivo, j.churn_score, h.nombre as host_nombre
            FROM vip_players v
            LEFT JOIN jugadores j ON j.nrodoc = v.nrodoc
            LEFT JOIN hosts h ON h.id = v.host_id
            WHERE v.id=?""", (vid,)).fetchone()
        return dict(r) if r else None

def update_vip_player(vid: int, data: dict):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        fields = []
        vals   = []
        for k in ["tier","health_score","sentimiento","proxima_accion","host_id",
                  "ltv_estimado","ultimo_contacto","regalo_enviado","viaje_programado"]:
            if k in data:
                fields.append(f"{k}=?")
                vals.append(data[k])
        if not fields: return
        vals += [now, vid]
        conn.execute(f"UPDATE vip_players SET {','.join(fields)},updated_at=? WHERE id=?", vals)

def save_vip_note(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO vip_notas (vip_id,tipo,texto,autor,fecha) VALUES (?,?,?,?,?) RETURNING id",
            (data["vip_id"], data.get("tipo","nota"), data.get("texto",""),
             data.get("autor","CRM"), now)
        )
        conn.execute("UPDATE vip_players SET notas_count=notas_count+1, ultimo_contacto=? WHERE id=?",
                     (now, data["vip_id"]))
        return cur.fetchone()[0]

def get_vip_notes(vid: int) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM vip_notas WHERE vip_id=? ORDER BY fecha DESC", (vid,)).fetchall()]

def save_vip_task(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO vip_tareas (vip_id,titulo,descripcion,prioridad,estado,vencimiento,created_at)
               VALUES (?,?,?,?,?,?,?) RETURNING id""",
            (data["vip_id"], data.get("titulo",""), data.get("descripcion",""),
             data.get("prioridad","media"), "pendiente",
             data.get("vencimiento",""), now)
        )
        return cur.fetchone()[0]

def get_vip_tasks(vid: int) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM vip_tareas WHERE vip_id=? ORDER BY prioridad DESC, created_at DESC", (vid,)).fetchall()]

def save_host(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO hosts (nombre,email,activo,created_at) VALUES (?,?,1,?) RETURNING id",
            (data["nombre"], data.get("email",""), now)
        )
        return cur.fetchone()[0]

def get_hosts() -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM hosts WHERE activo=1 ORDER BY nombre").fetchall()]

def get_financial_data(nrodoc: str) -> dict:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM datos_financieros WHERE nrodoc=?", (nrodoc,)).fetchone()
        if r: return dict(r)
        j = conn.execute("SELECT * FROM jugadores WHERE nrodoc=?", (nrodoc,)).fetchone()
        if not j: return {}
        j = dict(j)
        monto = float(j.get("monto_prom") or 0)
        cargas = int(j.get("total_cargas") or 0)
        total_dep = monto * cargas
        return {
            "nrodoc": nrodoc,
            "total_depositos":  round(total_dep, 2),
            "total_retiros":    0,
            "ggr":              round(total_dep * 0.05, 2),
            "ngr":              round(total_dep * 0.04, 2),
            "bonos_recibidos":  0,
            "bonus_abuse_score": 0,
            "deposito_promedio": monto,
            "ltv":              round(total_dep * 1.2, 2),
        }

def save_financial_data(data: dict):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO datos_financieros
            (nrodoc,total_depositos,total_retiros,ggr,ngr,bonos_recibidos,
             bonus_abuse_score,deposito_promedio,ltv,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (nrodoc) DO UPDATE SET
                total_depositos=EXCLUDED.total_depositos,
                total_retiros=EXCLUDED.total_retiros,
                ggr=EXCLUDED.ggr, ngr=EXCLUDED.ngr,
                bonos_recibidos=EXCLUDED.bonos_recibidos,
                bonus_abuse_score=EXCLUDED.bonus_abuse_score,
                deposito_promedio=EXCLUDED.deposito_promedio,
                ltv=EXCLUDED.ltv, updated_at=EXCLUDED.updated_at""",
            (data["nrodoc"],
             float(data.get("total_depositos",0)),  float(data.get("total_retiros",0)),
             float(data.get("ggr",0)),               float(data.get("ngr",0)),
             float(data.get("bonos_recibidos",0)),   float(data.get("bonus_abuse_score",0)),
             float(data.get("deposito_promedio",0)), float(data.get("ltv",0)),
             now))


# ── USUARIOS / AUTH ────────────────────────────────────────────────────────────

def get_usuario_by_username(username: str) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM usuarios WHERE username=? AND activo=1", (username,)).fetchone()
        return dict(r) if r else None

def get_usuario_by_id(uid: int) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None

def get_usuarios() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT id,username,email,nombre,rol,activo,created_at,ultimo_login FROM usuarios ORDER BY id").fetchall()
        return [dict(r) for r in rows]

def create_usuario(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO usuarios (username,email,nombre,password_hash,rol,activo,created_at) VALUES (?,?,?,?,?,1,?) RETURNING id",
            (data["username"], data.get("email",""), data.get("nombre",""),
             data["password_hash"], data.get("rol","operador"), now)
        )
        return cur.fetchone()[0]

def update_usuario(uid: int, data: dict):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fields, vals = [], []
    for k in ["email","nombre","rol","activo"]:
        if k in data: fields.append(f"{k}=?"); vals.append(data[k])
    if "password_hash" in data: fields.append("password_hash=?"); vals.append(data["password_hash"])
    if not fields: return
    vals += [now, uid]
    with get_conn() as conn:
        conn.execute(f"UPDATE usuarios SET {','.join(fields)},ultimo_login=? WHERE id=?", vals)

def update_ultimo_login(uid: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("UPDATE usuarios SET ultimo_login=? WHERE id=?", (now, uid))

def delete_usuario(uid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM usuarios WHERE id=?", (uid,))
