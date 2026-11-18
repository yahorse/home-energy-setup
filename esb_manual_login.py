#!/usr/bin/env python3
"""
Run this once manually to solve CAPTCHA, download all meter data and write to InfluxDB.
Saves session cookies so subsequent runs skip login if the session is still valid.
"""

import csv
import json
import configparser
import sys
import datetime
import pytz
from copy import deepcopy
from playwright.sync_api import sync_playwright
from influxdb import InfluxDBClient

config = configparser.ConfigParser()
config.read('.secrets')

username = config['esb']['USER']
password = config['esb']['PASSWORD']
mprn = config['esb']['MPRN']


def convert_to_unix(local_time):
    utc = pytz.utc
    epoch = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)
    local = pytz.timezone("Europe/Dublin")
    dt = datetime.datetime.strptime(local_time, '%d-%m-%Y %H:%M')
    adjusted = local.localize(dt)
    return int((adjusted.astimezone(utc) - epoch).total_seconds())


print("Opening browser. Log in and solve CAPTCHA if prompted.")

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        slow_mo=200,
        channel='chrome',
        args=['--disable-blink-features=AutomationControlled'],
        ignore_default_args=['--enable-automation'],
    )
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        viewport={'width': 1280, 'height': 800},
        accept_downloads=True,
    )
    page = context.new_page()

    page.goto('https://myaccount.esbnetworks.ie/', wait_until='networkidle', timeout=30000)

    # Pre-fill credentials
    try:
        page.wait_for_selector('#signInName', timeout=10000)
        page.fill('#signInName', username)
        page.fill('#password', password)
        print("Credentials filled. Clicking sign in...")
        page.click('#next')
    except Exception as e:
        print(f"Could not auto-fill: {e}. Please log in manually in the browser.")

    print("Waiting for dashboard (you may need to solve CAPTCHA)...")
    page.wait_for_url('https://myaccount.esbnetworks.ie/**', timeout=120000)
    print("Login successful! URL:", page.url)

    # Wait for SPA + MSAL token exchange to fully complete
    page.wait_for_load_state('networkidle', timeout=30000)
    print("SPA ready.")

    # Save cookies
    cookies = context.cookies()
    with open('esb_session.json', 'w') as f:
        json.dump({'cookies': cookies, 'saved_at': datetime.datetime.now().isoformat()}, f, indent=2)
    print(f"Saved {len(cookies)} cookies to esb_session.json")

    # Intercept all requests to find the download mechanism
    requests_log = []
    def on_request(req):
        if 'download' in req.url.lower() or 'hdf' in req.url.lower() or 'datahub' in req.url.lower():
            requests_log.append({'url': req.url, 'method': req.method, 'headers': dict(req.headers)})

    page.on('request', on_request)

    # Navigate to DataHub page
    print("Navigating to DataHub...")
    page.goto('https://myaccount.esbnetworks.ie/DataHub', wait_until='networkidle', timeout=30000)
    print("DataHub URL:", page.url)
    page.wait_for_load_state('networkidle', timeout=15000)

    # Log page content to find download button
    content = page.content()
    import re
    links = re.findall(r'href=["\']([^"\']*(?:download|hdf|csv|HDF)[^"\']*)["\']', content, re.IGNORECASE)
    buttons = re.findall(r'<button[^>]*>([^<]*(?:download|export|hdf)[^<]*)</button>', content, re.IGNORECASE)
    print("Download links found:", links[:5])
    print("Download buttons found:", buttons[:5])
    print("Intercepted requests:", requests_log[:5])

    # Try clicking a download element if found
    try:
        dl_el = page.locator('a[href*="DownloadHdf"], a[href*="download"], button:has-text("Download")').first
        print("Found element:", dl_el.inner_text())
        with page.expect_download(timeout=60000) as dl:
            dl_el.click()
        path = dl.value.path()
    except Exception as e:
        print("Click approach failed:", e)
        # Fall back to direct navigation
        print(f"Trying direct download URL...")
        with page.expect_download(timeout=60000) as dl:
            page.evaluate(f'window.location = "https://myaccount.esbnetworks.ie/DataHub/DownloadHdf?mprn={mprn}&startDate=2020-01-01"')
        path = dl.value.path()
    with open(path, 'r', encoding='utf-8') as f:
        csv_content = f.read()

    browser.close()

lines = csv_content.splitlines()
print(f"Downloaded {len(lines)} rows")
if len(lines) < 2:
    print("No data rows — aborting.")
    sys.exit(1)
print(f"Header: {lines[0]}")
print(f"First:  {lines[1]}")
print(f"Last:   {lines[-1]}")

records = list(csv.DictReader(lines))
print(f"Parsed {len(records)} records")

client = InfluxDBClient(
    host=config['influx']['HOST'],
    port=8086,
    username=config['influx']['USER'],
    password=config['influx']['PASSWORD'],
    ssl=False,
    verify_ssl=False,
)
client.switch_database(config['influx']['DB'])
print(f"Connected to InfluxDB at {config['influx']['HOST']}, DB: {config['influx']['DB']}")

points = []
entry = {}
for record in records:
    entry['measurement'] = 'meter_reading'
    entry['tags'] = {
        'MPRN': record['MPRN'],
        'MeterSerialNumber': record['Meter Serial Number'],
        'ReadType': record['Read Type'],
    }
    entry['fields'] = {'value': float(record['Read Value'])}
    entry['time'] = convert_to_unix(record['Read Date and End Time'])
    points.append(deepcopy(entry))
    entry.clear()

print(f"Writing {len(points)} points to InfluxDB...")
client.write_points(points, time_precision='s')
print("Done.")
