# mookquant * PyTorch model architecture registry
# Experimental: register model architectures via decorator.
# Upper layers build instances via build_model(arch, params).

from typing import Dict, Type, List

_MODEL_REGISTRY: Dict[str, Type] = {}


def register_model(name: str):
    # Decorator: register a model architecture.
    def decorator(cls):
        _MODEL_REGISTRY[name] = cls
        return cls
    return decorator


def get_model_class(name: str) -> Type:
    # Get model architecture class. Raises KeyError if not registered.
    if name not in _MODEL_REGISTRY:
        raise KeyError(name)
    return _MODEL_REGISTRY[name]


def list_models() -> List[str]:
    # List all registered model architecture names.
    return list(_MODEL_REGISTRY.keys())


def build_model(arch: str, params: dict):
    # Build a model instance from architecture name and params.
    cls = get_model_class(arch)
    return cls(**params)
