"""
Unified Airfoil Data Scraper
Author: Salih.E / Antigravity Assistant

Downloads:
1. Airfoil Polar Graphs (aerodynamic data) -> airfoiltools-data/airfoil_graphs.csv
2. Airfoil Profile Coordinates (Selig .dat format) -> airfoiltools-data/airfoil_profiles.json

All data is consolidated into single files directly inside the airfoiltools-data directory.
"""

import os
import sys
import re
import csv
import json
import time
import argparse
import threading
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# Base URLs
AIRFOILTOOLS_BASE = "http://airfoiltools.com"
UIUC_BASE_URL = "https://m-selig.ae.illinois.edu/ads/"
UIUC_INDEX_URL = "https://m-selig.ae.illinois.edu/ads/coord_database.html"

# Default Output Paths (Single Files in airfoiltools-data)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "airfoiltools-data")
DEFAULT_GRAPHS_FILE = os.path.join(DATA_DIR, "airfoil_graphs.csv")
DEFAULT_PROFILES_FILE = os.path.join(DATA_DIR, "airfoil_profiles.json")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

GRAPH_FIELDNAMES = [
    'Airfoil', 'Polar_Key', 'Reynolds_Number', 'Ncrit', 'Mach',
    'Max_Cl_Cd', 'Max_Cl_Cd_Alpha', 'Alpha', 'Cl', 'Cd', 'Cdp', 'Cm',
    'Top_Xtr', 'Bot_Xtr'
]

PROFILE_CSV_FIELDNAMES = ['Airfoil', 'Title', 'Source', 'Point_Index', 'X', 'Y']

# Threading locks for safe concurrent single-file writes
graph_lock = threading.Lock()
profile_lock = threading.Lock()


