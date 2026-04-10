import pickle

load = pickle.load

# Modules that are safe to deserialise from legacy .pt/.pth checkpoints.
# Everything else is hard-blocked to prevent arbitrary code execution via
# malicious model files (pickle RCE / supply-chain attacks).
_SAFE_MODULES = frozenset({
    "torch",
    "torch.storage",
    "_codecs",
    "collections",
    "numpy",
    "numpy.core.multiarray",
})

# A sentinel class returned for any pytorch_lightning reference so that
# legacy Lightning checkpoints can still load their state-dicts.
class Empty:
    pass


class Unpickler(pickle.Unpickler):
    def find_class(self, module, name):
        # Allow pytorch_lightning stubs (state-dict wrappers only)
        if module.startswith("pytorch_lightning"):
            return Empty

        # Allowlist check: only permit known-safe modules
        root = module.split(".")[0]
        if root not in _SAFE_MODULES:
            raise pickle.UnpicklingError(
                f"Blocked unsafe pickle class: {module}.{name}. "
                "Use .safetensors format for model files."
            )

        return super().find_class(module, name)
