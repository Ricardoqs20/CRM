import base64
import re
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.db import models

ENC_PREFIX = 'enc::'
_HKDF_SALT = b'imobicrm-field-encryption-salt-v1'
_HKDF_INFO = b'imobicrm-fernet-at-rest-key'


def _derive_fernet_key(secret_material: str) -> bytes:
    """Deriva uma chave Fernet URL-safe de 32 bytes via HKDF-SHA256."""
    if not secret_material:
        raise ValueError('Material criptográfico vazio para derivação HKDF.')
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    raw_key = hkdf.derive(secret_material.encode('utf-8'))
    return base64.urlsafe_b64encode(raw_key)


def _get_fernet_instances() -> list[Fernet]:
    """
    Retorna a instância primária do Fernet derivada de FIELD_ENCRYPTION_KEY (se configurada)
    ou SECRET_KEY, mantendo fallback secundário em SECRET_KEY para rotação suave de chaves.
    """
    field_key = (getattr(settings, 'FIELD_ENCRYPTION_KEY', '') or '').strip()
    secret_key = (getattr(settings, 'SECRET_KEY', '') or '').strip()

    instances = []
    if field_key:
        instances.append(Fernet(_derive_fernet_key(field_key)))
    if secret_key and secret_key != field_key:
        instances.append(Fernet(_derive_fernet_key(secret_key)))
    return instances


def is_encrypted(val) -> bool:
    """Verifica se a string já possui o prefixo de criptografia em repouso."""
    return isinstance(val, str) and val.startswith(ENC_PREFIX)


def encrypt_value(val):
    """
    Criptografa um valor textual usando Fernet (AES-128-CBC + HMAC-SHA256).
    - Preserva None e strings vazias.
    - Idempotente: não criptografa novamente valores já criptografados.
    """
    if val is None or val == '':
        return val

    text = str(val)
    if is_encrypted(text):
        return text

    fernets = _get_fernet_instances()
    if not fernets:
        return text

    token = fernets[0].encrypt(text.encode('utf-8')).decode('utf-8')
    return f'{ENC_PREFIX}{token}'


def decrypt_value(val):
    """
    Descriptografa um valor cifrado por encrypt_value().
    - Suporta leitura retroativa transparente: caso o registro antigo no banco
      ainda esteja em texto plano, retorna o valor original sem erro.
    """
    if val is None or val == '':
        return val

    text = str(val)
    fernets = _get_fernet_instances()

    if text.startswith(ENC_PREFIX):
        raw_token = text[len(ENC_PREFIX):]
        for fernet in fernets:
            try:
                return fernet.decrypt(raw_token.encode('utf-8')).decode('utf-8')
            except InvalidToken:
                continue
        return text

    # Suporte caso exista token Fernet puro sem prefixo enc::
    if text.startswith('gAAAAA') and len(text) >= 80:
        for fernet in fernets:
            try:
                return fernet.decrypt(text.encode('utf-8')).decode('utf-8')
            except InvalidToken:
                continue

    # Texto plano legado: leitura retroativa transparente
    return text


def mask_document(val) -> str:
    """
    Mascara CPF/CNPJ ou documento pessoal para exibição segura em tela (LGPD).
    Exemplos:
      - CPF (11 dígitos): ***.456.789-**
      - CNPJ (14 dígitos): **.345.678/0001-**
    """
    plain = decrypt_value(val)
    if not plain:
        return ''
    plain_str = str(plain).strip()
    if not plain_str:
        return ''

    digits = re.sub(r'\D', '', plain_str)
    if len(digits) == 11:
        return f'***.{digits[3:6]}.{digits[6:9]}-**'
    if len(digits) == 14:
        return f'**.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-**'

    if len(plain_str) <= 4:
        return '*' * len(plain_str)
    return f"{'*' * (len(plain_str) - 4)}{plain_str[-4:]}"


def mask_email(val) -> str:
    """Mascara endereço de e-mail para exibição reduzida."""
    plain = decrypt_value(val)
    if not plain or '@' not in str(plain):
        return str(plain or '')
    local, domain = str(plain).split('@', 1)
    if len(local) <= 2:
        masked_local = local[:1] + '***'
    else:
        masked_local = local[:2] + '***'
    return f'{masked_local}@{domain}'


class EncryptedTextField(models.TextField):
    """Campo TextField com criptografia transparente em repouso via Fernet."""

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value)

    def to_python(self, value):
        val = super().to_python(value)
        return decrypt_value(val)

    def get_db_prep_save(self, value, connection):
        prepared = super().get_db_prep_save(value, connection=connection)
        return encrypt_value(prepared)


class EncryptedCharField(models.CharField):
    """Campo CharField com criptografia transparente em repouso via Fernet."""

    def from_db_value(self, value, expression, connection):
        return decrypt_value(value)

    def to_python(self, value):
        val = super().to_python(value)
        return decrypt_value(val)

    def get_db_prep_save(self, value, connection):
        prepared = super().get_db_prep_save(value, connection=connection)
        return encrypt_value(prepared)
