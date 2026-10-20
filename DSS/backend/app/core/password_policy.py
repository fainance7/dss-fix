"""Требования к паролю (аудит безопасности, L-1): единые для создания пользователя,
смены пароля администратором и самим пользователем."""
import re

MIN_LENGTH = 10


def password_problem(password: str, username: str | None = None) -> str | None:
    """Возвращает описание нарушения или None, если пароль подходит."""
    if len(password) < MIN_LENGTH:
        return f"Пароль должен быть не короче {MIN_LENGTH} символов"
    if not re.search(r"[A-Za-zА-Яа-яЁё]", password) or not re.search(r"\d", password):
        return "Пароль должен содержать буквы и цифры"
    if username and username.lower() in password.lower():
        return "Пароль не должен содержать имя пользователя"
    return None
