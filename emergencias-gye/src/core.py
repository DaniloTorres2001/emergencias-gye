"""
Core de Rutas de Emergencia para Guayaquil (sin UI)
- DescargadorRed: obtiene y procesa red vial OSM
- SistemaRutasEmergenciaGuayaquil: FW all-pairs + consultas
"""

import math
import time
import requests
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional


# ========================
# Modelo de datos
# ========================
@dataclass
class ServicioEmergencia:
    nombre: str
    tipo: str
    coordenadas: Tuple[float, float]
    telefono: str
    especialidad: str = ""
    zona: str = ""


# ========================
# Descarga y parsing OSM
# ========================
class DescargadorRed:
    """
    Descarga automática de la red vial de Guayaquil usando Overpass API (OpenStreetMap)
    """
    def __init__(self):
        self.overpass_url = "http://overpass-api.de/api/interpreter"
        # Bounding box (Ceibos + ESPOL)
        # [sur, oeste, norte, este]
        self.bbox_guayaquil = [-2.19, -79.97, -2.142, -79.920]
        self.bbox_ceibos = [-2.19, -79.97, -2.142, -79.920]

    def descargar_red_vial(self, usar_solo_ceibos: bool = False) -> Dict:
        """
        Descarga la red vial usando Overpass API
        """
        bbox = self.bbox_ceibos if usar_solo_ceibos else self.bbox_guayaquil

        query = f"""
[out:json][timeout:120];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service|living_street|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link)$"]
      ({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  way["highway"="living_street"]
      ({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
);
out tags geom;
"""
        try:
            print("🌐 Descargando red vial desde OpenStreetMap...")
            response = requests.post(
                self.overpass_url,
                data=query,
                timeout=180,
                headers={'User-Agent': 'EmergencySystem/1.0'}
            )
            if response.status_code != 200:
                raise Exception(f"Error en Overpass API: {response.status_code}")
            return response.json()
        except Exception as e:
            print(f"❌ Error descargando red vial: {e}")
            return self._red_fallback()

    def _red_fallback(self) -> Dict:
        """Red mínima de respaldo en caso de fallo en la descarga"""
        print("🔄 Usando red de respaldo...")
        return {
            "elements": [
                {
                    "type": "way",
                    "id": 1,
                    "geometry": [
                        {"lat": -2.1448, "lon": -79.9663},  # ESPOL
                        {"lat": -2.1672, "lon": -79.9378},  # Ceibos Centro
                    ]
                }
            ]
        }

    def procesar_red_osm(self, data_osm: Dict) -> Dict:
        """
        Convierte datos OSM en estructura de grafo (nodos, aristas dirigidas con pesos en km)
        """
        print("🔄 Procesando red vial...")
        MAX_NODOS = 5000  # límite duro de nodos
        total_points = 0
        for _el in data_osm.get("elements", []):
            if _el.get("type") == "way" and "geometry" in _el:
                total_points += len(_el["geometry"])

        objetivo = max(500, MAX_NODOS - 150)  # reserva para nodos adicionales
        downsample_step = 1
        if total_points > objetivo:
            downsample_step = max(1, math.ceil((total_points * 1.1) / objetivo))
            print(f"⚖️  Submuestreo: cada {downsample_step} pts (total≈{total_points} → objetivo≤{objetivo})")

        nodos: Dict[str, Tuple[float, float]] = {}
        aristas: List[Tuple[str, str, float]] = []
        nodo_counter = 0
        coord_to_id: Dict[str, str] = {}

        for elemento in data_osm.get("elements", []):
            if elemento.get("type") != "way" or "geometry" not in elemento:
                continue

            geometry = elemento["geometry"]
            tags = elemento.get("tags", {})
            oneway = str(tags.get("oneway", "no")).lower()
            junction = str(tags.get("junction", "")).lower()

            dir_mode = "both"
            if oneway in ("yes", "true", "1"):
                dir_mode = "forward"
            elif oneway == "-1":
                dir_mode = "backward"
            elif junction == "roundabout":
                dir_mode = "forward"

            # Crear nodos (con submuestreo)
            way_nodes: List[str] = []
            for idx, punto in enumerate(geometry):
                if (downsample_step > 1) and (idx % downsample_step != 0) and (idx != len(geometry) - 1):
                    continue
                lat, lon = punto["lat"], punto["lon"]
                coord_key = f"{lat:.6f},{lon:.6f}"
                if coord_key not in coord_to_id:
                    node_id = f"node_{nodo_counter}"
                    coord_to_id[coord_key] = node_id
                    nodos[node_id] = (lat, lon)
                    nodo_counter += 1
                way_nodes.append(coord_to_id[coord_key])

            # Crear aristas dirigidas consecutivas
            for i in range(len(way_nodes) - 1):
                n1, n2 = way_nodes[i], way_nodes[i + 1]
                if n1 == n2:
                    continue
                coord1 = nodos[n1]
                coord2 = nodos[n2]
                distancia = max(self._haversine_km(coord1, coord2), 0.01)  # mínimo 10m
                if dir_mode in ("both", "forward"):
                    aristas.append((n1, n2, distancia))
                if dir_mode in ("both", "backward"):
                    aristas.append((n2, n1, distancia))

        print(f"✅ Red procesada: {len(nodos)} nodos, {len(aristas)} aristas")
        return {"nodos": nodos, "aristas": aristas}

    def _haversine_km(self, coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
        """Distancia geodésica aproximada entre dos puntos (km)"""
        lat1, lon1 = coord1
        lat2, lon2 = coord2
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c


# ========================
# Lógica del sistema
# ========================
class SistemaRutasEmergenciaGuayaquil:
    """
    Sistema principal de rutas de emergencia para Guayaquil
    Implementa Floyd-Warshall sobre red real de OpenStreetMap
    """
    def __init__(self):
        print("🚨 Iniciando Sistema de Emergencias - Guayaquil")
        self.servicios_emergencia = self._inicializar_servicios()
        self.ubicaciones_referencia = self._inicializar_ubicaciones()
        self.red_vial: Optional[Dict] = None
        self.fw_preparado: bool = False
        self.descargador = DescargadorRed()

    # --- utilidades plano local ---
    def _latlon_to_xy(self, lat, lon, lat0=None):
        R = 6371.0
        if lat0 is None:
            lat0 = lat
        x = math.radians(lon) * R * math.cos(math.radians(lat0))
        y = math.radians(lat) * R
        return x, y

    def _xy_to_latlon(self, x, y, lat0):
        R = 6371.0
        lat = math.degrees(y / R)
        lon = math.degrees(x / (R * math.cos(math.radians(lat0))))
        return lat, lon

    # --- proyección a la arista más cercana ---
    def _nearest_edge_projection(self, point_latlon):
        """
        Encuentra la arista más cercana y el punto proyectado sobre ella.
        Retorna (u, v, t, proj_lat, proj_lon, dist_pt_to_proj_km) con t in [0,1].
        """
        if not self.red_vial or not self.red_vial.get("aristas"):
            return None
        px, py = self._latlon_to_xy(point_latlon[0], point_latlon[1], point_latlon[0])
        best = (None, None, None, None, None, float("inf"))
        nodos = self.red_vial["nodos"]
        for (u, v, _w) in self.red_vial["aristas"]:
            a_lat, a_lon = nodos[u]
            b_lat, b_lon = nodos[v]
            ax, ay = self._latlon_to_xy(a_lat, a_lon, point_latlon[0])
            bx, by = self._latlon_to_xy(b_lat, b_lon, point_latlon[0])
            vx, vy = bx - ax, by - ay
            wx, wy = px - ax, py - ay
            denom = vx * vx + vy * vy
            if denom <= 1e-12:
                t = 0.0
                qx, qy = ax, ay
            else:
                t = (wx * vx + wy * vy) / denom
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                qx, qy = ax + t * vx, ay + t * vy
            dx, dy = px - qx, py - qy
            d = (dx * dx + dy * dy) ** 0.5
            if d < best[5]:
                q_lat, q_lon = self._xy_to_latlon(qx, qy, point_latlon[0])
                best = (u, v, t, q_lat, q_lon, d)
        return best

    def _split_edge_and_connect_point(self, point_latlon, node_id_prefix="snap"):
        """
        Inserta un nodo sobre la arista más cercana y lo conecta al punto externo (servicio/ref).
        Devuelve (new_node_id, dist_conn_km). Preserva direccionalidad existente.
        """
        res = self._nearest_edge_projection(point_latlon)
        if not res:
            return None, None
        u, v, t, q_lat, q_lon, _ = res
        new_id = f"{node_id_prefix}_{len(self.red_vial['nodos'])}"
        self.red_vial["nodos"][new_id] = (q_lat, q_lon)

        dir_set = {(a, b) for (a, b, _w) in self.red_vial["aristas"]}
        d_uq = self.descargador._haversine_km(self.red_vial["nodos"][u], (q_lat, q_lon))
        d_qv = self.descargador._haversine_km((q_lat, q_lon), self.red_vial["nodos"][v])

        if (u, v) in dir_set:
            self.red_vial["aristas"].append((u, new_id, max(d_uq, 0.01)))
            self.red_vial["aristas"].append((new_id, v, max(d_qv, 0.01)))
        if (v, u) in dir_set:
            self.red_vial["aristas"].append((v, new_id, max(d_qv, 0.01)))
            self.red_vial["aristas"].append((new_id, u, max(d_uq, 0.01)))

        d_conn = max(self.descargador._haversine_km(point_latlon, (q_lat, q_lon)), 0.01)
        return new_id, d_conn

    # --- datos base ---
    def _inicializar_servicios(self) -> Dict[str, List[ServicioEmergencia]]:
        return {
            'Hospital': [
                ServicioEmergencia("Hospital del IESS Los Ceibos", "Hospital",
                                   (-2.175396, -79.941602), "(04) 380-5130", "Público"),
                ServicioEmergencia("InterHospital", "Hospital",
                                   (-2.180693, -79.945202), "(04) 375-0000", "Privado"),
            ],
            'Bomberos': [
                ServicioEmergencia("Cuartel Bomberos #5", "Bomberos",
                                   (-2.16239, -79.92644), "(04) 371-4840", zona="Ceibos"),
            ],
            'Policía': [
                ServicioEmergencia("UPC Los Ceibos", "Policía",
                                   (-2.16567, -79.93697), "911", zona="Los Ceibos"),
                ServicioEmergencia("UPC Los Ceibos 2", "Policía",
                                   (-2.151886, -79.952468), "911", zona="Los Ceibos"),
            ],
        }

    def _inicializar_ubicaciones(self) -> Dict[str, Tuple[float, float]]:
        return {
            "ESPOL": (-2.1448, -79.9663),
            "FADCOM ESPOL": (-2.144132, -79.962161),
            "FIEC ESPOL": (-2.144503, -79.968048),
            "FCNM ESPOL": (-2.147740, -79.967923),
            "FCSH ESPOL": (-2.147568, -79.968294),
            "FCV ESPOL": (-2.152106, -79.957153),
            "FICT ESPOL": (-2.145025, -79.964813),
            "FIMCP ESPOL": (-2.144065, -79.965581),
            "Los Ceibos": (-2.1672, -79.9378),
            "Riocentro Ceibos": (-2.177456, -79.943431),
            "Las Cumbres": (-2.157333, -79.946304),
            "Colinas de los Ceibos": (-2.163287, -79.945786),
        }

    # --- carga de red y FW ---
    def cargar_red_vial(self, solo_ceibos: bool = False) -> bool:
        try:
            data_osm = self.descargador.descargar_red_vial(solo_ceibos)
            self.red_vial = self.descargador.procesar_red_osm(data_osm)
            self._integrar_servicios_en_red()
            return True
        except Exception as e:
            print(f"❌ Error cargando red vial: {e}")
            return False

    def _integrar_servicios_en_red(self):
        if not self.red_vial:
            return
        print("🔄 Integrando servicios y referencias en la red...")
        # Servicios
        for _, servicios in self.servicios_emergencia.items():
            for servicio in servicios:
                node_id = f"servicio_{servicio.nombre.replace(' ', '_')}"
                self.red_vial["nodos"][node_id] = servicio.coordenadas
                new_node, d_conn = self._split_edge_and_connect_point(servicio.coordenadas, node_id_prefix="snap")
                if new_node:
                    self.red_vial["aristas"].append((node_id, new_node, d_conn))
                    self.red_vial["aristas"].append((new_node, node_id, d_conn))
        # Referencias
        for nombre, coords in self.ubicaciones_referencia.items():
            node_id = f"ref_{nombre.replace(' ', '_')}"
            self.red_vial["nodos"][node_id] = coords
            new_node, d_conn = self._split_edge_and_connect_point(coords, node_id_prefix="snap")
            if new_node and new_node != node_id:
                d = min(d_conn, 0.5)
                self.red_vial["aristas"].append((node_id, new_node, d))
                self.red_vial["aristas"].append((new_node, node_id, d))

    def preparar_floyd_warshall(self) -> bool:
        if not self.red_vial:
            print("❌ Red vial no cargada")
            return False

        print("🔄 Preparando algoritmo Floyd-Warshall...")
        nodos = list(self.red_vial['nodos'].keys())
        self.nodo_a_indice = {nodo: i for i, nodo in enumerate(nodos)}
        self.indice_a_nodo = {i: nodo for nodo, i in self.nodo_a_indice.items()}

        n = len(nodos)
        INF = float('inf')

        self.matriz_distancias = [[INF] * n for _ in range(n)]
        self.matriz_sucesores = [[None] * n for _ in range(n)]

        for i in range(n):
            self.matriz_distancias[i][i] = 0.0
            self.matriz_sucesores[i][i] = i

        for u, v, peso in self.red_vial['aristas']:
            if u in self.nodo_a_indice and v in self.nodo_a_indice:
                i = self.nodo_a_indice[u]
                j = self.nodo_a_indice[v]
                if peso < self.matriz_distancias[i][j]:
                    self.matriz_distancias[i][j] = peso
                    self.matriz_sucesores[i][j] = j

        print("🔄 Ejecutando Floyd-Warshall...")
        inicio = time.time()
        for k in range(n):
            # progreso cada ~10%
            if n >= 10 and k % max(1, n // 10) == 0:
                print(f"   Progreso: {(k / n) * 100:.1f}%")
            for i in range(n):
                dik = self.matriz_distancias[i][k]
                if dik == INF:
                    continue
                row_i = self.matriz_distancias[i]
                suc_i = self.matriz_sucesores[i]
                row_k = self.matriz_distancias[k]
                for j in range(n):
                    alt = dik + row_k[j]
                    if alt < row_i[j]:
                        row_i[j] = alt
                        suc_i[j] = self.matriz_sucesores[i][k]
        print(f"✅ Floyd-Warshall completado en {time.time() - inicio:.2f} s")
        self.fw_preparado = True
        return True

    # --- consultas ---
    def _encontrar_nodo_mas_cercano(self, coordenadas: Tuple[float, float]) -> Optional[str]:
        if not self.red_vial or not self.red_vial["nodos"]:
            return None
        mejor_distancia = float('inf')
        mejor_nodo = None
        for node_id, node_coords in self.red_vial["nodos"].items():
            if node_id.startswith('servicio_') or node_id.startswith('ref_'):
                continue
            distancia = self.descargador._haversine_km(coordenadas, node_coords)
            if distancia < mejor_distancia:
                mejor_distancia = distancia
                mejor_nodo = node_id
        return mejor_nodo

    def obtener_ruta(self, origen: str, destino: str) -> Optional[Dict]:
        if not self.fw_preparado:
            return None
        if origen not in self.nodo_a_indice or destino not in self.nodo_a_indice:
            return None
        i = self.nodo_a_indice[origen]
        j = self.nodo_a_indice[destino]
        if self.matriz_sucesores[i][j] is None:
            return None

        ruta = [i]
        actual = i
        while actual != j:
            actual = self.matriz_sucesores[actual][j]
            if actual is None:
                return None
            ruta.append(actual)

        ruta_nodos = [self.indice_a_nodo[idx] for idx in ruta]
        coordenadas = []
        for nodo in ruta_nodos:
            lat, lon = self.red_vial["nodos"][nodo]
            coordenadas.append([lon, lat])  # GeoJSON: [lon, lat]
        distancia_km = self.matriz_distancias[i][j]
        tiempo_min = (distancia_km / 25.0) * 60.0  # 25 km/h
        return {
            "ruta_nodos": ruta_nodos,
            "coordenadas": coordenadas,
            "distancia_km": distancia_km,
            "tiempo_min": tiempo_min
        }

    def buscar_ubicacion(self, texto: str) -> Optional[Tuple[float, float]]:
        texto = texto.strip().lower()
        for nombre, coords in self.ubicaciones_referencia.items():
            if texto == nombre.lower():
                return coords
        for nombre, coords in self.ubicaciones_referencia.items():
            if texto in nombre.lower() or nombre.lower() in texto:
                return coords
        return None

    def encontrar_servicios_cercanos(self, coordenadas_origen: Tuple[float, float],
                                     tipo_servicio: str, max_resultados: int = 3) -> List[Dict]:
        if not self.fw_preparado or tipo_servicio not in self.servicios_emergencia:
            return []
        nodo_origen = self._encontrar_nodo_mas_cercano(coordenadas_origen)
        if not nodo_origen:
            return []
        resultados = []
        for servicio in self.servicios_emergencia[tipo_servicio]:
            nodo_servicio = f"servicio_{servicio.nombre.replace(' ', '_')}"
            if nodo_servicio not in self.nodo_a_indice:
                continue
            ruta_info = self.obtener_ruta(nodo_origen, nodo_servicio)
            if not ruta_info:
                continue
            resultados.append({
                "servicio": servicio,
                "distancia_km": ruta_info["distancia_km"],
                "tiempo_min": ruta_info["tiempo_min"],
                "ruta_coordenadas": ruta_info["coordenadas"],
                "ruta_nodos": ruta_info["ruta_nodos"]
            })
        resultados.sort(key=lambda x: x["distancia_km"])
        return resultados[:max_resultados]