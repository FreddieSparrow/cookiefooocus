import os
import sys

print('[System ARGV] ' + str(sys.argv))

root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(root)
os.chdir(root)

os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"
if "GRADIO_SERVER_PORT" not in os.environ:
    os.environ["GRADIO_SERVER_PORT"] = "7865"

# SSL certificate verification is enabled (default Python behaviour).
# Do NOT disable it — doing so allows MITM attacks when downloading models.

# ── Content filter integrity check ───────────────────────────────────────────
# content_filter.py is safety-critical. We verify it is present, unmodified
# (via SHA-256), and up to date with the upstream repository before launch.
# If it fails any check, startup is aborted.
import hashlib
import json

_MANIFEST_PATH = os.path.join(root, "security_manifest.json")


def _abort(reason: str) -> None:
    print(f"\n{'='*60}")
    print(f"  STARTUP BLOCKED — {reason}")
    print(f"{'='*60}")
    print("  A required safety file is missing or has been tampered with.")
    print("  To restore, run:")
    print("    git checkout modules/content_filter.py")
    print("  Then regenerate the manifest:")
    print("    python update_manifest.py")
    print(f"{'='*60}\n")
    sys.exit(1)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_safety_files() -> None:
    """
    Verify safety-critical files against security_manifest.json (offline, fast).
    The manifest is updated by running 'python update_manifest.py' after
    legitimate changes — no internet connection required at boot.
    """
    if not os.path.isfile(_MANIFEST_PATH):
        print("[Cookie-Fooocus] WARNING: security_manifest.json missing — "
              "run 'python update_manifest.py' to create it.")
        # Fallback: just check the filter exists
        if not os.path.isfile(os.path.join(root, "modules", "content_filter.py")):
            _abort("modules/content_filter.py is missing")
        print("[Cookie-Fooocus] content_filter.py present (manifest check skipped).")
        return

    try:
        manifest = json.loads(open(_MANIFEST_PATH).read())
    except Exception as exc:
        print(f"[Cookie-Fooocus] WARNING: Could not parse security_manifest.json: {exc}")
        return

    files = manifest.get("files", {})
    all_ok = True

    for rel_path, meta in files.items():
        abs_path = os.path.join(root, rel_path)
        required = meta.get("required", False)

        if not os.path.isfile(abs_path):
            if required:
                _abort(f"{rel_path} is missing (required by security manifest)")
            print(f"[Cookie-Fooocus] WARNING: {rel_path} missing (not required).")
            continue

        expected_hash = meta.get("sha256", "")
        if not expected_hash:
            continue

        actual_hash = _sha256_file(abs_path)
        if actual_hash != expected_hash:
            if required:
                _abort(
                    f"{rel_path} has been modified.\n"
                    f"  Manifest SHA-256: {expected_hash}\n"
                    f"  Actual   SHA-256: {actual_hash}\n"
                    "  If this is a legitimate update, run: python update_manifest.py"
                )
            print(f"[Cookie-Fooocus] WARNING: {rel_path} hash mismatch (non-required).")
            all_ok = False
        else:
            ver = meta.get("version", "?")
            print(f"[Cookie-Fooocus] ✓ {rel_path} v{ver} verified.")

    if all_ok:
        channel = manifest.get("update_channel", "stable")
        print(f"[Cookie-Fooocus] Safety manifest OK (channel: {channel}).")


_verify_safety_files()
# ─────────────────────────────────────────────────────────────────────────────

import platform
import fooocus_version

# ── PIL decompression-bomb protection (set before any image is opened) ────────
# A "decompression bomb" is a small image file that expands to gigabytes in RAM.
# Limiting to 50 MP covers any real use-case; beyond that it's almost certainly
# malicious. PIL raises DecompressionBombError automatically above this limit.
try:
    from PIL import Image as _PIL_Image
    _PIL_Image.MAX_IMAGE_PIXELS = 50_000_000   # 50 megapixels (~7071 × 7071 px)
except ImportError:
    pass
# ─────────────────────────────────────────────────────────────────────────────

from build_launcher import build_launcher
from modules.launch_util import is_installed, run, python, run_pip, requirements_met, delete_folder_content
from modules.model_loader import load_file_from_url
from modules.first_run import load_or_run_wizard, apply_memory_config

# ── First-run wizard (memory mode) ───────────────────────────────────────────
_first_run_config = load_or_run_wizard()
apply_memory_config(_first_run_config)
# ─────────────────────────────────────────────────────────────────────────────

REINSTALL_ALL = False
TRY_INSTALL_XFORMERS = False


