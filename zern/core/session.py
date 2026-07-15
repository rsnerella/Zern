import requests
from time import sleep
import random
import pyotp
import uuid

class Session:
    TYPE_GET = requests.get
    TYPE_POST = requests.post

    def __init__(self,user_name, password, totp_key) -> None:
        self.user_name = user_name
        self._private__password = password
        self.totp_key = totp_key
        self.cookies = None
        self._app_uuid = str(uuid.uuid4())

        # Headers for initial page load (login)
        # Note: Don't set accept-encoding manually - let requests handle it automatically
        self._login_headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'accept-language': 'en-GB,en;q=0.5',
            'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        }

        # Headers for API requests (orders, positions, etc.)
        # Note: Don't set accept-encoding manually - let requests handle it automatically
        self.headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-GB,en;q=0.5',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://kite.zerodha.com',
            'referer': 'https://kite.zerodha.com/',
            'sec-ch-ua': '"Chromium";v="143", "Not A(Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Linux"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'sec-gpc': '1',
            'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'x-kite-app-uuid': self._app_uuid,
            'x-kite-userid': user_name,
            'x-kite-version': '3.0.0',
        }

        self._base_url = 'https://kite.zerodha.com'
        self._login_url = 'https://kite.zerodha.com/api/login'
        self._totp_url = 'https://kite.zerodha.com/api/twofa'
        self._enc_token = None
        self.public_token = None
        self.start()
        self._get_instruments()

    
    def start(self):
        # Use a session to persist cookies across requests
        self._session = requests.Session()

        # Step 1: Get initial page to collect Cloudflare cookies
        resp = self._session.get(self._base_url, headers=self._login_headers)
        sleep(random.random() + 0.5)

        print('login: authenticating...')
        # Step 2: Login with username/password
        login_data = {'user_id': self.user_name, 'password': self._private__password}
        resp = self._session.post(self._login_url, headers=self._login_headers, data=login_data)

        if resp.status_code == 200:
            request_id = resp.json()['data']['request_id']
        else:
            try:
                obj = resp.json()
            except:
                raise Exception(f'error status {resp.status_code}: issue in the authentication code')
            raise Exception('{}: {}'.format(obj['status'], obj['message']))

        sleep(random.random() + 0.5)
        print('auto TOTP: verifying...')

        # Step 3: 2FA with TOTP
        totp_data = {
            'user_id': self.user_name,
            'request_id': request_id,
            'twofa_value': pyotp.TOTP(self.totp_key).now(),
            'twofa_type': 'totp',
            'skip_session': ''
        }
        response = self._session.post(self._totp_url, headers=self._login_headers, data=totp_data)

        # Extract tokens from cookies
        self.cookies = self._session.cookies
        self._enc_token = self.cookies.get("enctoken")
        self.public_token = self.cookies.get("public_token")

        # Set authorization header for API requests
        self.headers['authorization'] = 'enctoken {}'.format(self._enc_token)

        # Debug: print cookies
        print(f'Cookies obtained: {list(self.cookies.keys())}')

        done = True
        try:
            success = response.json()['status']
        except:
            done = False

        if done:
            print('Authentication: {}'.format(success))
        else:
            print('Authentication: failed')
    
    def request(self, url, method=TYPE_GET, cookies=True, headers=True, data=None, params=None):
        # Use the session to maintain cookies (including Cloudflare cookies)
        if method == requests.get:
            response = self._session.get(
                url,
                headers=self.headers if headers else None,
                params=params
            )
        elif method == requests.post:
            response = self._session.post(
                url,
                headers=self.headers if headers else None,
                data=data,
                params=params
            )
        else:
            response = method(
                url,
                cookies=self.cookies if cookies else None,
                headers=self.headers if headers else None,
                data=data,
                params=params
            )
        return response.json()

    def _get_instruments(self):
        # Zerodha's own instrument dump (public CSV, no third party). Replaces the old Sensibull oxide cache,
        # which now 401s behind an "insights access token". Builds the SAME nested schema trader.py reads,
        # with authoritative tradingsymbols straight from the exchange.
        import csv, io
        print('getting instrument tokens')
        EXCHANGES = ['NFO', 'BFO', 'NSE', 'BSE']
        INDEX_MAP = {'NIFTY 50': 'NIFTY', 'NIFTY BANK': 'BANKNIFTY', 'NIFTY FIN SERVICE': 'FINNIFTY',
                     'NIFTY MID SELECT': 'MIDCPNIFTY', 'NIFTY NEXT 50': 'NIFTYNXT50',
                     'SENSEX': 'SENSEX', 'BANKEX': 'BANKEX'}
        instruments = {'derivatives': {}, 'underlyer_list': {
            'NSE': {'NSE': {'EQ': {}}, 'NSE-INDICES': {'EQ': {}}},
            'BSE': {'BSE': {'EQ': {}}, 'BSE-INDICES': {'EQ': {}}}}}
        details = {}
        for exch in EXCHANGES:
            try:
                txt = requests.get('https://api.kite.trade/instruments/{}'.format(exch), timeout=60).text
            except Exception as e:
                print('  instruments fetch failed for {}: {}'.format(exch, e))
                continue
            for row in csv.DictReader(io.StringIO(txt)):
                try:
                    tok = int(row['instrument_token'])
                except (KeyError, ValueError):
                    continue
                ts, name, itype = row['tradingsymbol'], row['name'], row['instrument_type']
                seg, expiry, exchange = row['segment'], row['expiry'], row['exchange']
                entry = {'tradingsymbol': ts, 'instrument_token': tok}
                details[str(tok)] = {'tradingsymbol': ts, 'name': name, 'expiry': expiry, 'strike': row['strike'],
                                     'instrument_type': itype, 'lot_size': row['lot_size'],
                                     'tick_size': row['tick_size'], 'segment': seg, 'exchange': exchange}
                if itype in ('CE', 'PE'):
                    node = instruments['derivatives'].setdefault(name, {'derivatives': {}})['derivatives'].setdefault(expiry, {'options': {}})
                    try:
                        skey = '{}.0'.format(int(float(row['strike'])))
                    except ValueError:
                        continue
                    node['options'].setdefault(skey, {})[itype] = entry
                elif itype == 'FUT':
                    node = instruments['derivatives'].setdefault(name, {'derivatives': {}})['derivatives'].setdefault(expiry, {'options': {}})
                    node['tradingsymbol'], node['instrument_token'] = ts, tok
                elif seg == 'INDICES':
                    exbk = 'NSE' if exchange == 'NSE' else 'BSE'
                    idx = instruments['underlyer_list'][exbk][exbk + '-INDICES']['EQ']
                    idx[INDEX_MAP.get(name, name.replace(' ', '').upper())] = entry
                    idx[name.upper()] = entry
                elif itype == 'EQ':
                    exbk = 'NSE' if exchange == 'NSE' else 'BSE'
                    instruments['underlyer_list'][exbk][exbk]['EQ'][ts.upper()] = entry
        # order each derivative's expiries nearest-first (get_expiries()[0] must be the front contract)
        for d in instruments['derivatives'].values():
            d['derivatives'] = dict(sorted(d['derivatives'].items(), key=lambda kv: kv[0]))
        if 'NIFTY' not in instruments['derivatives']:
            raise Exception('Failed to get instruments')
        self.instruments = instruments
        self.ins_details_cache = details
        