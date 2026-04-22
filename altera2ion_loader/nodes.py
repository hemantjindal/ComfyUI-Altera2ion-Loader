"""
ALTERA2ION LoRA Loader - Licensed ComfyUI Node
Loads encrypted .a2enc LoRA files through the ALTERA2ION activation flow.
https://altera2ion.com
"""

import json
import os
import platform
import secrets
import tempfile
import time
import hashlib
import uuid
import urllib.request
import urllib.error
import webbrowser
from datetime import datetime

from cryptography.fernet import Fernet


NODE_DIR = os.path.dirname(__file__)
LEGACY_CONFIG_DIR = os.path.join(NODE_DIR, ".altera2ion")
USER_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".altera2ion", "comfyui")
CONFIG_DIR = (
    os.environ.get("ALTERA2ION_CONFIG_DIR")
    or (LEGACY_CONFIG_DIR if os.path.exists(os.path.join(LEGACY_CONFIG_DIR, "activation.json")) else USER_CONFIG_DIR)
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "activation.json")

API_BASE = os.environ.get("ALTERA2ION_API_BASE", "https://www.altera2ion.com/api").rstrip("/")
ACTIVATION_POLL_INTERVAL_SECONDS = 3
ACTIVATION_WAIT_SECONDS = 45
DECRYPT_KEY_OFFLINE_GRACE_HOURS = 24

PRODUCT_HINTS = {
    "exterior-adaptation": ("exterior", "adaptation"),
    "interior-adaptation": ("interior", "adaptation"),
    "dreamy": ("dreamy",),
    "re2form": ("re2form",),
}

PRODUCT_LORA_FILES = {
    "exterior-adaptation": "Exterior_Adaptation_LoRA.a2enc",
    "interior-adaptation": "Interior_Adaptation_LoRA.a2enc",
    "dreamy": "Dreamy_Style_LoRA.a2enc",
    "re2form": "Re2Form_LoRA.a2enc",
}


def now_timestamp():
    return time.time()


def parse_iso_timestamp(value):
    try:
        normalized = str(value or "").replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return 0


def get_machine_name():
    return (
        os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
        or platform.node()
        or "Unknown PC"
    )[:255]


def get_machine_id():
    raw = f"{uuid.getnode()}-{os.path.expanduser('~')}"
    return hashlib.sha256(raw.encode("utf8")).hexdigest()[:32]


def ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def get_comfy_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def get_lora_dir():
    return os.path.join(get_comfy_root(), "models", "loras")


def get_lora_cache_dir():
    return os.path.join(CONFIG_DIR, "lora-cache")


def sanitize_lora_name(lora_name):
    safe_name = os.path.basename(str(lora_name or "").replace("\\", "/"))

    if not safe_name.endswith(".a2enc"):
        raise RuntimeError("[ALTERA2ION] Encrypted LoRA file names must end with .a2enc.")

    return safe_name


def get_lora_options():
    lora_dir = get_lora_dir()
    enc_files = set(PRODUCT_LORA_FILES.values())

    if os.path.isdir(lora_dir):
        for file_name in os.listdir(lora_dir):
            if file_name.endswith(".a2enc"):
                enc_files.add(file_name)

    return sorted(enc_files)


def find_existing_lora_path(lora_name):
    safe_name = sanitize_lora_name(lora_name)
    candidates = [
        os.path.join(get_lora_dir(), safe_name),
        os.path.join(get_lora_cache_dir(), safe_name),
    ]

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    return None


def get_preferred_lora_download_path(lora_name):
    safe_name = sanitize_lora_name(lora_name)

    for directory in [get_lora_dir(), get_lora_cache_dir()]:
        test_handle = None
        test_path = None

        try:
            os.makedirs(directory, exist_ok=True)
            test_handle = tempfile.NamedTemporaryFile(
                "wb",
                dir=directory,
                prefix=".tmp-altera2ion-write-test-",
                delete=False,
            )
            test_path = test_handle.name
            test_handle.close()
            os.remove(test_path)
            return os.path.join(directory, safe_name)
        except OSError:
            if test_handle and not test_handle.closed:
                test_handle.close()
            if test_path and os.path.exists(test_path):
                try:
                    os.remove(test_path)
                except OSError:
                    pass
            continue

    raise RuntimeError("[ALTERA2ION] Unable to create a writable LoRA cache directory.")


def write_json_atomic(path, data):
    ensure_config_dir()
    handle = None
    temp_path = None

    try:
        handle = tempfile.NamedTemporaryFile(
            "w",
            dir=CONFIG_DIR,
            prefix=".tmp-",
            suffix=".json",
            delete=False,
            encoding="utf8",
        )
        temp_path = handle.name
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        os.replace(temp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if handle and not handle.closed:
            handle.close()
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}

    try:
        with open(CONFIG_FILE, "r", encoding="utf8") as handle:
            data = json.load(handle)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    data.pop("license_key", None)
    data.pop("machine_id_legacy", None)

    products = data.get("products")
    if not isinstance(products, dict):
        data["products"] = {}

    return data


