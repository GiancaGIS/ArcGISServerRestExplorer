from __future__ import annotations

import logging


SERVICE_NAME = "arcgis-server-rest-explorer"

try:
    import keyring

    KEYRING_AVAILABLE = True
except Exception:
    keyring = None
    KEYRING_AVAILABLE = False


def _account(connection_name: str) -> str:
    return connection_name.strip()


def _credential_account(connection_name: str, field: str) -> str:
    return f"{connection_name.strip()}:{field}"


def get_saved_token(connection_name: str) -> str:
    if not KEYRING_AVAILABLE or not connection_name.strip():
        return ""
    try:
        return keyring.get_password(SERVICE_NAME, _account(connection_name)) or ""
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not read token from keyring: %s", exc)
        return ""


def save_token(connection_name: str, token: str) -> bool:
    if not KEYRING_AVAILABLE or not connection_name.strip() or not token:
        return False
    try:
        keyring.set_password(SERVICE_NAME, _account(connection_name), token)
        return True
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not save token to keyring: %s", exc)
        return False


def delete_token(connection_name: str) -> None:
    if not KEYRING_AVAILABLE or not connection_name.strip():
        return
    try:
        keyring.delete_password(SERVICE_NAME, _account(connection_name))
    except Exception:
        pass


def get_saved_credentials(connection_name: str) -> tuple[str, str]:
    if not KEYRING_AVAILABLE or not connection_name.strip():
        return "", ""
    try:
        username = keyring.get_password(SERVICE_NAME, _credential_account(connection_name, "username")) or ""
        password = keyring.get_password(SERVICE_NAME, _credential_account(connection_name, "password")) or ""
        return username, password
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not read credentials from keyring: %s", exc)
        return "", ""


def save_credentials(connection_name: str, username: str, password: str) -> bool:
    if not KEYRING_AVAILABLE or not connection_name.strip() or not username or not password:
        return False
    try:
        keyring.set_password(SERVICE_NAME, _credential_account(connection_name, "username"), username)
        keyring.set_password(SERVICE_NAME, _credential_account(connection_name, "password"), password)
        return True
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not save credentials to keyring: %s", exc)
        return False


def delete_credentials(connection_name: str) -> None:
    if not KEYRING_AVAILABLE or not connection_name.strip():
        return
    for field in ("username", "password"):
        try:
            keyring.delete_password(SERVICE_NAME, _credential_account(connection_name, field))
        except Exception:
            pass
