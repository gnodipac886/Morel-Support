#!/usr/bin/env python3
"""Morel Support — Probability mapping app for morel mushroom foraging."""

import math
import datetime
import urllib.request
import urllib.error
import urllib.parse
import hashlib
import json
import os
import io
import csv
import time
import threading
import webbrowser
import concurrent.futures
import multiprocessing

from global_land_mask import globe

CPU_COUNT = os.cpu_count() or 4
# I/O-bound workloads benefit from more threads than CPU cores.
# Use 4× multiplier (common heuristic for network-heavy tasks), capped at 64.
IO_WORKERS = min(64, CPU_COUNT * 4)

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ── Utilities ──────────────────────────────────────────────────────────────────

def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))

def fetch_json(url, timeout=15):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'MorelSupport/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(2 ** attempt)   # 1s, then 2s
                continue
            print(f"[fetch] {url[:90]} → HTTP Error {e.code}: {e.reason}")
            return None
        except Exception as e:
            print(f"[fetch] {url[:90]} → {e}")
            return None
    return None

def create_grid(bounds, resolution_miles):
    n, s, e, w = bounds['north'], bounds['south'], bounds['east'], bounds['west']
    clat = (n + s) / 2
    lat_step = resolution_miles / 69.0
    lon_step = resolution_miles / (69.0 * math.cos(math.radians(clat)) + 1e-9)
    cells = []
    lat = s
    while lat < n:
        lon = w
        while lon < e:
            cn = min(lat + lat_step, n)
            ce = min(lon + lon_step, e)
            cell_lat = (lat + cn) / 2
            cell_lon = (lon + ce) / 2
            if globe.is_land(cell_lat, cell_lon):
                cells.append({
                    'center': (cell_lat, cell_lon),
                    'bounds': {'south': lat, 'north': cn, 'west': lon, 'east': ce},
                })
            lon += lon_step
        lat += lat_step
    return cells

# ── Data Fetchers ──────────────────────────────────────────────────────────────

def fetch_inat(bounds, opts):
    """Fetch Morchella (morel) observations from iNaturalist API."""
    years_back = int(opts.get('years_back', 5))
    quality    = opts.get('quality', 'research,needs_id')
    d1 = (datetime.date.today() - datetime.timedelta(days=365 * years_back)).isoformat()
    params = {
        'taxon_name':  'Morchella',   # genus of true morels
        'nelat':       round(bounds['north'], 4),
        'nelng':       round(bounds['east'],  4),
        'swlat':       round(bounds['south'], 4),
        'swlng':       round(bounds['west'],  4),
        'per_page':    200,
        'd1':          d1,
        'quality_grade': quality,
        'order_by':    'observed_on',
        'order':       'desc',
    }
    url  = 'https://api.inaturalist.org/v1/observations?' + urllib.parse.urlencode(params)
    data = fetch_json(url)
    obs  = data.get('results', []) if data else []
    print(f"[iNat] {len(obs)} observations")
    return obs

def _fetch_precip_pt(args):
    """Fetch one Open-Meteo sample point. Module-level so it's picklable."""
    lat, lon, start_iso, end_iso = args
    params = {
        'latitude':         round(lat, 3),
        'longitude':        round(lon, 3),
        'start_date':       start_iso,
        'end_date':         end_iso,
        'daily':            'precipitation_sum,snowfall_sum,soil_temperature_0_to_7cm_mean',
        'temperature_unit': 'fahrenheit',
        'timezone':         'auto',
    }
    url  = 'https://archive-api.open-meteo.com/v1/archive?' + urllib.parse.urlencode(params)
    data = fetch_json(url, timeout=8)
    if data and 'daily' in data:
        daily    = data['daily']
        precip   = [p or 0 for p in daily.get('precipitation_sum', [])]  # mm water equiv
        snow_cm  = [s or 0 for s in daily.get('snowfall_sum', [])]        # cm of snow
        temps    = [t for t in daily.get('soil_temperature_0_to_7cm_mean', []) if t is not None]
        return {
            'lat':         lat,
            'lon':         lon,
            'precip_in':   sum(precip) / 25.4,            # total precip as inches (rain+snow water)
            'snow_in':     sum(snow_cm) / 10 / 2.54,      # cm snow → cm water → inches
            'soil_temp_f': sum(temps) / len(temps) if temps else None,
        }
    return None


def _parse_time_window(tw):
    """Parse '1w','2w','3w','1m','2m','3m' (or plain int) → number of days."""
    tw = str(tw).strip().lower()
    if tw.endswith('w'):
        try: return int(tw[:-1]) * 7
        except ValueError: pass
    if tw.endswith('m'):
        try: return int(tw[:-1]) * 30
        except ValueError: pass
    try: return int(tw)
    except ValueError: return 14


def _fetch_daymet_pt(args):
    """Fetch one NASA Daymet point (total precip). Module-level so it's picklable."""
    lat, lon, start_iso, end_iso = args
    # Daymet covers CONUS + southern Canada (approx 14–55 °N, 131–53 °W), land only
    if not (14.0 <= lat <= 55.0 and -131.0 <= lon <= -53.0):
        return None
    if not globe.is_land(lat, lon):
        return None
    params = {
        'lat': round(lat, 4), 'lon': round(lon, 4),
        'vars': 'prcp', 'start': start_iso, 'end': end_iso,
    }
    url = ('https://daymet.ornl.gov/single-pixel/api/data?' +
           urllib.parse.urlencode(params))
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MorelSupport/1.0'})
        with urllib.request.urlopen(req, timeout=12) as r:
            text = r.read().decode('utf-8')
        lines = [l for l in text.splitlines() if not l.startswith('#') and l.strip()]
        if len(lines) < 2:
            return None
        reader = csv.DictReader(lines)
        total_mm = 0.0
        for row in reader:
            v = (row.get('prcp (mm/day)') or row.get('prcp') or '').strip()
            if v and v not in ('NA', ''):
                try: total_mm += float(v)
                except ValueError: pass
        if total_mm == 0:
            return None
        return {'lat': lat, 'lon': lon, 'precip_in': total_mm / 25.4, 'source': 'Daymet'}
    except Exception as e:
        print(f"[precip] Daymet {lat:.2f},{lon:.2f}: {e}")
        return None