# ==============================================================================
# HTTP Networking Utility
# ==============================================================================
def fetch_url(url, retries=3, backoff_factor=1, timeout=10):
    """Fetch URL text content with retries and exponential backoff."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            if attempt == retries - 1:
                return None
            time.sleep(backoff_factor * (2 ** attempt))
    return None


# ==============================================================================
# Airfoil Discovery Functions
# ==============================================================================
def get_airfoiltools_airfoils():
    """Scrape list of all airfoil IDs from AirfoilTools search page."""
    search_url = f"{AIRFOILTOOLS_BASE}/search/airfoils"
    html = fetch_url(search_url)
    if not html:
        print("[-] Failed to fetch AirfoilTools index.")
        return []

    if HAS_BS4:
        soup = BeautifulSoup(html, 'html.parser')
        links = soup.find_all('a', href=re.compile(r'/airfoil/details\?airfoil='))
        airfoils = []
        for a in links:
            m = re.search(r'airfoil=([^&"\' >]+)', a['href'])
            if m:
                airfoils.append(m.group(1))
    else:
        airfoils = re.findall(r'href=["\']/airfoil/details\?airfoil=([^&"\' >]+)["\']', html)

    return list(dict.fromkeys(airfoils))


def get_uiuc_profiles():
    """Scrape all .dat file relative URLs from UIUC database."""
    html = fetch_url(UIUC_INDEX_URL)
    if not html:
        return []

    if HAS_BS4:
        soup = BeautifulSoup(html, 'html.parser')
        links = soup.find_all('a', href=re.compile(r'\.dat$', re.IGNORECASE))
        rel_links = [a['href'] for a in links if 'href' in a.attrs]
    else:
        rel_links = re.findall(r'href=["\']([^"\']+\.dat)["\']', html, re.IGNORECASE)

    unique_links = list(dict.fromkeys(rel_links))
    profiles = []
    for rel in unique_links:
        full_url = urllib.parse.urljoin(UIUC_BASE_URL, rel)
        filename = os.path.basename(rel)
        profile_id = os.path.splitext(filename)[0]
        profiles.append({
            'profile_id': profile_id,
            'source': 'uiuc',
            'url': full_url,
            'filename': filename
        })
    return profiles


# ==============================================================================
# Graph (Polar) Parsing & Management
# ==============================================================================
def get_polar_keys_for_airfoil(airfoil_id):
    """Get all polar detail keys for a given airfoil."""
    details_url = f"{AIRFOILTOOLS_BASE}/airfoil/details?airfoil={airfoil_id}"
    html = fetch_url(details_url)
    if not html:
        return []

    if HAS_BS4:
        soup = BeautifulSoup(html, 'html.parser')
        polar_links = soup.find_all('a', href=re.compile(r'/polar/details\?polar='))
        keys = []
        for a in polar_links:
            m = re.search(r'polar=([^&"\' >]+)', a['href'])
            if m and m.group(1) not in keys:
                keys.append(m.group(1))
        return keys
    else:
        raw_keys = re.findall(r'href=["\']/polar/details\?polar=([^&"\' >]+)["\']', html)
        return list(dict.fromkeys(raw_keys))


def parse_polar_csv(raw_csv_text, airfoil_id, polar_key):
    """Parse raw polar CSV text into structured dictionary rows."""
    lines = raw_csv_text.strip().splitlines()
    meta = {}
    data_start_idx = -1

    for idx, line in enumerate(lines):
        if line.startswith("Alpha,Cl,Cd"):
            data_start_idx = idx
            break
        parts = line.split(',')
        if len(parts) >= 2:
            meta[parts[0].strip()] = parts[1].strip()

    if data_start_idx == -1:
        return []

    reader = csv.DictReader(lines[data_start_idx:])
    parsed_rows = []
    for r in reader:
        row = {
            'Airfoil': meta.get('Airfoil', airfoil_id),
            'Polar_Key': meta.get('Polar key', polar_key),
            'Reynolds_Number': meta.get('Reynolds number', ''),
            'Ncrit': meta.get('Ncrit', ''),
            'Mach': meta.get('Mach', ''),
            'Max_Cl_Cd': meta.get('Max Cl/Cd', ''),
            'Max_Cl_Cd_Alpha': meta.get('Max Cl/Cd alpha', ''),
            'Alpha': r.get('Alpha', ''),
            'Cl': r.get('Cl', ''),
            'Cd': r.get('Cd', ''),
            'Cdp': r.get('Cdp', ''),
            'Cm': r.get('Cm', ''),
            'Top_Xtr': r.get('Top_Xtr', ''),
            'Bot_Xtr': r.get('Bot_Xtr', '')
        }
        parsed_rows.append(row)
    return parsed_rows


def get_existing_airfoils_in_csv(filepath):
    """Scan existing CSV file and return set of stored airfoil IDs."""
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return set()

    existing = set()
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                af = row.get('Airfoil')
                if af:
                    existing.add(af)
    except Exception:
        pass
    return existing


def append_graph_rows(filepath, rows):
    """Thread-safe append of graph rows to consolidated CSV."""
    if not rows:
        return

    with graph_lock:
        file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=GRAPH_FIELDNAMES)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)


def scrape_airfoil_graphs(airfoil_id, output_file, existing_airfoils, force=False):
    """Scrape all polar curves for an airfoil and append to single CSV file."""
    if not force and airfoil_id in existing_airfoils:
        return {'airfoil_id': airfoil_id, 'status': 'skipped', 'rows': 0}

    polar_keys = get_polar_keys_for_airfoil(airfoil_id)
    if not polar_keys:
        return {'airfoil_id': airfoil_id, 'status': 'no_polars', 'rows': 0}

    all_rows = []
    for pkey in polar_keys:
        csv_url = f"{AIRFOILTOOLS_BASE}/polar/csv?polar={pkey}"
        raw_csv = fetch_url(csv_url)
        if raw_csv:
            rows = parse_polar_csv(raw_csv, airfoil_id, pkey)
            all_rows.extend(rows)

    if not all_rows:
        return {'airfoil_id': airfoil_id, 'status': 'empty_polars', 'rows': 0}

    append_graph_rows(output_file, all_rows)
    return {'airfoil_id': airfoil_id, 'status': 'success', 'rows': len(all_rows)}


# ==============================================================================
# ==============================================================================
# Profile (Coordinates) Parsing & Management
# ==============================================================================
def parse_selig_coords(raw_dat_text):
    """Parse Selig format dat text into (title, list of [x, y] coordinate pairs)."""
    lines = [line.strip() for line in raw_dat_text.strip().splitlines() if line.strip()]
    if not lines:
        return "", []

    title = lines[0]
    coord_lines = lines[1:]

    coords = []
    for line in coord_lines:
        parts = line.split()
        if len(parts) >= 2:
            try:
                x = float(parts[0])
                y = float(parts[1])
                coords.append([x, y])
            except ValueError:
                continue
    return title, coords


def load_existing_profiles(filepath):
    """Load existing profiles dictionary from JSON file."""
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_profiles_json(filepath, profiles_dict):
    """Save profile dictionary to JSON file with clean, compact [x, y] pairs."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    raw = json.dumps(profiles_dict, indent=2, ensure_ascii=False)
    compact = re.sub(r'\[\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?),\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\]', r'[\1, \2]', raw)
    with profile_lock:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(compact)


