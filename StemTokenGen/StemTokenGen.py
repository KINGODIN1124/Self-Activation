import os
import sys
import gevent.monkey
gevent.monkey.patch_all()

import json, base64, time, tempfile, shutil
import subprocess
from pathlib import Path
from steam.client import SteamClient
from steam.enums import EResult

BASE = Path(__file__).parent
DATA = BASE / "data"
MAUTH = DATA / "cachedlogins"
COMPLETED_DIR = BASE / "completed_configs"
UBI_DIR = BASE / "UbisoftBot"

for d in (DATA, MAUTH, COMPLETED_DIR, UBI_DIR):
    d.mkdir(parents=True, exist_ok=True)

def _rj(p, default):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except: return default

def _wj(p, data):
    fd, temp_path = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".tmp")
    with os.fdopen(fd, "w", encoding='utf-8') as f: 
        json.dump(data, f, indent=2)
    os.replace(temp_path, p)

STEAM_STATE_FILE = DATA / "steam_state.json"
ACCOUNTS_FILE = DATA / "accounts.json"
PENDING_FILE = DATA / "pending_requests.json"
GUARDS_FILE = DATA / "guards.json"

UBI_ACCOUNTS_FILE = DATA / "ubi_accounts.json"
UBI_PENDING_FILE = DATA / "pending_ubi.json"

if not ACCOUNTS_FILE.exists(): _wj(ACCOUNTS_FILE, [])
if not GUARDS_FILE.exists(): _wj(GUARDS_FILE, {})
if not UBI_ACCOUNTS_FILE.exists(): _wj(UBI_ACCOUNTS_FILE, {})

clients = {}
states = {}

def sync_state():
    _wj(STEAM_STATE_FILE, states)

def _do_login(u, p, code=None):
    if u not in clients:
        clients[u] = SteamClient()
        clients[u].set_credential_location(str(MAUTH))
    
    c = clients[u]
    if c.logged_on and not code: return 
    
    kwargs = {"username": u, "password": p}
    if code:
        prev_domain = states.get(u, {}).get("guard_domain")
        if prev_domain == "email": kwargs["auth_code"] = code
        else: kwargs["two_factor_code"] = code

    print(f"[Steam Fleet] Logging in {u}...")
    res = c.login(**kwargs)

    if res == EResult.OK:
        states[u] = {"logged_in": True, "steam_id": str(c.steam_id), "guard_needed": False, "guard_domain": None}
        print(f"[Steam Fleet] ✅ Logged in: {u}")
    elif res == EResult.AccountLoginDeniedNeedTwoFactor:
        states[u] = {"logged_in": False, "guard_needed": True, "guard_domain": "2FA"}
        print(f"[Steam Fleet] 🔐 2FA needed for {u}")
    elif res == EResult.AccountLogonDenied:
        states[u] = {"logged_in": False, "guard_needed": True, "guard_domain": "email"}
        print(f"[Steam Fleet] 🔐 Email guard needed for {u}")
    else:
        states[u] = {"logged_in": False, "guard_needed": False, "error": str(res)}
        print(f"[Steam Fleet] ❌ Failed to log in {u}: {res}")
    sync_state()

def _get_ticket(c: SteamClient, app_id: int):
    try:
        c.games_played([app_id])
        time.sleep(2)
        response = c.get_encrypted_app_ticket(app_id=app_id, userdata=b'')
        if not response or response.eresult != EResult.OK: return {"ok": False, "error": "Failed ticket."}
        t_obj = response.encrypted_app_ticket
        if hasattr(t_obj, "SerializeToString"): binary = t_obj.SerializeToString()
        elif hasattr(t_obj, "data"): binary = t_obj.data
        else: return {"ok": False, "error": "Unknown format."}
        return {"ok": True, "ticket": base64.b64encode(binary).decode()}
    except Exception as e: return {"ok": False, "error": str(e)}
    finally: c.games_played([])

def write_config_user(account_name, account_steamid, ticket, out_path):
    content = f"[user::general]\naccount_steamid={account_steamid}\naccount_name={account_name}\nticket={ticket}\n"
    with open(out_path, "w", encoding="utf-8") as f: f.write(content)

print("[System] Dual-Engine Fleet Manager Online. Awaiting requests...")

