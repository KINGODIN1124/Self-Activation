import os
import sys
import gevent.monkey
gevent.monkey.patch_all()

import asyncio
import json
import base64
import time
import tempfile
import shutil
import subprocess
import queue
import collections
from pathlib import Path
from steam.client import SteamClient
from steam.enums import EResult
from eventemitter.emitter import EventEmitter
from steam.client.builtins.friends import SteamFriendlist
from steam.client.gc import GameCoordinator

BASE = Path(__file__).parent
DATA = BASE / "data"
MAUTH = DATA / "cachedlogins"
COMPLETED_DIR = BASE / "completed_configs"
UBI_DIR = BASE / "UbisoftBot"

for d in (DATA, MAUTH, COMPLETED_DIR, UBI_DIR):
    d.mkdir(parents=True, exist_ok=True)

def _rj(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def _wj(p, data):
    fd, temp_path = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, p)

STEAM_STATE_FILE = DATA / "steam_state.json"
ACCOUNTS_FILE = DATA / "accounts.json"
PENDING_FILE = DATA / "pending_requests.json"
GUARDS_FILE = DATA / "guards.json"

UBI_ACCOUNTS_FILE = DATA / "ubi_accounts.json"
UBI_PENDING_FILE = DATA / "pending_ubi.json"

if not ACCOUNTS_FILE.exists():
    _wj(ACCOUNTS_FILE, [])
if not GUARDS_FILE.exists():
    _wj(GUARDS_FILE, {})
if not UBI_ACCOUNTS_FILE.exists():
    _wj(UBI_ACCOUNTS_FILE, {})

clients = {}
states = {}

def sync_state():
    _wj(STEAM_STATE_FILE, states)

def _init_sync_emitter(target):
    target._loop = None
    target._listeners = collections.defaultdict(list)
    target._once = collections.defaultdict(list)
    target._max_listeners = EventEmitter.DEFAULT_MAX_LISTENERS

def _sync_emit(target, event, *args):
    listeners = target._listeners[event][:]
    once_listeners = target._once[event][:]
    target._once[event] = []
    for listener in listeners + once_listeners:
        listener(*args)
    return target

def _safe_remove_all_listeners(target, event=None):
    if event is None:
        target._listeners = collections.defaultdict(list)
        target._once = collections.defaultdict(list)
    else:
        target._listeners.pop(event, None)
        target._once.pop(event, None)

_orig_friendlist_init = SteamFriendlist.__init__
def _patched_friendlist_init(self, client, logger_name="SteamFriendList"):
    _init_sync_emitter(self)
    _orig_friendlist_init(self, client, logger_name)

def _patched_friendlist_emit(self, event, *args):
    if event is not None:
        self._LOG.debug("Emit event: %s" % repr(event))
    return _sync_emit(self, event, *args)

_orig_gc_init = GameCoordinator.__init__
def _patched_gc_init(self, steam_client, app_id):
    _init_sync_emitter(self)
    _orig_gc_init(self, steam_client, app_id)

def _patched_gc_emit(self, event, *args):
    if event is not None:
        self._LOG.debug("Emit event: %s" % repr(event))
    return _sync_emit(self, event, *args)

SteamFriendlist.__init__ = _patched_friendlist_init
SteamFriendlist.emit = _patched_friendlist_emit
GameCoordinator.__init__ = _patched_gc_init
GameCoordinator.emit = _patched_gc_emit

class PatchedSteamClient(SteamClient):
    def __init__(self):
        _init_sync_emitter(self)
        super().__init__()

    def count_listeners(self, event):
        return self.count(event)

    def emit(self, event, *args):
        return _sync_emit(self, event, *args)

    def remove_all_listeners(self, event=None):
        _safe_remove_all_listeners(self, event)
        return self

    def wait_event(self, event, timeout=None, raises=False):
        result_queue = queue.Queue(maxsize=1)

        def handler(*args):
            try:
                result_queue.put_nowait(args)
            except queue.Full:
                pass

        self.once(event, handler)

        try:
            return result_queue.get(timeout=timeout)
        except queue.Empty:
            self.remove_listener(event, handler)
            if raises:
                raise TimeoutError(f"Timed out waiting for event: {event}")
            return None