def scrape_airfoil_profile(item, profiles_dict, existing_keys, force=False):
    """Scrape coordinates for a profile directly as [x, y] pairs."""
    profile_id = item['profile_id']
    url = item['url']

    if not force and profile_id in existing_keys:
        return {'profile_id': profile_id, 'status': 'skipped', 'points': 0}

    raw_text = fetch_url(url)
    if not raw_text:
        return {'profile_id': profile_id, 'status': 'failed', 'points': 0}

    title, coords = parse_selig_coords(raw_text)
    if not coords:
        return {'profile_id': profile_id, 'status': 'no_coords', 'points': 0}

    with profile_lock:
        profiles_dict[profile_id] = {
            'name': title,
            'coordinates': coords
        }

    return {'profile_id': profile_id, 'status': 'success', 'points': len(coords)}


# ==============================================================================
# Folder Fast Merging Utilities
# ==============================================================================
def merge_existing_folders(graphs_file=DEFAULT_GRAPHS_FILE, profiles_file=DEFAULT_PROFILES_FILE):
    """Merge any existing individual files from legacy folders into the single consolidated files."""
    import glob

    legacy_graphs_dir = os.path.join(DATA_DIR, "airofoil-graph")
    legacy_profiles_dir = os.path.join(DATA_DIR, "airofoil-profiles")

    # 1. Merge Graphs
    if os.path.exists(legacy_graphs_dir):
        csv_files = sorted(glob.glob(os.path.join(legacy_graphs_dir, "*.csv")))
        print(f"[+] Merging {len(csv_files)} existing graph CSV files into {os.path.basename(graphs_file)}...")
        existing_af = get_existing_airfoils_in_csv(graphs_file)
        
        file_exists = os.path.exists(graphs_file) and os.path.getsize(graphs_file) > 0
        os.makedirs(os.path.dirname(graphs_file), exist_ok=True)
        
        merged_graphs = 0
        total_rows = 0
        with open(graphs_file, 'a', newline='', encoding='utf-8') as out_f:
            writer = csv.DictWriter(out_f, fieldnames=GRAPH_FIELDNAMES)
            if not file_exists:
                writer.writeheader()
            
            for fpath in csv_files:
                af_id = os.path.splitext(os.path.basename(fpath))[0]
                if af_id in existing_af:
                    continue
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as in_f:
                        r_reader = csv.DictReader(in_f)
                        rows = [{k: r.get(k, '') for k in GRAPH_FIELDNAMES} for r in r_reader]
                        if rows:
                            writer.writerows(rows)
                            total_rows += len(rows)
                            merged_graphs += 1
                            existing_af.add(af_id)
                except Exception:
                    pass
        print(f"  -> Added {merged_graphs} airfoils ({total_rows} rows) to {graphs_file}")

    # 2. Merge Profiles
    if os.path.exists(legacy_profiles_dir):
        dat_files = sorted(glob.glob(os.path.join(legacy_profiles_dir, "*.dat")))
        print(f"[+] Merging {len(dat_files)} existing profile .dat files into {os.path.basename(profiles_file)}...")
        profiles_dict = {}
        merged_profiles = 0
        
        for fpath in dat_files:
            pid = os.path.splitext(os.path.basename(fpath))[0]
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    raw_text = f.read()
                title, coords = parse_selig_coords(raw_text)
                if coords:
                    profiles_dict[pid] = {
                        'name': title,
                        'coordinates': coords
                    }
                    merged_profiles += 1
            except Exception:
                pass
        
        save_profiles_json(profiles_file, profiles_dict)
        print(f"  -> Saved {merged_profiles} clean coordinate profiles to {profiles_file}")


