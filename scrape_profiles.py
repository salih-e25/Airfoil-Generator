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

UIUC_BASE_URL = "https://m-selig.ae.illinois.edu/ads/"
UIUC_INDEX_URL = "https://m-selig.ae.illinois.edu/ads/coord_database.html"
AIRFOILTOOLS_BASE = "http://airfoiltools.com"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, "airfoiltools-data", "airofoil-profiles")


HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}


def fetch_url(url, retries=3, backoff_factor=1, timeout=10):
    """Fetch URL text content with retries."""
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


def get_uiuc_profile_urls():
    """Scrape all .dat file relative URLs from UIUC database."""
    html = fetch_url(UIUC_INDEX_URL)
    if not html:
        print("[-] Failed to fetch UIUC coordinate database index.")
        return []

    if HAS_BS4:
        soup = BeautifulSoup(html, 'html.parser')
        links = soup.find_all('a', href=re.compile(r'\.dat$', re.IGNORECASE))
        rel_links = [a['href'] for a in links if 'href' in a.attrs]
    else:
        rel_links = re.findall(r'href=["\']([^"\']+\.dat)["\']', html, re.IGNORECASE)

    # Clean & normalize relative links
    unique_links = list(dict.fromkeys(rel_links))
    profiles = []
    for rel in unique_links:
        # e.g., 'coord/a18.dat' or 'coord_updates/bacnlf.dat' or 'a18.dat'
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


def get_airfoiltools_profile_urls():
    """Get list of airfoils from AirfoilTools with seligdatfile URLs."""
    search_url = f"{AIRFOILTOOLS_BASE}/search/airfoils"
    html = fetch_url(search_url)
    if not html:
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

    unique_airfoils = list(dict.fromkeys(airfoils))
    profiles = []
    for af_id in unique_airfoils:
        url = f"{AIRFOILTOOLS_BASE}/airfoil/seligdatfile?airfoil={af_id}"
        profiles.append({
            'profile_id': af_id,
            'source': 'airfoiltools',
            'url': url,
            'filename': f"{af_id}.dat"
        })
    return profiles


def parse_selig_coords(raw_dat_text, profile_id):
    """Parse Selig format dat text into (title, points_list)."""
    lines = [line.strip() for line in raw_dat_text.strip().splitlines() if line.strip()]
    if not lines:
        return "", []

    title = lines[0]
    coord_lines = lines[1:]

    points = []
    idx = 0
    for line in coord_lines:
        parts = line.split()
        if len(parts) >= 2:
            try:
                x = float(parts[0])
                y = float(parts[1])
                idx += 1
                points.append({
                    'Airfoil': profile_id,
                    'Point_Index': idx,
                    'X': x,
                    'Y': y
                })
            except ValueError:
                continue
    return title, points


def process_profile(item, output_dir, force=False):
    """Download and save profile coordinate data (.dat and .csv)."""
    profile_id = item['profile_id']
    url = item['url']

    dat_path = os.path.join(output_dir, f"{profile_id}.dat")
    csv_path = os.path.join(output_dir, f"{profile_id}.csv")

    if not force and os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        return {'profile_id': profile_id, 'status': 'skipped', 'points': 0}

    raw_text = fetch_url(url)
    if not raw_text:
        return {'profile_id': profile_id, 'status': 'failed', 'points': 0}

    title, points = parse_selig_coords(raw_text, profile_id)
    if not points:
        return {'profile_id': profile_id, 'status': 'no_coords', 'points': 0}

    # Write .dat file
    with open(dat_path, 'w', encoding='utf-8') as f:
        f.write(raw_text)

    # Write .csv file
    fieldnames = ['Airfoil', 'Point_Index', 'X', 'Y']
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(points)

    return {'profile_id': profile_id, 'status': 'success', 'points': len(points)}


def main():
    parser = argparse.ArgumentParser(
        description="Airfoil Profile Coordinate Scraper (UIUC & AirfoilTools)"
    )
    parser.add_argument(
        '-o', '--output-dir', type=str, default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to save profile coordinates (default: {DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        '-w', '--workers', type=int, default=15,
        help="Number of concurrent worker threads (default: 15)"
    )
    parser.add_argument(
        '-f', '--force', action='store_true',
        help="Force overwrite existing profile files"
    )
    parser.add_argument(
        '-l', '--limit', type=int, default=None,
        help="Limit number of profiles to scrape"
    )
    parser.add_argument(
        '--source', choices=['uiuc', 'airfoiltools', 'all'], default='all',
        help="Data source (default: all)"
    )

    args = parser.parse_args()
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    print("==================================================")
    print("      AIRFOIL PROFILE COORDINATE SCRAPER          ")
    print("==================================================")
    print(f"Output Directory: {output_dir}")

    items_map = {}

    if args.source in ['uiuc', 'all']:
        print("[+] Fetching UIUC Coordinate Database index...")
        uiuc_items = get_uiuc_profile_urls()
        print(f"[+] Found {len(uiuc_items)} profiles from UIUC database.")
        for item in uiuc_items:
            items_map[item['profile_id']] = item

    if args.source in ['airfoiltools', 'all']:
        print("[+] Fetching AirfoilTools profile list...")
        aft_items = get_airfoiltools_profile_urls()
        print(f"[+] Found {len(aft_items)} profiles from AirfoilTools.")
        for item in aft_items:
            if item['profile_id'] not in items_map:
                items_map[item['profile_id']] = item

    profiles = list(items_map.values())
    print(f"[+] Total combined unique profiles to process: {len(profiles)}")

    if args.limit and args.limit > 0:
        profiles = profiles[:args.limit]
        print(f"[+] Limited to first {args.limit} profiles.")

    if not profiles:
        print("[-] No profiles to process.")
        sys.exit(1)

    print(f"[+] Starting download for {len(profiles)} profile(s) with {args.workers} worker thread(s)...")
    start_time = time.time()
    completed = 0
    total = len(profiles)

    success_count = 0
    skipped_count = 0
    failed_count = 0
    total_points = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_profile = {
            executor.submit(process_profile, item, output_dir, args.force): item['profile_id']
            for item in profiles
        }

        for future in as_completed(future_to_profile):
            pid = future_to_profile[future]
            completed += 1
            try:
                res = future.result()
                status = res['status']
                if status == 'success':
                    success_count += 1
                    total_points += res['points']
                    print(f"[{completed}/{total}] SUCCESS: {pid} -> {res['points']} points saved (.dat & .csv)")
                elif status == 'skipped':
                    skipped_count += 1
                    print(f"[{completed}/{total}] SKIPPED: {pid} (already exists)")
                else:
                    failed_count += 1
                    print(f"[{completed}/{total}] NO DATA: {pid} (status: {status})")
            except Exception as e:
                failed_count += 1
                print(f"[{completed}/{total}] ERROR: {pid} -> {e}")

    elapsed = time.time() - start_time
    print("==================================================")
    print("            COORDINATE SCRAPING SUMMARY           ")
    print("==================================================")
    print(f"Total Profiles Processed: {total}")
    print(f"  - Downloaded: {success_count}")
    print(f"  - Skipped:    {skipped_count}")
    print(f"  - No Data:    {failed_count}")
    print(f"Total Coordinate Points Saved: {total_points}")
    print(f"Time Elapsed: {elapsed:.2f} seconds")
    print(f"Files saved in: {output_dir}")
    print("==================================================")


if __name__ == "__main__":
    main()