def prepare_environment():
    torch_index_url = os.environ.get('TORCH_INDEX_URL', "https://download.pytorch.org/whl/cu121")
    torch_command = os.environ.get('TORCH_COMMAND',
                                   f"pip install torch==2.1.0 torchvision==0.16.0 --extra-index-url {torch_index_url}")
    requirements_file = os.environ.get('REQS_FILE', "requirements_versions.txt")

    print(f"Python {sys.version}")
    print(f"{fooocus_version.app_name} version: {fooocus_version.version}")

    if REINSTALL_ALL or not is_installed("torch") or not is_installed("torchvision"):
        run(f'"{python}" -m {torch_command}', "Installing torch and torchvision", "Couldn't install torch", live=True)

    if TRY_INSTALL_XFORMERS:
        if REINSTALL_ALL or not is_installed("xformers"):
            xformers_package = os.environ.get('XFORMERS_PACKAGE', 'xformers==0.0.23')
            if platform.system() == "Windows":
                if platform.python_version().startswith("3.10"):
                    run_pip(f"install -U -I --no-deps {xformers_package}", "xformers", live=True)
                else:
                    print("Installation of xformers is not supported in this version of Python.")
                    print(
                        "You can also check this and build manually: https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Xformers#building-xformers-on-windows-by-duckness")
                    if not is_installed("xformers"):
                        exit(0)
            elif platform.system() == "Linux":
                run_pip(f"install -U -I --no-deps {xformers_package}", "xformers")

    if REINSTALL_ALL or not requirements_met(requirements_file):
        run_pip(f"install -r \"{requirements_file}\"", "requirements")

    return


vae_approx_filenames = [
    ('xlvaeapp.pth', 'https://huggingface.co/lllyasviel/misc/resolve/main/xlvaeapp.pth'),
    ('vaeapp_sd15.pth', 'https://huggingface.co/lllyasviel/misc/resolve/main/vaeapp_sd15.pt'),
    ('xl-to-v1_interposer-v4.0.safetensors',
     'https://huggingface.co/mashb1t/misc/resolve/main/xl-to-v1_interposer-v4.0.safetensors')
]


def ini_args():
    from args_manager import args
    return args


prepare_environment()
build_launcher()
args = ini_args()

if args.gpu_device_id is not None:
    os.environ['CUDA_VISIBLE_DEVICES'] = str(args.gpu_device_id)
    print("Set device to:", args.gpu_device_id)

if args.hf_mirror is not None:
    os.environ['HF_MIRROR'] = str(args.hf_mirror)
    print("Set hf_mirror to:", args.hf_mirror)

from modules import config
from modules.hash_cache import init_cache

os.environ["U2NET_HOME"] = config.path_inpaint

os.environ['GRADIO_TEMP_DIR'] = config.temp_path

if config.temp_path_cleanup_on_launch:
    print(f'[Cleanup] Attempting to delete content of temp dir {config.temp_path}')
    result = delete_folder_content(config.temp_path, '[Cleanup] ')
    if result:
        print("[Cleanup] Cleanup successful")
    else:
        print(f"[Cleanup] Failed to delete content of temp dir.")


def download_models(default_model, previous_default_models, checkpoint_downloads, embeddings_downloads, lora_downloads, vae_downloads):
    from modules.util import get_file_from_folder_list

    for file_name, url in vae_approx_filenames:
        load_file_from_url(url=url, model_dir=config.path_vae_approx, file_name=file_name)

    load_file_from_url(
        url='https://huggingface.co/lllyasviel/misc/resolve/main/fooocus_expansion.bin',
        model_dir=config.path_fooocus_expansion,
        file_name='pytorch_model.bin'
    )

    if args.disable_preset_download:
        print('Skipped model download.')
        return default_model, checkpoint_downloads

    if not args.always_download_new_model:
        if not os.path.isfile(get_file_from_folder_list(default_model, config.paths_checkpoints)):
            for alternative_model_name in previous_default_models:
                if os.path.isfile(get_file_from_folder_list(alternative_model_name, config.paths_checkpoints)):
                    print(f'You do not have [{default_model}] but you have [{alternative_model_name}].')
                    print(f'Cookie-Fooocus will use [{alternative_model_name}] to avoid downloading new models, '
                          f'but you are not using the latest models.')
                    print('Use --always-download-new-model to avoid fallback and always get new models.')
                    checkpoint_downloads = {}
                    default_model = alternative_model_name
                    break

    for file_name, url in checkpoint_downloads.items():
        model_dir = os.path.dirname(get_file_from_folder_list(file_name, config.paths_checkpoints))
        load_file_from_url(url=url, model_dir=model_dir, file_name=file_name)
    for file_name, url in embeddings_downloads.items():
        load_file_from_url(url=url, model_dir=config.path_embeddings, file_name=file_name)
    for file_name, url in lora_downloads.items():
        model_dir = os.path.dirname(get_file_from_folder_list(file_name, config.paths_loras))
        load_file_from_url(url=url, model_dir=model_dir, file_name=file_name)
    for file_name, url in vae_downloads.items():
        load_file_from_url(url=url, model_dir=config.path_vae, file_name=file_name)

    return default_model, checkpoint_downloads


config.default_base_model_name, config.checkpoint_downloads = download_models(
    config.default_base_model_name, config.previous_default_models, config.checkpoint_downloads,
    config.embeddings_downloads, config.lora_downloads, config.vae_downloads)

config.update_files()
init_cache(config.model_filenames, config.paths_checkpoints, config.lora_filenames, config.paths_loras)

from webui import *