# ==============================================================================
# Main Orchestration Workflow
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Unified Airfoil Scraper (Graphs & Profiles into Single Files)"
    )
    parser.add_argument(
        '--type', choices=['both', 'graphs', 'profiles'], default='both',
        help="What data to scrape: 'both' (default), 'graphs', or 'profiles'."
    )
    parser.add_argument(
        '-a', '--airfoil', type=str, help="Scrape a specific airfoil ID (e.g., 'a18-il')."
    )
    parser.add_argument(
        '-w', '--workers', type=int, default=12,
        help="Number of concurrent worker threads (default: 12)."
    )
    parser.add_argument(
        '-f', '--force', action='store_true',
        help="Force overwrite existing data."
    )
    parser.add_argument(
        '-l', '--limit', type=int, default=None,
        help="Limit the number of airfoils to scrape."
    )
    parser.add_argument(
        '--graphs-file', type=str, default=DEFAULT_GRAPHS_FILE,
        help=f"Consolidated CSV path for graph polars (default: {DEFAULT_GRAPHS_FILE})."
    )
    parser.add_argument(
        '--profiles-file', type=str, default=DEFAULT_PROFILES_FILE,
        help=f"Consolidated JSON path for profiles (default: {DEFAULT_PROFILES_FILE})."
    )
    parser.add_argument(
        '--merge-existing', action='store_true',
        help="Merge legacy folder CSVs and .dat files into the single files."
    )

    args = parser.parse_args()
    graphs_file = os.path.abspath(args.graphs_file)
    profiles_file = os.path.abspath(args.profiles_file)

    print("==================================================")
    print("           UNIFIED AIRFOIL DATA SCRAPER           ")
    print("==================================================")
    print(f"Graphs Output File:   {graphs_file}")
    print(f"Profiles Output File: {profiles_file}")
    print(f"Scrape Mode:          {args.type.upper()}")

    if args.merge_existing:
        merge_existing_folders(graphs_file, profiles_file)
        return

    # 1. Discover Airfoils
    if args.airfoil:
        airfoil_ids = [args.airfoil]
    else:
        print("[+] Fetching master list of airfoils from AirfoilTools...")
        airfoil_ids = get_airfoiltools_airfoils()
        print(f"[+] Found {len(airfoil_ids)} unique airfoils.")

    if args.limit and args.limit > 0:
        airfoil_ids = airfoil_ids[:args.limit]
        print(f"[+] Limited to first {args.limit} airfoils.")

    if not airfoil_ids:
        print("[-] No airfoils found to process.")
        sys.exit(1)

    start_time = time.time()

    # =========================================================================
    # Task A: Scrape Graphs
    # =========================================================================
    if args.type in ['both', 'graphs']:
        print("\n--------------------------------------------------")
        print(">>> SCRAPING AERODYNAMIC GRAPH POLARS...")
        print("--------------------------------------------------")
        
        if args.force and os.path.exists(graphs_file):
            with open(graphs_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=GRAPH_FIELDNAMES)
                writer.writeheader()
        
        existing_graphs = get_existing_airfoils_in_csv(graphs_file) if not args.force else set()
        if existing_graphs:
            print(f"[+] Skipping {len(existing_graphs)} already scraped graphs.")

        g_completed = 0
        g_success = 0
        g_rows = 0

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_af = {
                executor.submit(scrape_airfoil_graphs, af, graphs_file, existing_graphs, args.force): af
                for af in airfoil_ids
            }
            for future in as_completed(future_to_af):
                af = future_to_af[future]
                g_completed += 1
                try:
                    res = future.result()
                    if res['status'] == 'success':
                        g_success += 1
                        g_rows += res['rows']
                        print(f"[Graphs {g_completed}/{len(airfoil_ids)}] SUCCESS: {af} -> +{res['rows']} rows")
                    elif res['status'] == 'skipped':
                        print(f"[Graphs {g_completed}/{len(airfoil_ids)}] SKIPPED: {af}")
                    else:
                        print(f"[Graphs {g_completed}/{len(airfoil_ids)}] NO DATA: {af}")
                except Exception as e:
                    print(f"[Graphs {g_completed}/{len(airfoil_ids)}] ERROR: {af} -> {e}")

        print(f"[+] Graphs complete: {g_success} new airfoils, {g_rows} total rows appended.")

    # =========================================================================
    # Task B: Scrape Profiles
    # =========================================================================
    if args.type in ['both', 'profiles']:
        print("\n--------------------------------------------------")
        print(">>> SCRAPING PROFILE COORDINATES (.DAT)...")
        print("--------------------------------------------------")

        profiles_dict = load_existing_profiles(profiles_file) if not args.force else {}
        existing_profiles = set(profiles_dict.keys())
        if existing_profiles:
            print(f"[+] Skipping {len(existing_profiles)} already scraped profiles.")

        profile_items = []
        for af in airfoil_ids:
            profile_items.append({
                'profile_id': af,
                'source': 'airfoiltools',
                'url': f"{AIRFOILTOOLS_BASE}/airfoil/seligdatfile?airfoil={af}"
            })

        p_completed = 0
        p_success = 0
        p_points = 0

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_p = {
                executor.submit(scrape_airfoil_profile, item, profiles_dict, existing_profiles, args.force): item['profile_id']
                for item in profile_items
            }
            for future in as_completed(future_to_p):
                pid = future_to_p[future]
                p_completed += 1
                try:
                    res = future.result()
                    if res['status'] == 'success':
                        p_success += 1
                        p_points += res['points']
                        print(f"[Profiles {p_completed}/{len(profile_items)}] SUCCESS: {pid} -> {res['points']} pts")
                    elif res['status'] == 'skipped':
                        print(f"[Profiles {p_completed}/{len(profile_items)}] SKIPPED: {pid}")
                    else:
                        print(f"[Profiles {p_completed}/{len(profile_items)}] NO DATA: {pid}")
                except Exception as e:
                    print(f"[Profiles {p_completed}/{len(profile_items)}] ERROR: {pid} -> {e}")

        if p_success > 0 or args.force:
            print("[+] Writing updated profiles to JSON file...")
            save_profiles_json(profiles_file, profiles_dict)

        print(f"[+] Profiles complete: {p_success} new profiles saved.")

    elapsed = time.time() - start_time
    print("\n==================================================")
    print("                 SCRAPING SUMMARY                 ")
    print("==================================================")
    print(f"Total Time Elapsed: {elapsed:.2f} seconds")
    if os.path.exists(graphs_file):
        print(f"Consolidated Graphs:   {graphs_file} ({os.path.getsize(graphs_file)/1024/1024:.2f} MB)")
    if os.path.exists(profiles_file):
        print(f"Consolidated Profiles: {profiles_file} ({os.path.getsize(profiles_file)/1024/1024:.2f} MB)")
    print("==================================================")


if __name__ == "__main__":
    main()
