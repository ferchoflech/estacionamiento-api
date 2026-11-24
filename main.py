from datetime import datetime
import math
import sqlite3
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Estacionamiento Zoológico de León API", version="1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "estacionamiento_zoologico.db"
CAPACIDAD_MAXIMA = 50
TARIFA_EXTRAVIADO = 250.0  # Tarifa fija por boleto extraviado / perdido

TARIFAS_VEHICULO = {
    "MOTO": 12.0,
    "AUTOMOVIL": 31.0,
    "BUS": 60.0,
}


class EntradaRequest(BaseModel):
    tipo_vehiculo: str = "AUTOMOVIL"
    placas: str = "SIN-PLACA"


def get_conexion():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def inicializar_base_datos():
    conn = get_conexion()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            folio INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo_vehiculo TEXT NOT NULL DEFAULT 'AUTOMOVIL',
            placas TEXT NOT NULL DEFAULT 'SIN-PLACA',
            tarifa_base REAL NOT NULL DEFAULT 31.0,
            hora_entrada TEXT NOT NULL,
            hora_salida TEXT,
            monto_pagado REAL DEFAULT 0.0,
            estatus TEXT DEFAULT 'PENDIENTE'
        )
    """)

    # Migración segura por si la tabla ya existía sin estas columnas
    cursor.execute("PRAGMA table_info(tickets)")
    columnas = {row["name"] for row in cursor.fetchall()}
    if "tipo_vehiculo" not in columnas:
        cursor.execute("ALTER TABLE tickets ADD COLUMN tipo_vehiculo TEXT NOT NULL DEFAULT 'AUTOMOVIL'")
    if "placas" not in columnas:
        cursor.execute("ALTER TABLE tickets ADD COLUMN placas TEXT NOT NULL DEFAULT 'SIN-PLACA'")
    if "tarifa_base" not in columnas:
        cursor.execute("ALTER TABLE tickets ADD COLUMN tarifa_base REAL NOT NULL DEFAULT 31.0")

    conn.commit()
    conn.close()


inicializar_base_datos()


@app.post("/api/entrada")
def registrar_entrada(payload: Optional[EntradaRequest] = None):
    tipo = (payload.tipo_vehiculo if payload and payload.tipo_vehiculo else "AUTOMOVIL").upper().strip()
    if tipo not in TARIFAS_VEHICULO:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de vehículo no válido '{tipo}'. Use: MOTO, AUTOMOVIL o BUS."
        )

    placas = ((payload.placas if payload and payload.placas else "SIN-PLACA").upper().strip()) or "SIN-PLACA"
    tarifa_base = TARIFAS_VEHICULO[tipo]
    hora_entrada = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_conexion()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS total FROM tickets WHERE estatus = 'PENDIENTE'")
    adentro = cursor.fetchone()["total"]
    if adentro >= CAPACIDAD_MAXIMA:
        conn.close()
        raise HTTPException(status_code=400, detail="Estacionamiento lleno. Capacidad máxima alcanzada.")

    cursor.execute(
        """
        INSERT INTO tickets (tipo_vehiculo, placas, tarifa_base, hora_entrada, estatus)
        VALUES (?, ?, ?, ?, 'PENDIENTE')
        """,
        (tipo, placas, tarifa_base, hora_entrada),
    )
    conn.commit()
    folio = cursor.lastrowid
    conn.close()

    return {
        "folio": folio,
        "tipo_vehiculo": tipo,
        "placas": placas,
        "tarifa_base": tarifa_base,
        "hora_entrada": hora_entrada,
        "estatus": "PENDIENTE",
    }


@app.get("/api/ticket/{folio}")
def consultar_ticket(folio: int):
    conn = get_conexion()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tickets WHERE folio = ?", (folio,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail=f"Ticket #{folio} no encontrado.")

    return {
        "folio": row["folio"],
        "tipo_vehiculo": row["tipo_vehiculo"],
        "placas": row["placas"],
        "tarifa_base": row["tarifa_base"],
        "hora_entrada": row["hora_entrada"],
        "hora_salida": row["hora_salida"],
        "monto_pagado": row["monto_pagado"],
        "estatus": row["estatus"],
    }


@app.put("/api/pagar/{folio}")
def pagar_ticket(folio: int):
    conn = get_conexion()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tickets WHERE folio = ?", (folio,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Ticket #{folio} no encontrado.")

    if str(row["estatus"]).startswith("PAGADO"):
        conn.close()
        raise HTTPException(status_code=400, detail=f"El ticket #{folio} ya fue pagado.")

    ahora = datetime.now()
    hora_salida = ahora.strftime("%Y-%m-%d %H:%M:%S")
    entrada_dt = datetime.strptime(row["hora_entrada"], "%Y-%m-%d %H:%M:%S")
    segundos = max(0.0, (ahora - entrada_dt).total_seconds())
    horas_cobradas = max(1, math.ceil(segundos / 3600.0))

    # Tarifa base según tipo de vehículo (se cobra al menos 1 bloque)
    tarifa_unitaria = row["tarifa_base"] if row["tarifa_base"] else TARIFAS_VEHICULO.get(row["tipo_vehiculo"], 31.0)
    total_a_pagar = round(float(tarifa_unitaria), 2)

    cursor.execute(
        """
        UPDATE tickets
        SET hora_salida = ?, monto_pagado = ?, estatus = 'PAGADO'
        WHERE folio = ?
        """,
        (hora_salida, total_a_pagar, folio),
    )
    conn.commit()
    conn.close()

    return {
        "folio": folio,
        "tipo_vehiculo": row["tipo_vehiculo"],
        "placas": row["placas"],
        "horas_cobradas": horas_cobradas,
        "total_a_pagar": total_a_pagar,
        "hora_salida": hora_salida,
        "estatus": "PAGADO",
    }


@app.put("/api/boleto-extraviado/{folio}")
def boleto_extraviado(folio: int):
    conn = get_conexion()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tickets WHERE folio = ?", (folio,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail=f"Ticket #{folio} no encontrado.")

    if str(row["estatus"]).startswith("PAGADO"):
        conn.close()
        raise HTTPException(status_code=400, detail=f"El ticket #{folio} ya fue pagado.")

    hora_salida = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_a_pagar = round(float(TARIFA_EXTRAVIADO), 2)

    cursor.execute(
        """
        UPDATE tickets
        SET hora_salida = ?, monto_pagado = ?, estatus = 'PAGADO (EXTRAVIADO)'
        WHERE folio = ?
        """,
        (hora_salida, total_a_pagar, folio),
    )
    conn.commit()
    conn.close()

    return {
        "folio": folio,
        "tipo_vehiculo": row["tipo_vehiculo"],
        "placas": row["placas"],
        "total_a_pagar": total_a_pagar,
        "hora_salida": hora_salida,
        "estatus": "PAGADO (EXTRAVIADO)",
    }


@app.get("/api/aforo")
def obtener_aforo():
    conn = get_conexion()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS adentro FROM tickets WHERE estatus = 'PENDIENTE'")
    adentro = cursor.fetchone()["adentro"]

    cursor.execute("SELECT COUNT(*) AS total FROM tickets")
    total_hoy = cursor.fetchone()["total"]

    cursor.execute("SELECT tipo_vehiculo, COUNT(*) AS cant FROM tickets WHERE estatus = 'PENDIENTE' GROUP BY tipo_vehiculo")
    por_tipo = {r["tipo_vehiculo"]: r["cant"] for r in cursor.fetchall()}

    conn.close()

    return {
        "vehiculos_actuales_adentro": adentro,
        "capacidad_maxima": CAPACIDAD_MAXIMA,
        "lugares_disponibles": max(0, CAPACIDAD_MAXIMA - adentro),
        "total_registrados_hoy": total_hoy,
        "adentro_por_tipo": por_tipo,
    }


@app.get("/api/corte")
def generar_corte():
    conn = get_conexion()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) AS total FROM tickets")
    total_vehiculos = cursor.fetchone()["total"]

    cursor.execute("SELECT COALESCE(SUM(monto_pagado), 0.0) AS recaudado FROM tickets WHERE estatus LIKE 'PAGADO%'")
    recaudado = round(float(cursor.fetchone()["recaudado"]), 2)

    conn.close()

    return {
        "total_vehiculos_ingresados": total_vehiculos,
        "dinero_total_recaudado": recaudado,
    }