def create_steam_client():
    return PatchedSteamClient()

def _do_login(u, p, code=None):
    if u not in clients:
        clients[u] = create_steam_client()
        clients[u].set_credential_location(str(MAUTH))

    c = clients[u]
    if c.logged_on and not code:
        return

    kwargs = {"username": u, "password": p}
    if code:
        prev_domain = states.get(u, {}).get("guard_domain")
        if prev_domain == "email":
            kwargs["auth_code"] = code
        else:
            kwargs["two_factor_code"] = code

    print(f"[STEAM FLEET] Establishing connection for worker account: {u}...")
    res = c.login(**kwargs)

    if res == EResult.OK:
        states[u] = {"logged_in": True, "steam_id": str(c.steam_id), "guard_needed": False, "guard_domain": None}
        print(f"[STEAM FLEET] -> Successfully logged in: {u}")
    elif res == EResult.AccountLoginDeniedNeedTwoFactor:
        states[u] = {"logged_in": False, "guard_needed": True, "guard_domain": "2FA"}
        print(f"[STEAM FLEET] -> Action Required: 2FA needed for {u}")
    elif res == EResult.AccountLogonDenied:
        states[u] = {"logged_in": False, "guard_needed": True, "guard_domain": "email"}
        print(f"[STEAM FLEET] -> Action Required: Email guard needed for {u}")
    else:
        states[u] = {"logged_in": False, "guard_needed": False, "error": str(res)}
        print(f"[STEAM FLEET] -> Connection Failed for {u}: {res}")
    sync_state()