while True:
    accs = _rj(ACCOUNTS_FILE, [])
    acc_dict = {a['username']: a['password'] for a in accs}
    for u, p in acc_dict.items():
        if u not in clients: _do_login(u, p)

    if GUARDS_FILE.exists():
        guards = _rj(GUARDS_FILE, {})
        if guards:
            for u, code in list(guards.items()):
                if u in acc_dict: _do_login(u, acc_dict[u], code)
            _wj(GUARDS_FILE, {})

    if PENDING_FILE.exists():
        try:
            reqs = _rj(PENDING_FILE, [])
            if reqs:
                current_req = reqs.pop(0)
                app_id = int(current_req.get("app_id"))
                user_tag = current_req.get("user_tag", "UnknownUser")

                print(f"\n[Steam Engine] Compiling request for {user_tag} (AppID: {app_id})...")
                success = False

                for u, c in clients.items():
                    if not states.get(u, {}).get("logged_in") or not c.logged_on: continue
                    print(f"[Steam Engine] Checking license on {u}...")
                    resp = _get_ticket(c, app_id)

                    if resp.get("ok"):
                        safe_user_tag = "".join([ch for ch in user_tag if ch.isalpha() or ch.isdigit()]).rstrip()
                        
                        search_paths = [
                            BASE / "template",
                            BASE / "templates",
                            BASE.parent / "template",
                            BASE.parent / "templates"
                        ]
                        
                        with tempfile.TemporaryDirectory() as temp_dir:
                            temp_path = Path(temp_dir)
                            
                            template_found = False
                            for sp in search_paths:
                                z_path = sp / f"{app_id}.zip"
                                d_path = sp / str(app_id)
                                if z_path.exists():
                                    import zipfile
                                    with zipfile.ZipFile(z_path, 'r') as zip_ref:
                                        zip_ref.extractall(temp_path)
                                    template_found = True
                                    break
                                elif d_path.exists():
                                    shutil.copytree(d_path, temp_path, dirs_exist_ok=True)
                                    template_found = True
                                    break
                                
                            s_path = None
                            for p in temp_path.rglob("*"):
                                if p.is_dir() and p.name.lower() in ["steamsettings", "steam_settings"]:
                                    s_path = p
                                    break
                            
                            if s_path:
                                old_file = s_path / "configs.user"
                                if old_file.exists():
                                    try: old_file.unlink()
                                    except Exception as e: print(f"[Steam Engine] Could not delete old configs.user: {e}")
                                write_config_user(u, str(c.steam_id), resp['ticket'], s_path / "configs.user.ini")
                            else:
                                print(f"[Steam Engine] ⚠️ No steamsettings folder found for {app_id}. Not placing configs.user.")
                            
                            temp_zip = COMPLETED_DIR / f"configs_{safe_user_tag}_{app_id}_temp"
                            final_zip = COMPLETED_DIR / f"configs_{safe_user_tag}_{app_id}.zip"
                            shutil.make_archive(str(temp_zip), 'zip', temp_path)
                            os.replace(str(temp_zip) + ".zip", final_zip)
                            
                        print(f"[Steam Engine] ✅ Success! Archive secured for {user_tag}")
                        success = True
                        break
                if not success: print(f"[Steam Engine] ❌ Failure: No account owns AppID {app_id}")
                _wj(PENDING_FILE, reqs)
        except Exception as e: print(f"Steam Queue Error: {e}")

    if UBI_PENDING_FILE.exists():
        try:
            ureqs = _rj(UBI_PENDING_FILE, [])
            if ureqs:
                req = ureqs.pop(0)
                user_id = req["user_id"]
                t_val = req["t_val"]
                target_appid = req["app_id"]
                
                print(f"\n[Ubisoft Engine] Processing Denuvo Request for Discord ID {user_id} (Denuvo ID: {target_appid})...")
                
                ubi_db = _rj(UBI_ACCOUNTS_FILE, {})
                accounts = ubi_db.get(target_appid)
                
                success = False
                if accounts:
                    for acc in accounts:
                        print(f"[Ubisoft Engine] Generating with {acc['email']}...")
                        
                        exe_name = "DenuvoTicket.exe" if os.name == 'nt' else "./DenuvoTicket"
                        cmd = f'{exe_name} -username "{acc["email"]}" -password "{acc["password"]}" -denuvorequesttoken "{t_val}" -denuvoappid "{target_appid}"'
                        
                        try:
                            process = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(UBI_DIR))
                            process.communicate(input="\n", timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        
                        generated_token = UBI_DIR / "token.txt"
                        if generated_token.exists() and generated_token.stat().st_size > 0:
                            final_token_path = COMPLETED_DIR / f"ubi_token_{user_id}.txt"
                            shutil.move(str(generated_token), str(final_token_path))
                            print(f"[Ubisoft Engine] ✅ Success! Token locked for User {user_id}.")
                            success = True
                            break
                
                if not success:
                    fail_path = COMPLETED_DIR / f"ubi_token_{user_id}_failed.txt"
                    fail_path.touch()
                    print(f"[Ubisoft Engine] ❌ Generation Failed. Fleet out of licenses for this title.")

                _wj(UBI_PENDING_FILE, ureqs)
        except Exception as e: print(f"Ubisoft Queue Error: {e}")

    time.sleep(1)