"""Every module imports. A SyntaxError in a stage nothing else imports
reached the user's machine once; this is the cheap guard."""

import importlib
import pkgutil

import tube_auto
import tube_auto.stages


def test_every_module_imports():
    failures = []
    for pkg in (tube_auto, tube_auto.stages):
        for info in pkgutil.iter_modules(pkg.__path__):
            name = f"{pkg.__name__}.{info.name}"
            if name.endswith("__main__"):
                continue
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}: {exc}")
    assert not failures, "\n".join(failures)
