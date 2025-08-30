import os
import streamlit as st
import folium
from core import SistemaRutasEmergenciaGuayaquil

st.set_page_config(page_title="Rutas Emergencia GYE", layout="wide")

# Opcional: password simple por variable de entorno APP_PASS (configúrala en Railway → Variables)
APP_PASS = os.getenv("APP_PASS")
if APP_PASS:
    pwd = st.sidebar.text_input("Password", type="password")
    if pwd != APP_PASS:
        st.stop()

# Estilos táctiles para móvil
st.markdown("""
<style>
button[kind="secondary"], button[kind="primary"] { padding: 0.9rem 1.2rem; font-size: 1.05rem; }
div.stRadio > label { font-size: 1.05rem; }
</style>
""", unsafe_allow_html=True)

# -------- Cache: instancia única del sistema y preparación de red ----------
@st.cache_resource(show_spinner=False)
def get_system():
    return SistemaRutasEmergenciaGuayaquil()

@st.cache_data(ttl=3600, show_spinner=False)
def prepare_network(solo_ceibos: bool):
    s = get_system()
    ok = s.cargar_red_vial(solo_ceibos=solo_ceibos)
    if ok:
        s.preparar_floyd_warshall()
        return True, len(s.red_vial["nodos"])
    return False, 0

st.title("🚨 Rutas de Emergencia • Guayaquil (Floyd-Warshall)")
col1, col2 = st.columns([1, 2], gap="large")

with col1:
    st.subheader("1) Red vial")
    solo_ceibos = st.toggle("Usar Ceibos + ESPOL", value=True)
    if st.button("📥 Cargar/Actualizar red", use_container_width=True):
        with st.spinner("Descargando OSM y preparando Floyd-Warshall..."):
            ok, n = prepare_network(solo_ceibos)
            if ok:
                st.success(f"Red lista • nodos: {n:,}")
            else:
                st.error("No se pudo cargar la red (Overpass).")

    st.subheader("2) Ubicación")
    s = get_system()
    ejemplos = ["ESPOL", "FIEC ESPOL", "Los Ceibos", "Riocentro Ceibos", "Las Cumbres", "Colinas de los Ceibos"]
    ubic = st.selectbox("Elige una referencia", ejemplos, index=1)
    coords_usuario = s.buscar_ubicacion(ubic)

    st.subheader("3) Tipo de servicio")
    tipo = st.radio("Emergencia", ["Hospital", "Policía", "Bomberos"], horizontal=True)

    if st.button("🔍 Buscar servicios cercanos", use_container_width=True):
        if not getattr(s, "fw_preparado", False):
            st.error("Primero carga la red (paso 1).")
        elif not coords_usuario:
            st.error("Ubicación no válida.")
        else:
            resultados = s.encontrar_servicios_cercanos(coords_usuario, tipo, max_resultados=5)
            if not resultados:
                st.warning("Sin resultados.")
            else:
                st.session_state["resultados"] = {
                    "tipo": tipo,
                    "coords_usuario": coords_usuario,
                    "ubic": ubic,
                    "items": resultados
                }
                st.success("Listo. Ver mapa a la derecha.")

with col2:
    st.subheader("🗺️ Mapa")
    if "resultados" in st.session_state:
        data = st.session_state["resultados"]
        mapa = folium.Map(location=[-2.1709, -79.9218], zoom_start=12, tiles="CartoDB positron")

        # Marcador usuario
        folium.Marker(
            location=list(data["coords_usuario"]),
            popup=f"📍 {data['ubic']}",
            icon=folium.Icon(color="black", icon="home", prefix="fa")
        ).add_to(mapa)

        colores = {'Hospital': 'red', 'Policía': 'blue', 'Bomberos': 'orange'}
        color_serv = colores.get(data["tipo"], 'purple')

        for i, r in enumerate(data["items"]):
            serv = r["servicio"]
            # r["ruta_coordenadas"] llega como [[lon,lat], ...] → convertir a [lat,lon]
            coords_line = [[lat, lon] for lon, lat in r["ruta_coordenadas"]]
            folium.PolyLine(
                locations=coords_line,
                weight=5 if i == 0 else 3,
                color='purple' if i == 0 else color_serv,
                opacity=0.85,
                tooltip=f"Ruta a {serv.nombre}"
            ).add_to(mapa)
            pop = f"<b>{serv.nombre}</b><br>📏 {r['distancia_km']:.2f} km • ⏱️ {r['tiempo_min']:.1f} min<br>📞 {serv.telefono}"
            folium.Marker(
                location=list(serv.coordenadas),
                popup=pop,
                icon=folium.Icon(color='purple' if i == 0 else color_serv, icon='plus', prefix='fa')
            ).add_to(mapa)

        st.components.v1.html(mapa._repr_html_(), height=650, scrolling=True)
    else:
        st.info("Carga la red, elige tu ubicación y tipo de servicio, y presiona “Buscar”.")
st.caption("⚠️ En emergencias reales: 911 (ECU-911)")
