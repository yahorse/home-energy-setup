#!/usr/bin/env python3
"""
ESB Smart Meter reader using Playwright.
Uses saved cookies from esb_manual_login.py — no login required on automated runs.
If no cookies exist, falls back to browser login (headless).
"""

import sys
import csv
import json
import datetime
import pytz
import logging
import configparser
import os
from copy import deepcopy
from playwright.sync_api import sync_playwright
from influxdb import InfluxDBClient

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger = logging.getLogger()
logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

COOKIE_FILE = os.path.join(os.path.dirname(__file__), 'esb_session.json')
COOKIE_MAX_AGE_HOURS = 720  # 30 days


def convert_to_unix(local_time):
    utc = pytz.utc
    epoch = datetime.datetime(1970, 1, 1, 0, 0, 0, tzinfo=pytz.UTC)
    local = pytz.timezone("Europe/Dublin")
    dt = datetime.datetime.strptime(local_time, '%d-%m-%Y %H:%M')
    adjusted = local.localize(dt)
    return int((adjusted.astimezone(utc) - epoch).total_seconds())


def load_cookies():
    if not os.path.exists(COOKIE_FILE):
        return None
    with open(COOKIE_FILE) as f:
        data = json.load(f)
    saved_at = datetime.datetime.fromisoformat(data['saved_at'])
    age_hours = (datetime.datetime.now() - saved_at).total_seconds() / 3600
    if age_hours > COOKIE_MAX_AGE_HOURS:
        logger.warning('Saved cookies are %.0f hours old — may be expired', age_hours)
    logger.info('Loaded %d cookies (saved %.1f hours ago)', len(data['cookies']), age_hours)
    return data['cookies']


def fetch_esb_csv(username, password, mprn):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            channel='chrome',
            args=['--disable-blink-features=AutomationControlled'],
            ignore_default_args=['--enable-automation'],
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            accept_downloads=True,
        )

        cookies = load_cookies()
        if cookies:
            logger.info('Using saved session cookies — skipping login')
            context.add_cookies(cookies)
        else:
            logger.info('No saved cookies. Attempting headless login...')
            page = context.new_page()
            page.goto('https://myaccount.esbnetworks.ie/', wait_until='networkidle', timeout=30000)
            try:
                page.wait_for_selector('#signInName', timeout=10000)
                page.fill('#signInName', username)
                page.fill('#password', password)
                page.click('#next')
                page.wait_for_url('https://myaccount.esbnetworks.ie/**', timeout=30000)
                logger.info('Headless login succeeded')
                # Save cookies for next time
                new_cookies = context.cookies()
                with open(COOKIE_FILE, 'w') as f:
                    json.dump({'cookies': new_cookies, 'saved_at': datetime.datetime.now().isoformat()}, f)
                logger.info('Saved %d cookies to %s', len(new_cookies), COOKIE_FILE)
            except Exception as e:
                browser.close()
                raise Exception('Login failed (headless). Run esb_manual_login.py first to save a session.') from e

        page = context.new_page()
        logger.info('Navigating to dashboard...')
        page.goto('https://myaccount.esbnetworks.ie/', wait_until='networkidle', timeout=30000)
        logger.info('Current URL: %s', page.url)

        if 'login' in page.url:
            if os.path.exists(COOKIE_FILE):
                os.rename(COOKIE_FILE, COOKIE_FILE + '.expired')
            browser.close()
            raise Exception('Session expired. Run esb_manual_login.py again.')

        # Wait for SPA to initialise and MSAL to silently refresh tokens
        logger.info('Waiting for SPA to initialise...')
        import time
        time.sleep(5)
        page.wait_for_load_state('networkidle', timeout=30000)

        logger.info('Downloading data for MPRN %s...', mprn)
        download_url = f'https://myaccount.esbnetworks.ie/DataHub/DownloadHdf?mprn={mprn}&startDate=2020-01-01'

        with page.expect_download(timeout=60000) as dl:
            page.evaluate(f'window.location = "{download_url}"')
        path = dl.value.path()

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        browser.close()
        return content


def main():
    config = configparser.ConfigParser()
    config.read(os.path.join(os.path.dirname(__file__), '.secrets'))

    username = config['esb']['USER']
    password = config['esb']['PASSWORD']
    mprn = config['esb']['MPRN']

    logger.info('=== ESB Playwright Runner ===')
    csv_content = fetch_esb_csv(username, password, mprn)

    lines = csv_content.splitlines()
    logger.info('%d lines in CSV', len(lines))
    if len(lines) < 2:
        logger.error('No data rows returned')
        sys.exit(1)
    logger.info('Header: %s', lines[0])
    logger.info('First: %s', lines[1])
    logger.info('Last:  %s', lines[-1])

    records = list(csv.DictReader(lines))
    logger.info('%d data rows parsed', len(records))

    client = InfluxDBClient(
        host=config['influx']['HOST'],
        port=8086,
        username=config['influx']['USER'],
        password=config['influx']['PASSWORD'],
        ssl=False,
        verify_ssl=False
    )
    client.switch_database(config['influx']['DB'])
    logger.info('Connected to InfluxDB at %s, DB: %s', config['influx']['HOST'], config['influx']['DB'])

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

    logger.info('Writing %d points to InfluxDB...', len(points))
    client.write_points(points, time_precision='s')
    logger.info('Done.')
    sys.exit(0)


if __name__ == '__main__':
    main()
