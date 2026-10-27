"""Guards on the package surface itself.

`__all__` is a promise to every importer. A name listed there that does not
exist only fails at the moment someone writes `from app.engine import *`, which
is late and confusing, so it is checked here instead.
"""

import app.engine as engine


def test_every_exported_name_actually_exists():
    missing = [name for name in engine.__all__ if not hasattr(engine, name)]
    assert missing == [], f"__all__ lists names that do not exist: {missing}"


def test_star_import_succeeds():
    namespace = {}
    exec("from app.engine import *", namespace)

    for name in engine.__all__:
        assert name in namespace
