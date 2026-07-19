"""Генерация самоподписанного сертификата для HTTPS на LAN-IP.
iOS Safari даёт доступ к гироскопу/камере только в secure context (HTTPS с доверенным
сертификатом). Этот cert телефон установит и пометит доверенным один раз.
"""
import datetime
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def lan_ip() -> str:
    """LAN-адрес этой машины. Сокет никуда не шлёт — нужен только для выбора интерфейса."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# Сертификат привязан к IP, поэтому определяем его сам. Можно задать явно:
#   python pc/make_cert.py 192.168.1.42
IP = sys.argv[1] if len(sys.argv) > 1 else lan_ip()
OUT = Path(__file__).resolve().parent.parent / "certs"
OUT.mkdir(exist_ok=True)

now = datetime.datetime.now(datetime.timezone.utc)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"VRBox Desktop {IP}")])

cert = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(days=1))
    .not_valid_after(now + datetime.timedelta(days=820))  # < 825 (лимит Apple)
    .add_extension(
        x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(IP))]),
        critical=False,
    )
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .add_extension(
        x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
    )
    .sign(key, hashes.SHA256())
)

(OUT / "key.pem").write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
)
(OUT / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
print("сертификат создан в", OUT, "(cert.pem + key.pem) для IP", IP)
