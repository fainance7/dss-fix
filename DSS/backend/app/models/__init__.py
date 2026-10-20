"""Re-exports моделей. ApiKey импортируется здесь, чтобы Base.metadata знал о таблице
при тестовом ``Base.metadata.create_all`` (conftest), без явного импорта в каждом тесте."""
from app.models.api_key import ApiKey  # noqa: F401

__all__ = ["ApiKey"]