def save_config(data):
    write_json_atomic(CONFIG_FILE, data)


def get_product_state(config, product_slug):
    products = config.setdefault("products", {})
    state = products.get(product_slug)

    if not isinstance(state, dict):
        state = {}
        products[product_slug] = state

    return state


def is_future_timestamp(value):
    try:
        return float(value) > now_timestamp()
    except (TypeError, ValueError):
        return False


def clear_product_state(state):
    for key in [
        "activation_token",
        "activation_token_expires_at",
        "decrypt_key",
        "decrypt_key_expires_at",
        "pending_request_id",
        "pending_request_expires_at",
        "pending_activation_url",
        "pending_browser_opened_at",
    ]:
        state.pop(key, None)


def has_valid_activation_token(state):
    token = state.get("activation_token")
    expires_at = state.get("activation_token_expires_at")

    if not token or not expires_at:
        return False

    return parse_iso_timestamp(expires_at) > now_timestamp()


def ensure_machine_state(config):
    machine_id = config.get("machine_id") or get_machine_id()
    machine_name = config.get("machine_name") or get_machine_name()
    activation_secret = config.get("activation_secret")

    if not activation_secret or len(str(activation_secret)) < 32:
        activation_secret = secrets.token_urlsafe(48)

    config["machine_id"] = machine_id
    config["machine_name"] = machine_name
    config["activation_secret"] = activation_secret

    return machine_id, machine_name, activation_secret


def infer_product_slug(lora_name):
    normalized = os.path.splitext(os.path.basename(lora_name))[0].lower().replace("_", "-")

    for product_slug, hints in PRODUCT_HINTS.items():
        if all(hint in normalized for hint in hints):
            return product_slug

    raise RuntimeError(
        "[ALTERA2ION] Unable to infer the product from this file name. "
        "Rename the file to include the product name or update PRODUCT_HINTS in the loader."
    )


def post_json(path, payload, timeout=15):
    body = json.dumps(payload).encode("utf8")
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ALTERA2ION-ComfyUI/2.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf8")
            data = json.loads(response_body) if response_body else {}
            return response.status, data, None
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf8")
        try:
            data = json.loads(body_text) if body_text else {}
        except Exception:
            data = {"error": body_text}
        message = data.get("error") or body_text or f"HTTP {error.code}"
        return error.code, data, message
    except Exception as error:
        return None, None, f"Connection error: {error}"


def request_activation(product_slug, machine_id, machine_name, activation_secret):
    status, data, error = post_json("/activations/request", {
        "product_slug": product_slug,
        "machine_id": machine_id,
        "machine_name": machine_name,
        "activation_secret": activation_secret,
    })

    if status == 200:
        return data, None

    return None, error or "Unable to create activation request."


def poll_activation(request_id, machine_id, activation_secret):
    status, data, error = post_json("/activations/poll", {
        "request_id": request_id,
        "machine_id": machine_id,
        "activation_secret": activation_secret,
    })

    if status == 200:
        return data, None

    return None, error or "Unable to poll activation."


def request_decrypt_key(activation_token, activation_secret, machine_id, product_slug):
    status, data, error = post_json("/activations/decrypt-key", {
        "activation_token": activation_token,
        "activation_secret": activation_secret,
        "machine_id": machine_id,
        "product_slug": product_slug,
    })

    if status == 200:
        return data, None, status

    return None, error or "Unable to fetch decrypt key.", status


def download_encrypted_lora(activation_token, activation_secret, machine_id, product_slug, lora_name):
    target_path = get_preferred_lora_download_path(lora_name)
    target_dir = os.path.dirname(target_path)
    body = json.dumps({
        "activation_token": activation_token,
        "activation_secret": activation_secret,
        "machine_id": machine_id,
        "product_slug": product_slug,
    }).encode("utf8")
    request = urllib.request.Request(
        f"{API_BASE}/activations/lora",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/octet-stream",
            "User-Agent": "ALTERA2ION-ComfyUI/2.1",
        },
        method="POST",
    )
    handle = None
    temp_path = None

    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            handle = tempfile.NamedTemporaryFile(
                "wb",
                dir=target_dir,
                prefix=".tmp-altera2ion-lora-",
                suffix=".a2enc",
                delete=False,
            )
            temp_path = handle.name

            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)

            handle.flush()
            handle.close()
            os.replace(temp_path, target_path)
            return target_path, None, response.status
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf8", errors="replace")
        try:
            data = json.loads(body_text) if body_text else {}
        except Exception:
            data = {"error": body_text}
        message = data.get("error") or body_text or f"HTTP {error.code}"
        return None, message, error.code
    except Exception as error:
        return None, f"Connection error: {error}", None
    finally:
        if handle and not handle.closed:
            handle.close()
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def decrypt_lora_bytes(enc_path, key_bytes):
    fernet = Fernet(key_bytes)

    with open(enc_path, "rb") as handle:
        chunk_count = int.from_bytes(handle.read(4), "big")
        parts = []
        for _index in range(chunk_count):
            size = int.from_bytes(handle.read(4), "big")
            parts.append(fernet.decrypt(handle.read(size)))

    return b"".join(parts)


