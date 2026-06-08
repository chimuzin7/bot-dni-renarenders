import streamlit as st
import sqlite3
import threading
import os
import telebot
import re
import time
import io
import requests
import base64
from datetime import datetime

# --- CONFIGURACIÓN GLOBAL DE RED ---
telebot.apihelper.CONNECT_TIMEOUT = 60
telebot.apihelper.READ_TIMEOUT = 60

# --- API KEY DEL BOT CONFIGURADA ---
TOKEN_TELEGRAM = "8710052428:AAFx_9W1iGPPOqE8VnrZaX3EoYVmUi55Bow"
ADMIN_ID = 8432779868  # Tu ID de Telegram

bot = telebot.TeleBot(TOKEN_TELEGRAM, threaded=False)

# --- BASE DE DATOS DE USUARIOS ---
def db_query(sql, params=()):
    conn = sqlite3.connect("usuarios.db")
    cursor = conn.cursor()
    cursor.execute(sql, params)
    res = cursor.fetchone() if "SELECT" in sql else None
    conn.commit()
    conn.close()
    return res

def init_db():
    conn = sqlite3.connect("usuarios.db")
    conn.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, nombre TEXT, tokens INTEGER DEFAULT 5)''')
    conn.close()

def calcular_edad(f_nac):
    try:
        if "-" in f_nac:
            nac = datetime.strptime(f_nac.split()[0], "%Y-%m-%d")
        elif "/" in f_nac:
            nac = datetime.strptime(f_nac.split()[0], "%d/%m/%Y")
        else:
            return f_nac
        hoy = datetime.now()
        return f"{hoy.year - nac.year - ((hoy.month, hoy.day) < (nac.month, nac.day))}"
    except:
        return "S/D"

def verificar_y_descontar(user_id, nombre_default="Usuario"):
    if user_id == ADMIN_ID:
        return True
    user = db_query("SELECT tokens FROM users WHERE id=?", (user_id,))
    if not user:
        db_query("INSERT INTO users (id, nombre, tokens) VALUES (?, ?, ?)", (user_id, nombre_default, 5))
        user = (5,)
    if user[0] < 1:
        return False
    db_query("UPDATE users SET tokens = tokens - 1 WHERE id=?", (user_id,))
    return True

# --- CONEXIÓN CON LA API EXTERNA ---
def consultar_servicio_remoto(dni: str, sexo: str):
    url = "https://app.cefsa.ar/api/v1/renaper/consultas/consultar-persona"
    payload = {"notaryId": 0, "dni": str(dni), "sexo": sexo.upper()}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://app.cefsa.ar",
        "Referer": "https://app.cefsa.ar/renaper",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=25)
        if response.status_code in [200, 201]:
            return response.json()
        return None
    except:
        return None

def extraer_datos_limpios(resultado):
    try:
        if isinstance(resultado, dict) and "data" in resultado:
            data_interna = resultado["data"]
            if "resultadoRaw" in data_interna and "data" in data_interna["resultadoRaw"]:
                return data_interna["resultadoRaw"]["data"]
        return None
    except:
        return None

# --- MENÚ DE COMANDOS ---
@bot.message_handler(commands=['start', 'help', 'comandos'])
def start(m):
    if not db_query("SELECT id FROM users WHERE id=?", (m.from_user.id,)):
        db_query("INSERT INTO users (id, nombre, tokens) VALUES (?, ?, ?)", (m.from_user.id, m.from_user.first_name, 5))
        
    msg = (
        f"✨ **SISTEMA DE CONSULTAS RAVEBOT**\n"
        f"───────────────\n"
        f"⚙️ **FORMATO DE ENTRADA:**\n\n"
        f"🔹 Envía: `DNI SEXO` para recibir la foto del rostro y el informe detallado en texto.\n"
        f"🔹 _Ejemplo:_ `49810424 F`\n\n"
        f"📋 **COMANDOS GENERALES:**\n"
        f"🔹 `/me` o `/perfil` ➡️ Revisa tus créditos y estado de cuenta."
    )
    if m.from_user.id == ADMIN_ID:
        msg += f"\n\n⚡ **CONSOLA ADMIN:**\n🔹 `/dar [ID] [TOKENS]` ➡️ Asigna saldo a un cliente."
        
    bot.reply_to(m, msg, parse_mode='Markdown')

@bot.message_handler(commands=['me', 'perfil'])
def perfil(m):
    u = db_query("SELECT id, nombre, tokens FROM users WHERE id=?", (m.from_user.id,))
    if not u: u = (m.from_user.id, m.from_user.first_name, 5)
    saldo = "♾️ INFINITOS (MASTER)" if m.from_user.id == ADMIN_ID else f"`{u[2]}`"
    
    msg = (
        f"👤 **PANEL DE USUARIO**\n"
        f"───────────────\n"
        f"▫️ **Nombre:** {u[1]}\n"
        f"▫️ **ID:** `{u[0]}`\n\n"
        f"💳 **CRÉDITOS:** {saldo}\n"
        f"───────────────\n"
        f"📢 _Para recargar créditos contactá con soporte._"
    )
    bot.send_message(m.chat.id, msg, parse_mode='Markdown')

@bot.message_handler(commands=['dar'])
def dar_tokens(m):
    if m.from_user.id != ADMIN_ID: return
    try:
        args = m.text.split()
        target_id = int(args[1])
        cantidad = int(args[2])
        
        exists = db_query("SELECT id FROM users WHERE id=?", (target_id,))
        if not exists:
            db_query("INSERT INTO users (id, nombre, tokens) VALUES (?, 'Usuario Registrado', ?)", (target_id, cantidad))
        else:
            db_query("UPDATE users SET tokens = tokens + ? WHERE id=?", (cantidad, target_id))
            
        bot.reply_to(m, f"✅ Se asignaron **{cantidad} tokens** al ID `{target_id}`.")
        try:
            bot.send_message(target_id, f"💰 **¡Recarga Exitosa!** Se te acreditaron `{cantidad}` tokens.")
        except: pass
    except:
        bot.reply_to(m, "❌ Usa: `/dar [ID] [CANTIDAD]`")

# --- ENGINE PRINCIPAL: CONSULTA LIVE RENAPER ---
@bot.message_handler(func=lambda m: True)
def procesar_consulta_live(m):
    partes = m.text.strip().split()
    if len(partes) != 2:
        bot.reply_to(m, "⚠️ **Formato inválido.** Envía: `DNI SEXO` (Ejemplo: `49810424 F`)")
        return
        
    dni = "".join(re.findall(r'\d+', partes[0]))
    sexo = partes[1].upper()
    
    if sexo not in ['M', 'F']:
        bot.reply_to(m, "⚠️ El sexo debe ser **M** (Masculino) o **F** (Femenino).")
        return

    if not verificar_y_descontar(m.from_user.id, m.from_user.first_name):
        bot.reply_to(m, "❌ ups te quedaste sin tokens. Para adquirir tokens contactá a @viIIero 👨🏻‍💻")
        return

    msg_espera = bot.reply_to(m, "⏳ **Conectando con el servicio remoto y estructurando reporte de texto...**")

    try:
        raw_res = consultar_servicio_remoto(dni, sexo)
        datos = extraer_datos_limpios(raw_res)
        
        if not datos or not isinstance(datos, dict):
            bot.edit_message_text("❌ No se encontraron registros asociados en el servicio remoto.", m.chat.id, msg_espera.message_id)
            return

        nombre = datos.get("nombres", "S/D").strip().upper()
        apellido = datos.get("apellido", "S/D").strip().upper()
        fecha_nac = datos.get("fecha_nacimiento", "S/D")
        edad = calcular_edad(fecha_nac)
        sexo_completo = "FEMENINO" if sexo == "F" else "MASCULINO"
        cuil = datos.get("cuil", "S/D")
        tramite = datos.get("nro_tramite", "S/D")
        vencimiento = datos.get("fecha_vencimiento", "S/D")
        emision = datos.get("fecha_emision", "S/D")
        ejemplar = datos.get("ejemplar", "S/D").upper()
        fallecido = datos.get("fallecido", "Sin Aviso De Fallecimiento")
        if not fallecido or fallecido == "false" or fallecido == "N":
            fallecido = "Sin Aviso De Fallecimiento"

        calle = datos.get("calle", "S/D").strip().upper()
        numero = datos.get("numero", "S/D").strip().upper()
        piso = datos.get("piso", "-").strip().upper()
        depto = datos.get("departamento", "-").strip().upper()
        barrio = datos.get("barrio", "-").strip().upper()
        municipio = datos.get("localidad", "S/D").strip().upper()
        cpostal = datos.get("codigo_postal", "S/D").strip().upper()
        ciudad = datos.get("localidad", "S/D").strip().upper()
        provincia = datos.get("provincia", "S/D").strip().upper()

        if not piso or piso == "NONE": piso = "-"
        if not depto or depto == "NONE": depto = "-"
        if not barrio or barrio == "NONE": barrio = "-"

        f_nac_inv = "".join(fecha_nac.split("/")[2:3])[2:] + "".join(fecha_nac.split("/")[1:2]) + "".join(fecha_nac.split("/")[0:1]) if "/" in fecha_nac else "000000"
        f_venc_inv = "".join(vencimiento.split("/")[2:3])[2:] + "".join(vencimiento.split("/")[1:2]) + "".join(vencimiento.split("/")[0:1]) if "/" in vencimiento else "000000"
        
        idarg_l1 = f"IDARG{dni}<0<<<<<<<<<<<<<<<"
        idarg_l2 = f"{f_nac_inv}3{sexo}{f_venc_inv}1ARG<<<<<<<<<<<4"
        idarg_l3 = f"{apellido}<<{nombre.replace(' ', '<')}<<<<<<<<<"

        pdf417_str = f"00{tramite}@{apellido}@{nombre}@{sexo}@{dni}@{ejemplar}@{fecha_nac}@{emision}@275"

        informe_texto = (
            f"[›››] Información Personal\n"
            f"> Nombre:        {nombre.title()}\n"
            f"> Apellido:      {apellido}\n"
            f"> Edad:          {edad}\n"
            f"> Sexo:          {sexo_completo.title()}\n"
            f"> DNI:           {dni}\n"
            f"> CUIL:          {cuil}\n"
            f"> Trámite:       {tramite}\n"
            f"> Nacimiento:    {fecha_nac}\n"
            f"> Vencimiento:   {vencimiento}\n"
            f"> Emisión:       {emision}\n"
            f"> Ejemplar:      {ejemplar}\n"
            f"> Fallecimiento: {fallecido}\n\n"
            f"[›››] Domicilio\n"
            f"> Calle:         {calle}\n"
            f"> Altura:        {numero}\n"
            f"> Piso:          {piso}\n"
            f"> Depto:         {depto}\n"
            f"> Barrio:        {barrio}\n"
            f"> Municipio:     {municipio}\n"
            f"> CPostal:       {cpostal}\n"
            f"> Ciudad:        {ciudad}\n"
            f"> Provincia:     {provincia}\n\n"
            f"{calle} {numero} | {cpostal} | {ciudad} | {provincia} | ARGENTINA\n\n"
            f"[›››] IDARG\n"
            f"{idarg_l1}\n"
            f"{idarg_l2}\n"
            f"{idarg_l3}\n\n"
            f"[›››] PDF417\n"
            f"{pdf417_str}"
        )

        foto_b64 = datos.get("foto", "")
        
        if foto_b64:
            try:
                if "," in foto_b64:
                    foto_b64 = foto_b64.split(",")[1]
                foto_bytes = base64.b64decode(foto_b64)
                foto_archivo = io.BytesIO(foto_bytes)
                foto_archivo.name = f"Rostro_{dni}.jpg"
                
                bot.send_photo(m.chat.id, foto_archivo, caption=informe_texto)
                bot.delete_message(m.chat.id, msg_espera.message_id)
                return
            except Exception as img_err:
                informe_texto += f"\n\n⚠️ *(No se pudo procesar la foto adjunta: {str(img_err)})*"

        bot.reply_to(m, informe_texto)
        bot.delete_message(m.chat.id, msg_espera.message_id)
        
    except Exception as e:
        bot.edit_message_text("🔧 Error en el módulo de generación de texto plano.", m.chat.id, msg_espera.message_id)

# --- ENGINE POLLING MEJORADO PARA EVITAR CAÍDAS POR HANDSHAKE TIMEOUT ---
init_db()
def run():
    try:
        bot.remove_webhook()
    except:
        pass
    while True:
        try:
            # Polling directo y más tolerante a microcortes de red compartida
            bot.polling(non_stop=True, timeout=60, long_polling_timeout=30)
            break
        except Exception as e:
            # Si el handshake de Telegram expira, espera 10 segundos y reconecta solo en silencio
            time.sleep(10)

# --- PANEL DE CONTROL (STREAMLIT) ---
st.title("Admin Panel v12.4 - Anti-Baneos de Red")

if "bot_iniciado" not in st.session_state: 
    st.session_state.bot_iniciado = False

if st.button("🚀 DEPLOY RAVEBOT ENGINE"):
    if not st.session_state.bot_iniciado:
        threading.Thread(target=run, daemon=True).start()
        st.session_state.bot_iniciado = True
        st.success("🟢 RaveBot en línea con bypass de red activado.")
    else:
        st.warning("⚠️ El bot ya está corriendo.")
        