def _get_ticket(c: SteamClient, app_id: int):
    try:
        c.games_played([app_id])
        time.sleep(2)
        response = c.get_encrypted_app_ticket(app_id=app_id, userdata=b"")
        if not response or response.eresult != EResult.OK:
            return {"ok": False, "error": "Failed ticket."}
        t_obj = response.encrypted_app_ticket
        if hasattr(t_obj, "SerializeToString"):
            binary = t_obj.SerializeToString()
        elif hasattr(t_obj, "data"):
            binary = t_obj.data
        else:
            return {"ok": False, "error": "Unknown format."}
        return {"ok": True, "ticket": base64.b64encode(binary).decode()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        c.games_played([])

def _get_owned_app_ids(c: SteamClient):
    try:
        c.wait_event("licenses", timeout=15)
        licenses = c.licenses
        if not licenses:
            time.sleep(3)
            licenses = c.licenses

        if not licenses:
            return set()

        pkg_ids = list(licenses.keys())
        info = c.get_product_info(packages=pkg_ids)
        owned_app_ids = set()

        if info and "packages" in info:
            for pkg_data in info["packages"].values():
                appids_data = pkg_data.get("appids", {})
                if isinstance(appids_data, dict):
                    for value in appids_data.values():
                        owned_app_ids.add(str(value))
                elif isinstance(appids_data, list):
                    for value in appids_data:
                        owned_app_ids.add(str(value))

        return owned_app_ids
    except Exception as exc:
        print(f"[STEAM ENGINE] Failed to fetch owned app list: {exc}")
        return set()

def write_config_user(account_name, account_steamid, ticket, out_path):
    content = f"[user::general]\naccount_steamid={account_steamid}\naccount_name={account_name}\nticket={ticket}\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

def process_steam_queue():
    if not PENDING_FILE.exists():
        return

    try:
        reqs = _rj(PENDING_FILE, [])
        if not reqs:
            return

        current_req = reqs.pop(0)
        app_id = int(current_req.get("app_id"))
        user_tag = current_req.get("user_tag", "UnknownUser")

        print("\n[STEAM ENGINE] ----------------------------------------")
        print("[STEAM ENGINE] New generation payload received.")
        print(f"[STEAM ENGINE] Target User: {user_tag}")
        print(f"[STEAM ENGINE] Target AppID: {app_id}")
        print("[STEAM ENGINE] Hunting fleet for valid license...")

        success = False
        ownership_seen = False

        for u, c in clients.items():
            if not states.get(u, {}).get("logged_in") or not c.logged_on:
                continue
            print(f"[STEAM ENGINE] -> Testing license on worker node: {u}...")
            owned_app_ids = _get_owned_app_ids(c)
            if str(app_id) not in owned_app_ids:
                print(f"[STEAM ENGINE] -> {u} does not list AppID {app_id} in owned packages.")
                continue

            ownership_seen = True
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
                            except Exception as e: print(f"[STEAM ENGINE] Could not delete old configs.user: {e}")
                        write_config_user(u, str(c.steam_id), resp["ticket"], s_path / "configs.user.ini")
                    else:
                        print(f"[STEAM ENGINE] ⚠️ No steamsettings folder found for {app_id}. Not placing configs.user.")

                    temp_zip = COMPLETED_DIR / f"configs_{safe_user_tag}_{app_id}_temp"
                    final_zip = COMPLETED_DIR / f"configs_{safe_user_tag}_{app_id}.zip"
                    shutil.make_archive(str(temp_zip), "zip", temp_path)
                    os.replace(str(temp_zip) + ".zip", final_zip)

                print(f"[STEAM ENGINE] Success! Archive secured for {user_tag}")
                success = True
                break
            else:
                print(f"[STEAM ENGINE] -> {u} owns AppID {app_id}, but ticket generation failed: {resp.get('error')}")

        if not success:
            if ownership_seen:
                print(f"[STEAM ENGINE] Failure: At least one account owns AppID {app_id}, but encrypted ticket generation failed.")
            else:
                print(f"[STEAM ENGINE] Failure: No worker account lists AppID {app_id} in owned packages.")

        _wj(PENDING_FILE, reqs)
    except Exception as e:
        print(f"Steam Queue Error: {e}")

def process_ubi_queue():
    if not UBI_PENDING_FILE.exists():
        return

    try:
        ureqs = _rj(UBI_PENDING_FILE, [])
        if not ureqs:
            return

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

                exe_name = "DenuvoTicket.exe" if os.name == "nt" else "./DenuvoTicket"
                cmd = f'{exe_name} -username "{acc["email"]}" -password "{acc["password"]}" -denuvorequesttoken "{t_val}" -denuvoappid "{target_appid}"'

                try:
                    process = subprocess.Popen(
                        cmd,
                        shell=True,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        cwd=str(UBI_DIR),
                    )
                    process.communicate(input="\n", timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()

                generated_token = UBI_DIR / "token.txt"
                if generated_token.exists() and generated_token.stat().st_size > 0:
                    final_token_path = COMPLETED_DIR / f"ubi_token_{user_id}.txt"
                    shutil.move(str(generated_token), str(final_token_path))
                    print(f"[Ubisoft Engine] Success! Token locked for User {user_id}.")
                    success = True
                    break

        if not success:
            fail_path = COMPLETED_DIR / f"ubi_token_{user_id}_failed.txt"
            fail_path.touch()
            print("[Ubisoft Engine] Generation Failed. Fleet out of licenses for this title.")

        _wj(UBI_PENDING_FILE, ureqs)
    except Exception as e:
        print(f"Ubisoft Queue Error: {e}")

def main():
    while True:
        accs = _rj(ACCOUNTS_FILE, [])
        acc_dict = {a["username"]: a["password"] for a in accs}
        for u, p in acc_dict.items():
            if u not in clients:
                _do_login(u, p)

        if GUARDS_FILE.exists():
            guards = _rj(GUARDS_FILE, {})
            if guards:
                for u, code in list(guards.items()):
                    if u in acc_dict:
                        _do_login(u, acc_dict[u], code)
                _wj(GUARDS_FILE, {})

        process_steam_queue()
        process_ubi_queue()
        time.sleep(1)

if __name__ == "__main__":
    main()
