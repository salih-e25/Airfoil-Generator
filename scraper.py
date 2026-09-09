import os
import sys
import re
import csv
import time
import argparse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

BASE_URL = "http://airfoiltools.com"
DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "airfoiltools-data", "airofoil-graph")
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}


def fetch_url(url, retries=3, backoff_factor=1, timeout=10):
    """Fetch URL content as string with retries."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            if attempt == retries - 1:
                return None
            time.sleep(backoff_factor * (2 ** attempt))
    return None


def get_all_airfoil_ids():
    """Scrape the list of all unique airfoil IDs from airfoiltools.com search page."""
    search_url = f"{BASE_URL}/search/airfoils"
    html = fetch_url(search_url)
    if not html:
        print("[-] Failed to fetch airfoil index page.")
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
        # Regex fallback if bs4 is unavailable
        airfoils = re.findall(r'href=["\']/airfoil/details\?airfoil=([^&"\' >]+)["\']', html)

    # Preserve order & uniqueness
    unique_airfoils = list(dict.fromkeys(airfoils))
    return unique_airfoils


def get_polar_keys_for_airfoil(airfoil_id):
    """Get all polar detail keys for a given airfoil."""
    details_url = f"{BASE_URL}/airfoil/details?airfoil={airfoil_id}"
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
    """Parse raw polar CSV content into structured dictionary rows."""
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


def scrape_airfoil(airfoil_id, output_dir, force=False):
    """
    Scrape all polar condition data for a single airfoil
    and save it as <airfoil_id>.csv in output_dir.
    """
    os.makedirs(output_dir, exist_ok=True)
    out_filepath = os.path.join(output_dir, f"{airfoil_id}.csv")

    if not force and os.path.exists(out_filepath) and os.path.getsize(out_filepath) > 0:
        return {'airfoil_id': airfoil_id, 'status': 'skipped', 'rows': 0, 'file': out_filepath}

    polar_keys = get_polar_keys_for_airfoil(airfoil_id)
    if not polar_keys:
        return {'airfoil_id': airfoil_id, 'status': 'no_polars', 'rows': 0, 'file': None}

    all_rows = []
    fieldnames = [
        'Airfoil', 'Polar_Key', 'Reynolds_Number', 'Ncrit', 'Mach',
        'Max_Cl_Cd', 'Max_Cl_Cd_Alpha', 'Alpha', 'Cl', 'Cd', 'Cdp', 'Cm',
        'Top_Xtr', 'Bot_Xtr'
    ]

    for pkey in polar_keys:
        csv_url = f"{BASE_URL}/polar/csv?polar={pkey}"
        raw_csv = fetch_url(csv_url)
        if raw_csv:
            rows = parse_polar_csv(raw_csv, airfoil_id, pkey)
            all_rows.extend(rows)

    if not all_rows:
        return {'airfoil_id': airfoil_id, 'status': 'empty_polars', 'rows': 0, 'file': None}

    with open(out_filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    return {'airfoil_id': airfoil_id, 'status': 'success', 'rows': len(all_rows), 'file': out_filepath}


def main():
    parser = argparse.ArgumentParser(
        description="AirfoilTools.com Data Scraper - Download airfoil profile polars to CSV."
    )
    parser.add_argument(
        '-a', '--airfoil', type=str, help="Scrape a specific airfoil ID (e.g., 'a18-il')."
    )
    parser.add_argument(
        '--all', action='store_true', help="Scrape all airfoils from AirfoilTools."
    )
    parser.add_argument(
        '-o', '--output-dir', type=str, default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save CSV files (default: {DEFAULT_OUTPUT_DIR})."
    )
    parser.add_argument(
        '-w', '--workers', type=int, default=10,
        help="Number of concurrent worker threads (default: 10)."
    )
    parser.add_argument(
        '-f', '--force', action='store_true',
        help="Force overwrite existing CSV files."
    )
    parser.add_argument(
        '-l', '--limit', type=int, default=None,
        help="Limit the number of airfoils to scrape."
    )

    args = parser.parse_args()
    output_dir = os.path.abspath(args.output_dir)

    print("==================================================")
    print("           AIRFOIL TOOLS SCRAPER                 ")
    print("==================================================")
    print(f"Output Directory: {output_dir}")

    # Case 1: Single specified airfoil
    if args.airfoil:
        airfoils = [args.airfoil]
    else:
        print("[+] Fetching list of airfoils from AirfoilTools...")
        airfoils = get_all_airfoil_ids()
        print(f"[+] Total airfoils found: {len(airfoils)}")

    if args.limit and args.limit > 0:
        airfoils = airfoils[:args.limit]
        print(f"[+] Limited to first {args.limit} airfoils.")

    if not airfoils:
        print("[-] No airfoils to process.")
        sys.exit(1)

    print(f"[+] Starting download for {len(airfoils)} airfoil(s) with {args.workers} worker thread(s)...")
    start_time = time.time()
    completed = 0
    total = len(airfoils)

    success_count = 0
    skipped_count = 0
    failed_count = 0
    total_rows = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_airfoil = {
            executor.submit(scrape_airfoil, af, output_dir, args.force): af
            for af in airfoils
        }

        for future in as_completed(future_to_airfoil):
            af = future_to_airfoil[future]
            completed += 1
            try:
                res = future.result()
                status = res['status']
                if status == 'success':
                    success_count += 1
                    total_rows += res['rows']
                    print(f"[{completed}/{total}] SUCCESS: {af} -> {res['rows']} rows saved")
                elif status == 'skipped':
                    skipped_count += 1
                    print(f"[{completed}/{total}] SKIPPED: {af} (already exists)")
                else:
                    failed_count += 1
                    print(f"[{completed}/{total}] NO DATA: {af} (status: {status})")
            except Exception as e:
                failed_count += 1
                print(f"[{completed}/{total}] ERROR: {af} -> {e}")

    elapsed = time.time() - start_time
    print("==================================================")
    print("                 SCRAPING SUMMARY                 ")
    print("==================================================")
    print(f"Total Airfoils Processed: {total}")
    print(f"  - Downloaded: {success_count}")
    print(f"  - Skipped:    {skipped_count}")
    print(f"  - No Data:    {failed_count}")
    print(f"Total Data Rows Saved: {total_rows}")
    print(f"Time Elapsed: {elapsed:.2f} seconds")
    print(f"All files saved in: {output_dir}")
    print("==================================================")


if __name__ == "__main__":
    main()