def _fetch_acis_precip(bounds, start_str, end_str):
    """Fetch PRISM precipitation from NOAA ACIS GridData. Returns [{lat,lon,precip_in}]."""
    url  = 'http://data.rcc-acis.org/GridData'
    body = json.dumps({
        'bbox':  f"{bounds['west']:.3f},{bounds['south']:.3f},{bounds['east']:.3f},{bounds['north']:.3f}",
        'grid':  '21',   # PRISM 4 km
        'sdate': start_str,
        'edate': end_str,
        'elems': [{'name': 'pcpn', 'reduce': {'reduce': 'sum'}}],
        'meta':  'll',
    }).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={
            'Content-Type': 'application/json', 'User-Agent': 'MorelSupport/1.0',
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            resp = json.loads(r.read())
        pts     = []
        meta_ll = resp.get('meta', {}).get('ll', [])
        rows    = resp.get('data', [])
        # meta.ll is a flat list of [lon, lat] pairs.
        # With reduce='sum', data has one row: [date_label, v0, v1, ...].
        # Without reduce, sum all date rows ourselves.
        if meta_ll and rows:
            n_pts  = len(meta_ll)
            totals = [0.0] * n_pts
            valid  = [False] * n_pts
            for row in rows:
                vals = row[1:] if len(row) > 1 else row   # skip date/label field
                for i, v in enumerate(vals):
                    if i >= n_pts:
                        break
                    if v not in ('M', 'T', None, '') and v != -9999:
                        try:
                            totals[i] += float(v)
                            valid[i]   = True
                        except (TypeError, ValueError):
                            pass
            for i, ll in enumerate(meta_ll):
                if valid[i]:
                    try:
                        pts.append({'lat': float(ll[1]), 'lon': float(ll[0]),
                                    'precip_in': totals[i], 'source': 'PRISM'})
                    except (TypeError, ValueError, IndexError):
                        pass
        print(f"[precip] ACIS/PRISM: {len(pts)} grid pts")
        return pts
    except Exception as e:
        print(f"[precip] ACIS: {e}")
        return []


def fetch_precip(cells, opts):
    """Fetch precipitation (rain+snow) per grid cell.
    Sources: Open-Meteo + NASA Daymet (9 sample pts), NOAA ACIS/PRISM (bbox grid).
    Returns a list of per-cell dicts with precip_in, snow_in, soil_temp_f, bounds.
    """
    if not cells:
        return []
    time_window = opts.get('time_window', opts.get('days_back', '2w'))
    days_back   = _parse_time_window(time_window)
    end         = datetime.date.today() - datetime.timedelta(days=1)
    start       = end - datetime.timedelta(days=days_back)
    s_iso, e_iso = start.isoformat(), end.isoformat()

    lats   = [c['center'][0] for c in cells]
    lons   = [c['center'][1] for c in cells]
    bounds = {'north': max(lats), 'south': min(lats),
              'east':  max(lons), 'west':  min(lons)}
    n, s, e, w = bounds['north'], bounds['south'], bounds['east'], bounds['west']

    # 9 sample points spread over bbox
    sample_coords = [
        (s + (n-s)*fy, w + (e-w)*fx)
        for fy, fx in [(0.1,0.1),(0.1,0.5),(0.1,0.9),
                       (0.5,0.1),(0.5,0.5),(0.5,0.9),
                       (0.9,0.1),(0.9,0.5),(0.9,0.9)]
    ]

    # Fire all HTTP requests in parallel (threads — I/O bound)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        om_futs  = [pool.submit(_fetch_precip_pt,  (lat, lon, s_iso, e_iso)) for lat, lon in sample_coords]
        dm_futs  = [pool.submit(_fetch_daymet_pt,   (lat, lon, s_iso, e_iso)) for lat, lon in sample_coords]
        acis_fut = pool.submit(_fetch_acis_precip, bounds, s_iso, e_iso)
        om_res   = [f.result() for f in om_futs]
        dm_res   = [f.result() for f in dm_futs]
        acis_pts = acis_fut.result()

    # Blend Open-Meteo + Daymet at each sample point
    sample_pts = []
    for i, (lat, lon) in enumerate(sample_coords):
        om, dm = om_res[i], dm_res[i]
        if om and dm:
            sample_pts.append({
                'lat': lat, 'lon': lon,
                'precip_in':   (om['precip_in'] + dm['precip_in']) / 2,
                'snow_in':     om.get('snow_in', 0),
                'soil_temp_f': om.get('soil_temp_f'),
                'source': 'Open-Meteo+Daymet',
            })
        elif om:
            sample_pts.append({**om, 'source': 'Open-Meteo'})
        elif dm:
            sample_pts.append({'lat': lat, 'lon': lon, 'precip_in': dm['precip_in'],
                               'snow_in': 0, 'soil_temp_f': None, 'source': 'Daymet'})

    all_ref = acis_pts + sample_pts   # ACIS has finer resolution; prefer by nearest-neighbor
    avg_in  = sum(p['precip_in'] for p in sample_pts) / max(1, len(sample_pts))
    print(f"[precip] {len(sample_pts)}/9 pts · avg {avg_in:.2f} in · "
          f"ACIS {len(acis_pts)} pts · window={time_window}")

    # Assign per-cell values by nearest-neighbor interpolation
    results = []
    for cell in cells:
        lat, lon = cell['center']
        ref = min(all_ref, key=lambda p: haversine_miles(lat, lon, p['lat'], p['lon'])) if all_ref else None
        results.append({
            'lat':         lat,
            'lon':         lon,
            'bounds':      cell['bounds'],
            'precip_in':   round(ref['precip_in'], 2)      if ref else 0,
            'snow_in':     round(ref.get('snow_in', 0), 2) if ref else 0,
            'soil_temp_f': ref.get('soil_temp_f')          if ref else None,
            'source':      ref.get('source', '')           if ref else '',
        })
    return results

def _parse_esri_fire_features(data, source_name, name_fields, ts_fields, year_field, min_year):
    """Parse ESRI FeatureServer JSON → unified fire list."""
    fires = []
    if data is None:
        print(f"[fires] {source_name}: fetch returned None")
        return fires
    if 'error' in data:
        print(f"[fires] {source_name}: API error: {data['error']}")
        return fires
    if 'features' not in data:
        print(f"[fires] {source_name}: unexpected keys: {list(data.keys())}")
        return fires
    cur_year = datetime.datetime.now().year
    for feat in data['features']:
        attrs = feat.get('attributes') or {}
        # Determine year
        year = cur_year
        if year_field and attrs.get(year_field) is not None:
            try:
                year = int(attrs[year_field])
            except (TypeError, ValueError):
                pass
        else:
            ts = next((attrs[f] for f in ts_fields if attrs.get(f)), None)
            if ts and isinstance(ts, (int, float)) and ts > 0:
                try:
                    year = datetime.datetime.utcfromtimestamp(ts / 1000).year
                except Exception:
                    pass
        if year < min_year:
            continue
        # Determine name
        name = next((attrs[f] for f in name_fields if attrs.get(f)), '')
        # Parse geometry
        esri_geom = feat.get('geometry') or {}
        rings = esri_geom.get('rings', [])
        if not rings:
            continue
        if len(rings) == 1:
            geojson_geom = {'type': 'Polygon', 'coordinates': rings}
        else:
            geojson_geom = {'type': 'MultiPolygon', 'coordinates': [[r] for r in rings]}
        fires.append({
            'feature': {'geometry': geojson_geom, 'properties': {'attr_IncidentName': name}},
            'year': year,
        })
    print(f"[fires] {source_name}: {len(fires)} features (min_year={min_year})")
    return fires


def _fetch_firms_fires(bounds, min_year, map_key):
    """Fetch NASA FIRMS MODIS hotspot detections; each point becomes a ~1 km proxy polygon."""
    fires = []
    days_needed = min(500, max(10, (datetime.date.today() - datetime.date(min_year, 1, 1)).days + 30))
    area = f"{bounds['west']:.3f},{bounds['south']:.3f},{bounds['east']:.3f},{bounds['north']:.3f}"
    url  = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{map_key}/MODIS_C6_1/{area}/{days_needed}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'MorelSupport/1.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode('utf-8')
    except Exception as e:
        print(f"[fires] FIRMS: {e}")
        return fires
    try:
        for row in csv.DictReader(io.StringIO(text)):
            try:
                lat      = float(row['latitude'])
                lon      = float(row['longitude'])
                acq_date = row.get('acq_date', '')
                year     = int(acq_date[:4]) if len(acq_date) >= 4 else datetime.datetime.now().year
            except (ValueError, KeyError):
                continue
            if year < min_year:
                continue
            d    = 0.005   # ~500 m half-width
            ring = [[lon-d, lat-d], [lon+d, lat-d], [lon+d, lat+d], [lon-d, lat+d], [lon-d, lat-d]]
            fires.append({
                'feature': {
                    'geometry':   {'type': 'Polygon', 'coordinates': [ring]},
                    'properties': {'attr_IncidentName': f'FIRMS hotspot {year}'},
                },
                'year': year,
            })
    except Exception as e:
        print(f"[fires] FIRMS parse error: {e}")
    print(f"[fires] FIRMS: {len(fires)} hotspot detections")
    return fires


def fetch_fires(bounds, opts):
    """Fetch fire perimeters from NIFC WFIGS, USFS EDW, CAL FIRE/FRAP, and NASA FIRMS."""
    years_back          = int(opts.get('years_back', 3))
    ignore_current_year = bool(opts.get('ignore_current_year', False))
    cur_year  = datetime.datetime.now().year
    min_year  = cur_year - years_back
    geom = f"{bounds['west']},{bounds['south']},{bounds['east']},{bounds['north']}"
    esri_base = {
        'geometry':          geom,
        'geometryType':      'esriGeometryEnvelope',
        'inSR':              '4326',
        'outSR':             '4326',
        'spatialRel':        'esriSpatialRelIntersects',
        'resultRecordCount': 200,
        'returnGeometry':    'true',
        'f':                 'json',
    }

    # Build a flat list of (url, label, name_fields, ts_fields, year_field) tasks
    tasks = []

    # 1 — NIFC WFIGS
    wfigs_name = ['poly_IncidentName', 'attr_IncidentName', 'IncidentName', 'INCIDENTNAME']
    wfigs_ts   = ['poly_PolygonDateTime', 'poly_DateCurrent', 'attr_FireDiscoveryDateTime',
                  'FireDiscoveryDateTime', 'DISCOVERYDATETIME']
    for svc in [
        'WFIGS_Interagency_Perimeters',
        'WFIGS_Interagency_Perimeters_YearToDate',
        'WFIGS_Interagency_Perimeters_Current',
    ]:
        url = (f'https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/'
               f'{svc}/FeatureServer/0/query?' +
               urllib.parse.urlencode({**esri_base, 'outFields': '*', 'where': '1=1'}))
        tasks.append((url, f'WFIGS/{svc}', wfigs_name, wfigs_ts, None))

    # 2 — USFS EDW
    for usfs_svc, layer, name_fs, ts_fs, yr_f in [
        ('EDW_FireOccurrenceAndPerimeter_01', 0, ['FIRE_NAME'], [], 'FIRE_YEAR'),
        ('EDW_MTBS_01',                       0, ['Fire_Name', 'FIRE_NAME'], [], 'Year'),
    ]:
        url = (f'https://apps.fs.usda.gov/arcx/rest/services/EDW/{usfs_svc}/MapServer/{layer}/query?' +
               urllib.parse.urlencode({**esri_base, 'outFields': '*', 'where': '1=1'}))
        tasks.append((url, f'USFS/{usfs_svc}', name_fs, ts_fs, yr_f))

    # 3 — CAL FIRE / FRAP per-year (California bbox only)
    ca_bbox = bounds['east'] > -124.5 and bounds['west'] < -114.0 \
              and bounds['north'] > 32.5 and bounds['south'] < 42.0
    calfire_years = []
    if ca_bbox:
        for yr in range(min_year, cur_year + 1):
            svc = (f'{yr}_California_Fire_Perimeters'
                   if yr == 2021 else f'{yr}_California_Fire_Perimeters_View')
            url = (f'https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/'
                   f'{svc}/FeatureServer/0/query?' +
                   urllib.parse.urlencode({**esri_base, 'outFields': '*', 'where': '1=1'}))
            tasks.append((url, f'CAL FIRE/{yr}', ['FIRE_NAME', 'INCIDENTNAME'],
                          ['ALARM_DATE', 'CONT_DATE'], 'YEAR_'))
            calfire_years.append(yr)

    # Fetch all URLs in parallel with threads (I/O bound)
    def _fetch_task(task):
        url, label, name_fs, ts_fs, yr_f = task
        return label, yr_f, name_fs, ts_fs, fetch_json(url)

    fires = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as tpool:
        results = list(tpool.map(_fetch_task, tasks))

    for label, yr_f, name_fs, ts_fs, data in results:
        # CAL FIRE: stop accumulating once we hit a year that isn't published
        if label.startswith('CAL FIRE/') and data and \
                data.get('error', {}).get('message') == 'Invalid URL':
            break
        fires += _parse_esri_fire_features(data, label, name_fs, ts_fs, yr_f, min_year)

    # 4 — NASA FIRMS (requires FIRMS_MAP_KEY env var)
    firms_key = os.environ.get('FIRMS_MAP_KEY', '').strip()
    if firms_key:
        fires += _fetch_firms_fires(bounds, min_year, firms_key)
    else:
        print('[fires] FIRMS: skipped (set FIRMS_MAP_KEY env var to enable)')

    # Optionally drop fires from the current calendar year
    if ignore_current_year:
        before = len(fires)
        fires  = [f for f in fires if f['year'] != cur_year]
        print(f"[fires] dropped {before - len(fires)} current-year features")

    # Deduplicate by incident name + year
    seen, unique = set(), []
    for fire in fires:
        key = fire['feature']['properties']['attr_IncidentName'] + str(fire['year'])
        if key not in seen:
            seen.add(key)
            unique.append(fire)
    print(f"[fires] {len(unique)} perimeters after dedup (min_year={min_year})")
    return unique

def fetch_trees(bounds, opts):
    """Fetch host tree occurrences from GBIF + LANDFIRE + USFS TreeMap + NLCD."""
    selected = opts.get('species', ['douglas_fir', 'pine', 'white_fir',
                                    'oak', 'cottonwood', 'sycamore', 'ash'])
    taxon_map = {
        'elm':          6265,
        'ash':          3189866,
        'oak':          2877951,
        'tulip_poplar': 3190081,
        'cottonwood':   3190165,
        # Burn-zone conifer species
        'douglas_fir':  5284895,   # Pseudotsuga menziesii
        'pine':         2684241,   # Pinus (genus — covers all pines)
        'white_fir':    2684222,   # Abies (genus — covers white fir + relatives)
    }

    def _gbif():
        res = []
        for sp in selected:
            if sp not in taxon_map:
                continue
            params = {
                'taxonKey':          taxon_map[sp],
                'decimalLatitude':   f"{bounds['south']},{bounds['north']}",
                'decimalLongitude':  f"{bounds['west']},{bounds['east']}",
                'limit':             100,
                'hasCoordinate':     'true',
                'occurrenceStatus':  'PRESENT',
            }
            url  = 'https://api.gbif.org/v1/occurrence/search?' + urllib.parse.urlencode(params)
            data = fetch_json(url)
            if data:
                for obs in data.get('results', []):
                    lat = obs.get('decimalLatitude')
                    lon = obs.get('decimalLongitude')
                    if lat and lon:
                        res.append({'lat': lat, 'lon': lon, 'species': sp, 'source': 'GBIF'})
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        gbif_fut    = pool.submit(_gbif)
        lf_fut      = pool.submit(_fetch_landfire_trees_bbox, bounds, selected)
        treemap_fut = pool.submit(_fetch_usfs_treemap_bbox, bounds)
        nlcd_fut    = pool.submit(_fetch_nlcd_bbox, bounds)

        results = gbif_fut.result()
        results += lf_fut.result()
        results += treemap_fut.result()
        results += nlcd_fut.result()

    print(f"[trees] {len(results)} occurrences (GBIF + LANDFIRE + TreeMap + NLCD)")
    return results


# ── EVT keyword → species mapping ──────────────────────────────────────────────
_EVT_KEYWORDS = {
    'elm':         ['elm'],
    'ash':         ['ash'],
    'oak':         ['oak'],
    'tulip_poplar':['tulip', 'yellow-poplar', 'yellow poplar'],
    'cottonwood':  ['cottonwood', 'populus'],
    'hickory':     ['hickory'],
    'maple':       ['maple'],
    'walnut':      ['walnut'],
    'sycamore':    ['sycamore'],
    # Burn-zone conifers
    'douglas_fir': ['douglas-fir', 'douglas fir', 'pseudotsuga'],
    'pine':        ['pine', 'pinus'],
    'white_fir':   ['white fir', 'abies concolor', 'grand fir', 'abies'],
}

# Species whose presence inside a fire perimeter triggers a probability boost
BURN_SPECIES = frozenset({'douglas_fir', 'pine', 'white_fir'})

def _evt_name_to_species(evt_name: str) -> str | None:
    """Return the first matching species key for an EVT name, or None."""
    low = evt_name.lower()
    for sp, kws in _EVT_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return sp
    if any(kw in low for kw in ['deciduous', 'hardwood', 'forest']):
        return 'deciduous'
    return None


# ── LANDFIRE lazy service discovery ────────────────────────────────────────────
# Probe the LANDFIRE ArcGIS REST root once on first use; cache result so we
# never make more than ~3 catalogue requests regardless of grid size.
_lf_lock           = threading.Lock()
_lf_service_url    = None   # str path like ".../ImageServer/identify" or ".../MapServer/0/query"
_lf_service_type   = None   # 'identify' | 'query'
_lf_probed         = False

_LF_ROOTS = [
    # Try USGS EROS / LFPS hosts — www.landfire.gov /arcgis/rest/services root is 404
    'https://lfps.usgs.gov/arcgis/rest/services',
    'https://edcintl.cr.usgs.gov/arcgis/rest/services',
    'https://edcintl.cr.usgs.gov/arcgis/rest/services/Landfire',
    'https://www.landfire.gov/arcgis/rest/services',
]

def _discover_landfire():
    """Walk LANDFIRE ArcGIS REST catalogue to find the EVT service. Runs once."""
    global _lf_service_url, _lf_service_type, _lf_probed
    with _lf_lock:
        if _lf_probed:
            return
        _lf_probed = True
        for root in _LF_ROOTS:
            d = fetch_json(root + '?f=json', timeout=10)
            if not d:
                continue
            # Flatten all service entries across folders
            all_svcs = list(d.get('services', []))
            for folder in d.get('folders', []):
                fd = fetch_json(f'{root}/{folder}?f=json', timeout=10)
                if fd:
                    all_svcs.extend(fd.get('services', []))
            for svc in all_svcs:
                name = svc.get('name', '')
                stype = svc.get('type', '')
                if 'EVT' not in name.upper():
                    continue
                if stype == 'ImageServer':
                    _lf_service_url  = f'{root}/{name}/ImageServer/identify'
                    _lf_service_type = 'identify'
                    print(f"[trees] LANDFIRE discovered: {_lf_service_url}")
                    return
                if stype == 'MapServer':
                    _lf_service_url  = f'{root}/{name}/MapServer/0/query'
                    _lf_service_type = 'query'
                    print(f"[trees] LANDFIRE discovered: {_lf_service_url}")
                    return
        print("[trees] LANDFIRE: no EVT service found in catalogue — skipping")


def _fetch_landfire_evt_pt(args):
    """Query LANDFIRE EVT at a single lat/lon point using the discovered service URL."""
    lat, lon = args
    if not globe.is_land(lat, lon):
        return None
    _discover_landfire()
    if not _lf_service_url:
        return None

    if _lf_service_type == 'identify':
        params = {
            'geometry':       f'{lon},{lat}',
            'geometryType':   'esriGeometryPoint',
            'inSR':           '4326',
            'returnGeometry': 'false',
            'f':              'json',
        }
        data = fetch_json(_lf_service_url + '?' + urllib.parse.urlencode(params), timeout=10)
        if not data:
            return None
        evt_name = (
            (data.get('properties') or {}).get('EVT_NAME')
            or (data.get('attributes') or {}).get('EVT_NAME')
            or str(data.get('value') or '')
        )
    else:  # query
        params = {
            'geometry':       f'{lon},{lat}',
            'geometryType':   'esriGeometryPoint',
            'inSR':           '4326',
            'spatialRel':     'esriSpatialRelIntersects',
            'where':          '1=1',
            'outFields':      'EVT_NAME,EVT_PHYS',
            'returnGeometry': 'false',
            'f':              'json',
        }
        data = fetch_json(_lf_service_url + '?' + urllib.parse.urlencode(params), timeout=10)
        if not data or 'features' not in data:
            return None
        evt_name = ''
        for feat in data['features']:
            evt_name = (feat.get('attributes') or {}).get('EVT_NAME', '') or ''
            if evt_name:
                break

    sp = _evt_name_to_species(str(evt_name))
    if sp:
        return {'lat': lat, 'lon': lon, 'species': sp, 'evt_name': evt_name, 'source': 'LANDFIRE'}
    return None


def _fetch_landfire_trees_bbox(bounds, selected):
    """Sample a 4×4 grid of LANDFIRE EVT points across the bounding box."""
    # Quick check: if catalogue probe already failed, bail immediately
    _discover_landfire()
    if not _lf_service_url:
        return []
    n, s, e, w = bounds['north'], bounds['south'], bounds['east'], bounds['west']
    steps = 4
    pts = [
        (s + (n - s) * (fi + 0.5) / steps, w + (e - w) * (fj + 0.5) / steps)
        for fi in range(steps) for fj in range(steps)
    ]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(_fetch_landfire_evt_pt, pts):
            if r and (r['species'] in selected or r['species'] == 'deciduous'):
                results.append(r)
    print(f"[trees] LANDFIRE: {len(results)} EVT matches")
    return results


def _fetch_usfs_treemap_bbox(bounds):
    """Query USFS EDW ForestCoverType / TreeMap for the bounding box."""
    n, s, e, w = bounds['north'], bounds['south'], bounds['east'], bounds['west']
    envelope = f'{w},{s},{e},{n}'
    services = [
        'https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_ForestCoverType_01/MapServer/0/query',
        'https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_ForestCoverType_01/MapServer/1/query',
        'https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_ForestAttributes_01/MapServer/0/query',
        'https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_TreeMap2016_01/MapServer/0/query',
        'https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_TreeMap2016_01/FeatureServer/0/query',
    ]
    params = {
        'geometry':          envelope,
        'geometryType':      'esriGeometryEnvelope',
        'inSR':              '4326',
        'outSR':             '4326',
        'spatialRel':        'esriSpatialRelIntersects',
        'where':             '1=1',
        'outFields':         '*',
        'resultRecordCount': 50,
        'returnGeometry':    'true',
        'f':                 'json',
    }
    for url_base in services:
        data = fetch_json(url_base + '?' + urllib.parse.urlencode(params), timeout=12)
        if not data or 'error' in data:
            continue
        feats = data.get('features', [])
        if not feats:
            continue   # empty for this area — try next service variant
        results = []
        for feat in feats:
            attrs = feat.get('attributes') or {}
            geom  = feat.get('geometry') or {}
            lat = lon = None
            if 'x' in geom and 'y' in geom:
                lon, lat = geom['x'], geom['y']
            elif 'rings' in geom:
                ring = geom['rings'][0]
                xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
                if xs and ys:
                    lon, lat = sum(xs) / len(xs), sum(ys) / len(ys)
            if lat is None or lon is None:
                continue
            sp = None
            for v in attrs.values():
                if isinstance(v, str) and v:
                    sp = _evt_name_to_species(v)
                    if sp:
                        break
            results.append({'lat': lat, 'lon': lon, 'species': sp or 'deciduous', 'source': 'USFS_TreeMap'})
        if results:
            print(f"[trees] USFS TreeMap ({url_base.split('/')[-4]}): {len(results)} features")
            return results
    print("[trees] USFS TreeMap: no results")
    return []


# NLCD class → species mapping (MRLC classes)
_NLCD_SPECIES = {
    41: 'deciduous',   # Deciduous Forest
    43: 'deciduous',   # Mixed Forest
    90: 'cottonwood',  # Woody Wetlands (cottonwood/willow habitat)
    95: 'deciduous',   # Emergent Herbaceous Wetlands (occasional)
}

def _fetch_nlcd_pt(args):
    """Get NLCD land-cover class at a point via Esri Living Atlas ImageServer identify."""
    lat, lon = args
    if not globe.is_land(lat, lon):
        return None
    # Esri Living Atlas hosts a public NLCD 2019 ImageServer — no auth required.
    params = {
        'geometry':       f'{lon},{lat}',
        'geometryType':   'esriGeometryPoint',
        'inSR':           '4326',
        'returnGeometry': 'false',
        'f':              'json',
    }
    url  = ('https://landscape2.arcgis.com/arcgis/rest/services/USA_NLCD_2019/'
            'ImageServer/identify?' + urllib.parse.urlencode(params))
    data = fetch_json(url, timeout=10)
    if not data:
        return None
    # Response: {"value": "41", "properties": {"Values": ["41"]}, ...}
    raw = (data.get('value')
           or (data.get('properties') or {}).get('Values', [None])[0])
    try:
        val = int(float(str(raw)))
    except (TypeError, ValueError):
        return None
    sp = _NLCD_SPECIES.get(val)
    if sp:
        return {'lat': lat, 'lon': lon, 'species': sp, 'nlcd_class': val, 'source': 'NLCD'}
    return None


def _fetch_nlcd_bbox(bounds):
    """Sample NLCD forest cover across the bounding box via MRLC WMS."""
    n, s, e, w = bounds['north'], bounds['south'], bounds['east'], bounds['west']
    steps = 4
    pts = [
        (s + (n - s) * (fi + 0.5) / steps, w + (e - w) * (fj + 0.5) / steps)
        for fi in range(steps) for fj in range(steps)
    ]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for r in pool.map(_fetch_nlcd_pt, pts):
            if r:
                results.append(r)
    print(f"[trees] NLCD: {len(results)} forest pts")
    return results


def fetch_elevation(cells, _opts):
    """Batch-fetch elevations from OpenTopoData (max 100 pts/call)."""
    if not cells:
        return {}
    centers = [c['center'] for c in cells[:100]]
    loc_str = '|'.join(f"{lat},{lon}" for lat, lon in centers)
    url  = 'https://api.opentopodata.org/v1/ned10m?locations=' + urllib.parse.quote(loc_str)
    data = fetch_json(url, timeout=20)
    out  = {}
    if data and 'results' in data:
        for i, r in enumerate(data['results']):
            if i < len(centers):
                lat, lon = centers[i]
                out[(round(lat, 6), round(lon, 6))] = r.get('elevation')
    print(f"[elevation] {len(out)} values")
    return out

def fetch_soil(bounds, _opts):
    """Fetch soil properties (pH, texture) from SoilGrids ISRIC."""
    # Sample 4 corner points + center
    n, s, e, w = bounds['north'], bounds['south'], bounds['east'], bounds['west']
    pts = [
        ((n + s) / 2, (e + w) / 2),
        (s + (n-s)*0.25, w + (e-w)*0.25),
        (s + (n-s)*0.75, w + (e-w)*0.75),
    ]
    results = []
    for lat, lon in pts:
        params = {
            'lon':      round(lon, 4),
            'lat':      round(lat, 4),
            'property': 'phh2o,clay,ocd',
            'depth':    '5-15cm',
            'value':    'mean',
        }
        url  = 'https://rest.isric.org/soilgrids/v2.0/properties/query?' + urllib.parse.urlencode(params)
        data = fetch_json(url)
        if data and 'properties' in data:
            props  = data['properties']
            layers = {p['name']: p for p in props.get('layers', [])}
            ph_val  = None
            clay_val = None
            if 'phh2o' in layers:
                for depth in layers['phh2o'].get('depths', []):
                    if depth.get('label') == '5-15cm':
                        ph_val = depth['values'].get('mean')
                        if ph_val:
                            ph_val /= 10  # stored as pH*10
            if 'clay' in layers:
                for depth in layers['clay'].get('depths', []):
                    if depth.get('label') == '5-15cm':
                        clay_val = depth['values'].get('mean')
            results.append({'lat': lat, 'lon': lon, 'ph': ph_val, 'clay': clay_val})
    print(f"[soil] {len(results)} sample points")
    return results

# ── Scoring Functions ──────────────────────────────────────────────────────────

def score_inat(cell_lat, cell_lon, obs_list, opts, target_date):
    if not obs_list:
        return 0.0, 0
    target_doy = target_date.timetuple().tm_yday
    max_dist   = 25.0
    best  = 0.0
    count = 0
    for obs in obs_list:
        try:
            loc = obs.get('location', '')
            if not loc:
                continue
            parts   = loc.split(',')
            obs_lat = float(parts[0])
            obs_lon = float(parts[1])
            dist    = haversine_miles(cell_lat, cell_lon, obs_lat, obs_lon)
            if dist > max_dist:
                continue
            count += 1
            dist_score = max(0.0, 1.0 - (dist / max_dist) ** 0.7)
            # Seasonal weighting: sightings near target day-of-year score higher
            time_score = 0.5
            obs_date_str = obs.get('observed_on', '')
            if obs_date_str and opts.get('seasonal_weight', True):
                d        = datetime.date.fromisoformat(obs_date_str)
                doy      = d.timetuple().tm_yday
                doy_diff = abs(target_doy - doy)
                if doy_diff > 183:
                    doy_diff = 366 - doy_diff
                time_score  = max(0.05, 1.0 - doy_diff / 45.0)
                years_ago   = target_date.year - d.year
                time_score *= max(0.25, 1.0 - years_ago * 0.08)
            best = max(best, dist_score * time_score)
        except Exception:
            continue
    density_bonus = min(0.15, count * 0.015)
    return min(1.0, best + density_bonus), count

def score_precip(cell_lat, cell_lon, precip_pts, _opts):
    if not precip_pts:
        return 0.5, {}
    pt     = min(precip_pts, key=lambda p: haversine_miles(cell_lat, cell_lon, p['lat'], p['lon']))
    inches = pt.get('precip_in', pt.get('precip_mm', 0) / 25.4)
    # Precipitation response curve — morels love 1–3 inches
    if   inches < 0.2:  ps = 0.05
    elif inches < 0.5:  ps = 0.30
    elif inches < 1.0:  ps = 0.60
    elif inches < 2.0:  ps = 0.85
    elif inches < 3.5:  ps = 1.00
    elif inches < 6.0:  ps = 0.75
    else:               ps = 0.45
    # Soil temperature curve — optimal 45–65°F
    tf = pt.get('soil_temp_f')
    if   tf is None:  ts = 0.5
    elif tf < 32:     ts = 0.0
    elif tf < 42:     ts = 0.20
    elif tf < 50:     ts = 0.75
    elif tf < 60:     ts = 1.00
    elif tf < 68:     ts = 0.65
    elif tf < 78:     ts = 0.25
    else:             ts = 0.05
    combined = ps * 0.55 + ts * 0.45
    return combined, {
        'Precipitation': f"{inches:.1f} in",
        'Soil Temp':     f"{tf:.0f}°F" if tf else 'N/A',
    }

def score_fires(cell_lat, cell_lon, cell_bounds, fires, _opts):
    if not fires:
        return 0.0, 0
    now  = datetime.datetime.now().year
    best = 0.0
    count = 0
    for fire in fires:
        years_ago = now - fire['year']
        # Post-fire morel fruiting curve
        if   years_ago < 0:  age = 0.40   # planned/active
        elif years_ago == 0: age = 0.70   # very fresh
        elif years_ago == 1: age = 1.00   # peak year
        elif years_ago == 2: age = 0.85
        elif years_ago == 3: age = 0.60
        elif years_ago == 4: age = 0.35
        else:                age = 0.15
        geom = fire['feature'].get('geometry', {})
        if not geom or not geom.get('coordinates'):
            continue
        try:
            coords = geom['coordinates']
            if   geom['type'] == 'Polygon':      flat = [c for ring in coords for c in ring]
            elif geom['type'] == 'MultiPolygon': flat = [c for poly in coords for ring in poly for c in ring]
            else: continue
            if not flat:
                continue
            fs = min(c[1] for c in flat); fn = max(c[1] for c in flat)
            fw = min(c[0] for c in flat); fe = max(c[0] for c in flat)
            overlap = (cell_bounds['south'] < fn and cell_bounds['north'] > fs and
                       cell_bounds['west']  < fe and cell_bounds['east']  > fw)
            if overlap:
                score = age
                count += 1
            else:
                nearest_lat = max(fs, min(fn, cell_lat))
                nearest_lon = max(fw, min(fe, cell_lon))
                dist  = haversine_miles(cell_lat, cell_lon, nearest_lat, nearest_lon)
                score = age * max(0.0, 1.0 - dist / 20.0)
            best = max(best, score)
        except Exception:
            continue
    return min(1.0, best), count

def score_trees(cell_lat, cell_lon, tree_obs, _opts):
    if not tree_obs:
        return 0.5, 0   # neutral — absence of data ≠ absence of trees
    sp_weights = {
        # ── Non-burn hardwood species ──
        'ash':          0.90,
        'elm':          0.85,
        'tulip_poplar': 0.80,
        'cottonwood':   0.70,
        'oak':          0.65,
        'sycamore':     0.65,
        'walnut':       0.60,
        'hickory':      0.60,
        'maple':        0.55,
        'deciduous':    0.40,
        # ── Burn-zone conifer species (base weight; fire-zone boost applied in worker) ──
        'douglas_fir':  0.70,
        'white_fir':    0.65,
        'pine':         0.60,
    }
    max_dist = 8.0
    best  = 0.0
    count = 0
    for obs in tree_obs:
        dist = haversine_miles(cell_lat, cell_lon, obs['lat'], obs['lon'])
        if dist > max_dist:
            continue
        count += 1
        w     = sp_weights.get(obs.get('species', ''), 0.40)
        score = w * max(0.0, 1.0 - (dist / max_dist) ** 0.8)
        best  = max(best, score)
    return min(1.0, best), count

def score_seasonality(lat, _lon, target_date):
    """Estimate probability based on target date vs. typical morel window."""
    doy      = target_date.timetuple().tm_yday
    peak_doy = 70 + (lat - 30) * 2.5   # ~DOY 70 at 30°N, ~120 at 50°N
    window   = 25
    diff     = abs(doy - peak_doy)
    if diff < window:
        return max(0.30, 1.0 - (diff / window) ** 1.5)
    elif diff < 55:
        return max(0.05, 0.40 * (1.0 - (diff - window) / 30.0))
    else:
        return 0.05

def score_elevation(cell_lat, cell_lon, elev_map):
    key    = (round(cell_lat, 6), round(cell_lon, 6))
    elev_m = elev_map.get(key)
    if elev_m is None:
        return 0.5
    ft = elev_m * 3.281
    if   ft < 0:     return 0.10
    elif ft < 300:   return 0.30
    elif ft < 600:   return 0.65
    elif ft < 1500:  return 1.00
    elif ft < 3000:  return 0.90
    elif ft < 5000:  return 0.55
    elif ft < 8000:  return 0.25
    else:            return 0.10

def score_soil(cell_lat, cell_lon, soil_pts):
    if not soil_pts:
        return 0.5
    pt = min(soil_pts, key=lambda p: haversine_miles(cell_lat, cell_lon, p['lat'], p['lon']))
    ph   = pt.get('ph')
    clay = pt.get('clay')   # g/kg
    ph_score = 0.5
    if ph is not None:
        # Morels prefer pH 6.0–7.5
        if   ph < 4.5:  ph_score = 0.10
        elif ph < 5.5:  ph_score = 0.35
        elif ph < 6.0:  ph_score = 0.65
        elif ph < 7.0:  ph_score = 1.00
        elif ph < 7.5:  ph_score = 0.85
        elif ph < 8.0:  ph_score = 0.50
        else:           ph_score = 0.20
    clay_score = 0.5
    if clay is not None:
        # Prefer loamy soils (low–moderate clay, 50–250 g/kg)
        if   clay < 50:   clay_score = 0.60
        elif clay < 200:  clay_score = 1.00
        elif clay < 350:  clay_score = 0.70
        else:             clay_score = 0.30
    return ph_score * 0.6 + clay_score * 0.4

# ── Probability Combination ────────────────────────────────────────────────────

DEFAULT_WEIGHTS = {
    'inat':      0.30,
    'precip':    0.25,
    'fires':     0.20,
    'trees':     0.12,
    'season':    0.07,
    'soil':      0.04,
    'elevation': 0.02,
}

def combine(scores, custom_weights):
    if not scores:
        return 0.0
    w = {k: custom_weights.get(k, DEFAULT_WEIGHTS.get(k, 0)) for k in scores}
    total = sum(w.values())
    if total == 0:
        return 0.0
    return sum(scores[k] * w[k] for k in scores) / total

# ── Process-pool worker (module level — required for pickle/ProcessPoolExecutor) ─

def _score_cell_worker(args):
    """Score a single grid cell. Runs in a worker process."""
    cell, layer_data, layers_cfg, weights, target_date = args
    lat, lon = cell['center']
    cb       = cell['bounds']
    scores   = {}
    details  = {}

    if 'inat' in layer_data:
        s, cnt = score_inat(lat, lon, layer_data['inat'], layers_cfg.get('inat', {}), target_date)
        scores['inat'] = s
        details['iNat sightings'] = cnt

    if 'precip' in layer_data:
        s, det = score_precip(lat, lon, layer_data['precip'], layers_cfg.get('precip', {}))
        scores['precip'] = s
        details.update(det)

    if 'fires' in layer_data:
        s, cnt = score_fires(lat, lon, cb, layer_data['fires'], layers_cfg.get('fires', {}))
        scores['fires'] = s
        details['Fire perimeters'] = cnt

    if 'trees' in layer_data:
        s, cnt = score_trees(lat, lon, layer_data['trees'], layers_cfg.get('trees', {}))
        scores['trees'] = s
        details['Host trees'] = cnt

    # Burn-species fire boost: burn-zone conifers (Douglas-fir, pine, white fir)
    # near an active fire perimeter dramatically increase morel probability.
    if 'fires' in scores and 'trees' in layer_data and scores.get('fires', 0) > 0.15:
        burn_nearby = [
            obs for obs in layer_data['trees']
            if obs.get('species') in BURN_SPECIES
            and haversine_miles(lat, lon, obs['lat'], obs['lon']) <= 8.0
        ]
        if burn_nearby:
            scores['fires'] = min(1.0, scores['fires'] * 1.55)
            scores['trees'] = min(1.0, scores.get('trees', 0.5) * 1.45)
            details['Burn species in fire zone'] = len(burn_nearby)

    if layers_cfg.get('season', {}).get('enabled'):
        scores['season'] = score_seasonality(lat, lon, target_date)

    if 'elevation' in layer_data:
        scores['elevation'] = score_elevation(lat, lon, layer_data['elevation'])

    if 'soil' in layer_data:
        scores['soil'] = score_soil(lat, lon, layer_data['soil'])

    prob = combine(scores, weights)
    b    = cb
    return {
        'type': 'Feature',
        'geometry': {
            'type': 'Polygon',
            'coordinates': [[
                [b['west'], b['south']], [b['east'], b['south']],
                [b['east'], b['north']], [b['west'], b['north']],
                [b['west'], b['south']],
            ]],
        },
        'properties': {
            'probability':  round(prob * 100),
            'layer_scores': {k: round(v * 100) for k, v in scores.items()},
            'details':      details,
        },
    }

# ── Layer Cache ────────────────────────────────────────────────────────────────
#
# TTLs per layer (seconds) — more volatile layers expire sooner:
_CACHE_TTL = {
    'precip':    6  * 3600,   # 6 h  — weather data changes
    'fires':     12 * 3600,   # 12 h — incident updates a few times/day
    'inat':      24 * 3600,   # 24 h — observations rarely disappear
    'trees':     48 * 3600,   # 48 h — species distribution is stable
    'soil':      72 * 3600,   # 72 h — soil properties essentially static
    'elevation': 72 * 3600,   # 72 h — elevation never changes
}
# Invalidate if viewport center moves more than this many miles from cached center
CACHE_RADIUS_MILES = 75

_cache: dict = {}
_cache_lock  = threading.Lock()

def _bounds_center(bounds):
    return ((bounds['north'] + bounds['south']) / 2,
            (bounds['east']  + bounds['west'])  / 2)

def _opts_hash(opts: dict) -> str:
    return hashlib.md5(json.dumps(opts, sort_keys=True).encode()).hexdigest()[:10]

def cache_get(layer: str, bounds: dict, opts: dict):
    key   = (layer, _opts_hash(opts))
    now   = datetime.datetime.utcnow()
    ttl   = _CACHE_TTL.get(layer, 24 * 3600)
    clat, clon = _bounds_center(bounds)
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        age  = (now - entry['fetched_at']).total_seconds()
        dist = haversine_miles(clat, clon, entry['center'][0], entry['center'][1])
        if age > ttl or dist > CACHE_RADIUS_MILES:
            del _cache[key]
            reason = f"expired ({age/3600:.1f}h old)" if age > ttl else f"moved {dist:.0f} mi"
            print(f"[cache] {layer} invalidated — {reason}")
            return None
        print(f"[cache] {layer} hit  (center {dist:.1f} mi away, {age/3600:.1f}h old)")
        return entry['data']

def cache_set(layer: str, bounds: dict, opts: dict, data):
    key = (layer, _opts_hash(opts))
    with _cache_lock:
        _cache[key] = {
            'center':     _bounds_center(bounds),
            'fetched_at': datetime.datetime.utcnow(),
            'data':       data,
        }

# ── API Endpoints ──────────────────────────────────────────────────────────────

def _build_raw_geojson(layer_name, data):
    """Convert raw fetched layer data to a list of GeoJSON features."""
    if layer_name == 'inat':
        today = datetime.date.today()
        features = []
        for obs in (data or []):
            loc = obs.get('location', '')
            if not loc:
                continue
            parts = loc.split(',')
            if len(parts) < 2:
                continue
            try:
                lat, lon = float(parts[0]), float(parts[1])
            except ValueError:
                continue
            obs_date  = obs.get('observed_on', '')
            years_ago = 0
            if obs_date:
                try:
                    years_ago = today.year - datetime.date.fromisoformat(obs_date).year
                except Exception:
                    pass
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': {
                    'observed_on':   obs_date,
                    'taxon_name':    (obs.get('taxon') or {}).get('name', 'Morchella'),
                    'quality_grade': obs.get('quality_grade', ''),
                    'user':          (obs.get('user') or {}).get('login', ''),
                    'uri':           obs.get('uri', ''),
                    'years_ago':     years_ago,
                },
            })
        return features

    if layer_name == 'fires':
        features = []
        for fire in (data or []):
            inner = fire['feature']
            props = dict(inner.get('properties', {}))
            props['year'] = fire['year']
            features.append({
                'type':       'Feature',
                'geometry':   inner['geometry'],
                'properties': props,
            })
        return features

    if layer_name == 'precip':
        features = []
        for pt in (data or []):
            b     = pt.get('bounds')
            props = {
                'precip_in':   round(pt.get('precip_in', 0), 2),
                'snow_in':     round(pt.get('snow_in', 0), 2),
                'soil_temp_f': pt.get('soil_temp_f'),
                'source':      pt.get('source', ''),
            }
            if b:
                features.append({
                    'type': 'Feature',
                    'geometry': {
                        'type': 'Polygon',
                        'coordinates': [[
                            [b['west'], b['south']], [b['east'], b['south']],
                            [b['east'], b['north']], [b['west'], b['north']],
                            [b['west'], b['south']],
                        ]],
                    },
                    'properties': props,
                })
            else:
                features.append({
                    'type': 'Feature',
                    'geometry': {'type': 'Point', 'coordinates': [pt['lon'], pt['lat']]},
                    'properties': props,
                })
        return features

    if layer_name == 'trees':
        return [
            {
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [obs['lon'], obs['lat']]},
                'properties': {'species': obs.get('species', '')},
            }
            for obs in (data or [])
        ]

    if layer_name == 'soil':
        return [
            {
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [pt['lon'], pt['lat']]},
                'properties': {'ph': pt.get('ph'), 'clay': pt.get('clay')},
            }
            for pt in (data or [])
        ]

    if layer_name == 'elevation':
        return [
            {
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                'properties': {'elevation_ft': round(elev * 3.281) if elev else None},
            }
            for (lat, lon), elev in (data or {}).items()
        ]

    return []