def maybe_open_browser(url, state):
    last_opened_at = float(state.get("pending_browser_opened_at") or 0)

    if last_opened_at and (now_timestamp() - last_opened_at) < 10:
        return

    try:
        webbrowser.open(url)
    except Exception:
        pass

    state["pending_browser_opened_at"] = now_timestamp()


def wait_for_activation(state, machine_id, activation_secret):
    request_id = state.get("pending_request_id")

    if not request_id:
        return None, "Activation request is missing."

    deadline = now_timestamp() + ACTIVATION_WAIT_SECONDS

    while now_timestamp() < deadline:
        payload, error = poll_activation(request_id, machine_id, activation_secret)

        if error:
            return None, error

        status = payload.get("status")

        if status == "approved":
            state["activation_token"] = payload.get("token", "")
            state["activation_token_expires_at"] = payload.get("expires_at", "")
            state.pop("pending_request_id", None)
            state.pop("pending_request_expires_at", None)
            state.pop("pending_activation_url", None)
            state.pop("pending_browser_opened_at", None)
            return payload.get("token"), None

        if status in ("expired", "revoked", "denied"):
            clear_product_state(state)
            return None, f"Activation request ended with status: {status}"

        time.sleep(ACTIVATION_POLL_INTERVAL_SECONDS)

    return None, "Activation is still pending."


def get_valid_cached_decrypt_key(state):
    if state.get("decrypt_key") and is_future_timestamp(state.get("decrypt_key_expires_at")):
        return state["decrypt_key"]

    return None


def exchange_activation_for_decrypt_key(state, activation_secret, machine_id, product_slug):
    activation_token = state.get("activation_token")

    if not activation_token:
        return None, "Activation token is missing."

    payload, error, status = request_decrypt_key(
        activation_token,
        activation_secret,
        machine_id,
        product_slug,
    )

    if payload:
        state["decrypt_key"] = payload.get("decrypt_key", "")
        expires_at = payload.get("expires_at", "")

        if expires_at:
            state["decrypt_key_expires_at"] = parse_iso_timestamp(expires_at) or (
                now_timestamp() + (DECRYPT_KEY_OFFLINE_GRACE_HOURS * 3600)
            )
        else:
            state["decrypt_key_expires_at"] = now_timestamp() + (DECRYPT_KEY_OFFLINE_GRACE_HOURS * 3600)

        return state["decrypt_key"], None

    if status in (403, 404, 410):
        clear_product_state(state)

    return None, error


def activate_and_get_key(config, product_slug, require_activation_token=False):
    machine_id, machine_name, activation_secret = ensure_machine_state(config)
    state = get_product_state(config, product_slug)

    cached_key = get_valid_cached_decrypt_key(state)
    if cached_key and (not require_activation_token or has_valid_activation_token(state)):
        return cached_key.encode("utf8")

    if state.get("activation_token"):
        decrypt_key, error = exchange_activation_for_decrypt_key(
            state,
            activation_secret,
            machine_id,
            product_slug,
        )
        if decrypt_key:
            return decrypt_key.encode("utf8")
        if error and "Connection error:" in error:
            raise RuntimeError(
                "[ALTERA2ION] Unable to refresh the decrypt key and the offline grace period has expired. "
                "Reconnect to the internet and run again."
            )

    pending_request_id = state.get("pending_request_id")
    if pending_request_id and is_future_timestamp(state.get("pending_request_expires_at")):
        activation_url = state.get("pending_activation_url")
        if activation_url:
            maybe_open_browser(activation_url, state)
        token, error = wait_for_activation(state, machine_id, activation_secret)
        if token:
            decrypt_key, decrypt_error = exchange_activation_for_decrypt_key(
                state,
                activation_secret,
                machine_id,
                product_slug,
            )
            if decrypt_key:
                return decrypt_key.encode("utf8")
            raise RuntimeError(f"[ALTERA2ION] Activation succeeded but decrypt key exchange failed: {decrypt_error}")

        save_config(config)
        raise RuntimeError(
            "[ALTERA2ION] Activation is pending. Approve this workstation in your browser and run again."
            + (f" {error}" if error else "")
        )

    activation_payload, error = request_activation(
        product_slug,
        machine_id,
        machine_name,
        activation_secret,
    )

    if not activation_payload:
        raise RuntimeError(f"[ALTERA2ION] Unable to start activation: {error}")

    state["pending_request_id"] = activation_payload.get("request_id", "")
    state["pending_request_expires_at"] = parse_iso_timestamp(activation_payload.get("expires_at")) or (
        now_timestamp() + (15 * 60)
    )
    state["pending_activation_url"] = activation_payload.get("activation_url", "")
    maybe_open_browser(state["pending_activation_url"], state)
    save_config(config)

    token, wait_error = wait_for_activation(state, machine_id, activation_secret)
    if token:
        decrypt_key, decrypt_error = exchange_activation_for_decrypt_key(
            state,
            activation_secret,
            machine_id,
            product_slug,
        )
        if decrypt_key:
            save_config(config)
            return decrypt_key.encode("utf8")
        raise RuntimeError(f"[ALTERA2ION] Activation succeeded but decrypt key exchange failed: {decrypt_error}")

    save_config(config)
    raise RuntimeError(
        "[ALTERA2ION] Browser approval is required before this LoRA can load. "
        "Finish activation and run again."
        + (f" {wait_error}" if wait_error else "")
    )


