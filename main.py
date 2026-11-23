from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
from datetime import datetime

app = FastAPI(title="Estacionamiento Zoológico de León API", version="1.0")

DB_NAME = "estacionamiento_zoologico.db"
TARIFA_POR_HORA = 20.0
TARIFA_EXTRABIEN = 100.0  # Tarifa fija para boleto extraviado
CAPACIDAD_MAXIMA = 50     # Límite del estacionamiento para el aforo

def inicializar_base_datos():
    """Crea la base de datos y la tabla de tickets si no existen."""
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            folio INTEGER PRIMARY KEY AUTOINCREMENT,
            hora_entrada TEXT NOT NULL,
            hora_salida TEXT,
            monto_pagado REAL DEFAULT 0.0,
            estatus TEXT DEFAULT 'PENDIENTE'
        )
    """)
    conexion.commit()
    conexion.close()

# Inicializar la BD al arrancar la API
inicializar_base_datos()

# --- ENDPOINTS (Las rutas que consumirán tus teléfonos) ---

@app.post("/api/entrada")
def registrar_entrada():
    """Registra la entrada de un vehículo y devuelve su folio."""
    # Opcional: Validar aforo antes de dejar entrar
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    cursor.execute("SELECT COUNT(*) FROM tickets WHERE estatus = 'PENDIENTE'")
    en_curso = cursor.fetchone()[0]
    
    if en_curso >= CAPACIDAD_MAXIMA:
        conexion.close()
        raise HTTPException(status_code=400, detail="¡Estacionamiento lleno! Aforo al límite.")
    
    hora_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO tickets (hora_entrada, estatus) VALUES (?, 'PENDIENTE')", (hora_actual,))
    conexion.commit()
    nuevo_folio = cursor.lastrowid
    conexion.close()
    
    return {
        "mensaje": "Vehículo registrado con éxito",
        "folio": nuevo_folio,
        "hora_entrada": hora_actual
    }

@app.get("/api/ticket/{folio}")
def consultar_ticket(folio: int):
    """Consulta el estatus de un ticket por su folio."""
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    cursor.execute("SELECT folio, hora_entrada, hora_salida, monto_pagado, estatus FROM tickets WHERE folio = ?", (folio,))
    ticket = cursor.fetchone()
    conexion.close()
    
    if not ticket:
        raise HTTPException(status_code=404, detail=f"El ticket #{folio} no existe.")
        
    return {
        "folio": ticket[0],
        "hora_entrada": ticket[1],
        "hora_salida": ticket[2],
        "monto_pagado": ticket[3],
        "estatus": ticket[4]
    }

@app.put("/api/pagar/{folio}")
def registrar_pago(folio: int):
    """Calcula el tiempo transcurrido, cobra y actualiza a PAGADO."""
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    
    cursor.execute("SELECT hora_entrada, estatus FROM tickets WHERE folio = ?", (folio,))
    resultado = cursor.fetchone()
    
    if not resultado:
        conexion.close()
        raise HTTPException(status_code=404, detail=f"El ticket #{folio} no existe.")
        
    hora_entrada_str, estatus = resultado
    
    if estatus == 'PAGADO':
        conexion.close()
        raise HTTPException(status_code=400, detail=f"El ticket #{folio} ya fue pagado previamente.")
        
    hora_entrada = datetime.strptime(hora_entrada_str, "%Y-%m-%d %H:%M:%S")
    hora_salida_dt = datetime.now()
    hora_salida_str = hora_salida_dt.strftime("%Y-%m-%d %H:%M:%S")
    
    diferencia_horas = (hora_salida_dt - hora_entrada).total_seconds() / 3600
    horas_a_cobrar = max(1.0, round(diferencia_horas, 2))
    monto_total = horas_a_cobrar * TARIFA_POR_HORA
    
    cursor.execute("""
        UPDATE tickets 
        SET hora_salida = ?, monto_pagado = ?, estatus = 'PAGADO' 
        WHERE folio = ?
    """, (hora_salida_str, monto_total, folio))
    
    conexion.commit()
    conexion.close()
    
    return {
        "mensaje": "Pago registrado con éxito",
        "folio": folio,
        "horas_cobradas": horas_a_cobrar,
        "total_a_pagar": monto_total,
        "estatus": "PAGADO"
    }

@app.put("/api/boleto-extraviado/{folio}")
def boleto_extraviado(folio: int):
    """Módulo especial para boletos extraviados con tarifa fija."""
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    
    cursor.execute("SELECT estatus FROM tickets WHERE folio = ?", (folio,))
    resultado = cursor.fetchone()
    
    if not resultado:
        conexion.close()
        raise HTTPException(status_code=404, detail=f"El ticket #{folio} no existe.")
        
    hora_salida_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
        UPDATE tickets 
        SET hora_salida = ?, monto_pagado = ?, estatus = 'PAGADO (EXTRAVIADO)' 
        WHERE folio = ?
    """, (hora_salida_str, TARIFA_EXTRABIEN, folio))
    
    conexion.commit()
    conexion.close()
    
    return {
        "mensaje": "Boleto extraviado procesado con penalización",
        "folio": folio,
        "total_a_pagar": TARIFA_EXTRABIEN,
        "estatus": "PAGADO (EXTRAVIADO)"
    }

@app.get("/api/aforo")
def consultar_aforo():
    """Devuelve el aforo actual en tiempo real."""
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM tickets WHERE estatus = 'PENDIENTE'")
    vehiculos_adentro = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM tickets")
    total_historico = cursor.fetchone()[0]
    
    conexion.close()
    
    return {
        "capacidad_maxima": CAPACIDAD_MAXIMA,
        "vehiculos_actuales_adentro": vehiculos_adentro,
        "lugares_disponibles": CAPACIDAD_MAXIMA - vehiculos_adentro,
        "total_registrados_hoy": total_historico
    }

@app.get("/api/corte")
def generar_corte():
    """Genera el reporte financiero del día."""
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM tickets")
    total_vehiculos = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(monto_pagado) FROM tickets WHERE estatus LIKE 'PAGADO%'")
    resultado_dinero = cursor.fetchone()[0]
    total_recaudado = resultado_dinero if resultado_dinero else 0.0
    
    conexion.close()
    
    return {
        "total_vehiculos_ingresados": total_vehiculos,
        "dinero_total_recaudado": total_recaudado
    }