@app.route('/api/calculate', methods=['POST'])
def calculate():
    body           = request.get_json()
    bounds         = body['bounds']
    resolution     = max(1.0, min(20.0, float(body.get('resolution', 5))))
    layers         = body.get('layers', {})
    weights        = body.get('weights', {})
    lookahead_weeks = max(0, int(body.get('lookahead_weeks', 0)))
    target_date    = datetime.date.today() + datetime.timedelta(weeks=lookahead_weeks)

    grid = create_grid(bounds, resolution)
    if not grid:
        return jsonify({'type': 'FeatureCollection', 'features': [], 'meta': {}})

    # Fetch all enabled layers in parallel — use cache where available
    layer_data = {}
    FETCHERS = {
        'inat':      (fetch_inat,      lambda: (bounds, layers['inat'])),
        'precip':    (fetch_precip,    lambda: (grid,   layers['precip'])),
        'fires':     (fetch_fires,     lambda: (bounds, layers['fires'])),
        'trees':     (fetch_trees,     lambda: (bounds, layers['trees'])),
        'elevation': (fetch_elevation, lambda: (grid,   layers['elevation'])),
        'soil':      (fetch_soil,      lambda: (bounds, layers['soil'])),
    }
    with concurrent.futures.ProcessPoolExecutor(max_workers=CPU_COUNT, mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = {}
        for k, (fn, args_fn) in FETCHERS.items():
            if not layers.get(k, {}).get('enabled'):
                continue
            cached = cache_get(k, bounds, layers[k])
            if cached is not None:
                layer_data[k] = cached
            else:
                futures[k] = pool.submit(fn, *args_fn())
        for k, f in futures.items():
            try:
                data = f.result(timeout=90)
                layer_data[k] = data
                cache_set(k, bounds, layers[k], data)
            except Exception as e:
                print(f"[{k}] error: {e}")
                layer_data[k] = {} if k == 'elevation' else []

    # Single-layer raw mode: return the fetched data directly (no probability scoring)
    enabled_data_layers = [k for k in FETCHERS if layers.get(k, {}).get('enabled')]
    if len(enabled_data_layers) == 1:
        layer_name   = enabled_data_layers[0]
        raw_features = _build_raw_geojson(layer_name, layer_data.get(layer_name))
        print(f"[raw] {layer_name} → {len(raw_features)} features")
        return jsonify({
            'type':     'FeatureCollection',
            'features': raw_features,
            'meta':     {'raw_layer': layer_name, 'count': len(raw_features), 'target_date': target_date.isoformat()},
        })

    # Score each grid cell across CPU cores using multiple processes.
    # ProcessPoolExecutor bypasses the GIL for true parallelism on CPU-bound work.
    # 'fork' context avoids re-importing all modules in each worker (fast startup).
    ctx         = multiprocessing.get_context('spawn')
    worker_args = [(cell, layer_data, layers, weights, target_date) for cell in grid]
    chunksize   = max(1, len(grid) // (CPU_COUNT * 4))
    with concurrent.futures.ProcessPoolExecutor(max_workers=CPU_COUNT, mp_context=ctx) as pool:
        features = list(pool.map(_score_cell_worker, worker_args, chunksize=chunksize))

    total_obs = sum(1 for f in features if f['properties']['probability'] >= 30)
    print(f"[calc] {len(features)} cells, {total_obs} cells ≥30%")
    return jsonify({
        'type':     'FeatureCollection',
        'features': features,
        'meta':     {'cells': len(features), 'resolution_miles': resolution, 'target_date': target_date.isoformat()},
    })

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    port = 8081
    threading.Timer(1.2, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    print(f'🍄  Morel Support →  http://localhost:{port}')
    app.run(port=port, debug=False)