def ensure_encrypted_lora_file(config, product_slug, lora_name):
    existing_path = find_existing_lora_path(lora_name)

    if existing_path:
        return existing_path

    machine_id, _machine_name, activation_secret = ensure_machine_state(config)
    state = get_product_state(config, product_slug)
    activation_token = state.get("activation_token")

    if not activation_token or not has_valid_activation_token(state):
        raise RuntimeError("[ALTERA2ION] Activation is required before this LoRA can be downloaded.")

    lora_path, error, status = download_encrypted_lora(
        activation_token,
        activation_secret,
        machine_id,
        product_slug,
        lora_name,
    )

    if lora_path:
        return lora_path

    if status in (403, 404, 410):
        clear_product_state(state)

    raise RuntimeError(f"[ALTERA2ION] Unable to download encrypted LoRA: {error}")


class Altera2ionLoRALoader:
    CATEGORY = "ALTERA2ION"
    FUNCTION = "load_lora"
    RETURN_TYPES = ("MODEL", "CLIP")
    RETURN_NAMES = ("model", "clip")

    @classmethod
    def INPUT_TYPES(cls):
        enc_files = get_lora_options()

        return {
            "required": {
                "model": ("MODEL",),
                "clip": ("CLIP",),
                "lora_name": (sorted(enc_files), {"default": sorted(enc_files)[0]}),
                "strength_model": ("FLOAT", {
                    "default": 1.0, "min": -10.0, "max": 10.0, "step": 0.01
                }),
                "strength_clip": ("FLOAT", {
                    "default": 1.0, "min": -10.0, "max": 10.0, "step": 0.01
                }),
            }
        }

    def load_lora(self, model, clip, lora_name, strength_model, strength_clip):
        lora_name = sanitize_lora_name(lora_name)
        product_slug = infer_product_slug(lora_name)
        enc_path = find_existing_lora_path(lora_name)
        config = load_config()
        decrypt_key = activate_and_get_key(config, product_slug, require_activation_token=not bool(enc_path))
        enc_path = ensure_encrypted_lora_file(config, product_slug, lora_name)
        save_config(config)

        try:
            raw_bytes = decrypt_lora_bytes(enc_path, decrypt_key)
        except Exception as error:
            raise RuntimeError(
                f"[ALTERA2ION] Decryption failed - invalid activation or corrupted file. {error}"
            )

        from safetensors.torch import load as safetensors_load

        lora_data = safetensors_load(raw_bytes)

        import comfy.lora
        import comfy.sd

        if hasattr(comfy.sd, "load_lora_for_models"):
            new_model, new_clip = comfy.sd.load_lora_for_models(
                model,
                clip,
                lora_data,
                strength_model,
                strength_clip,
            )
        else:
            key_map = comfy.lora.model_lora_keys_unet(model.model, {})
            key_map.update(comfy.lora.model_lora_keys_clip(clip.cond_stage_model, {}))

            lora_converted = comfy.lora.load_lora(lora_data, key_map)

            new_model = model.clone()
            new_clip = clip.clone()

            comfy.lora.apply_lora(new_model, lora_converted, strength_model)
            comfy.lora.apply_lora_clip(new_clip, lora_converted, strength_clip)

        print(
            f"[ALTERA2ION] Loaded {lora_name} "
            f"(product: {product_slug}, model: {strength_model}, clip: {strength_clip})"
        )

        return (new_model, new_clip)


NODE_CLASS_MAPPINGS = {
    "Altera2ionLoRALoader": Altera2ionLoRALoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Altera2ionLoRALoader": "ALTERA2ION LoRA Loader",
}
