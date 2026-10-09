"""Generate a self-signed TLS certificate for local testing (needs `pip install cryptography`).

    python gen_cert.py [extra-hostname ...]

Browsers will warn about a self-signed cert; that's expected for a POC.
"""
import datetime
import ipaddress
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_CERT = BASE / "certs" / "cert.pem"
DEFAULT_KEY = BASE / "certs" / "key.pem"


def ensure_cert(cert_path: Path = DEFAULT_CERT, key_path: Path = DEFAULT_KEY, extra_names: tuple[str, ...] = ()) -> None:
    """Create cert/key files if either is missing."""
    if cert_path.exists() and key_path.exists():
        return
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "TLS cert not found and the 'cryptography' package is missing.\n"
            "Run: pip install cryptography   (or supply --cert/--key)"
        ) from exc

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    sans: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    for host in extra_names:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            sans.append(x509.DNSName(host))

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


if __name__ == "__main__":
    ensure_cert(extra_names=tuple(sys.argv[1:]))
    print(f"Certificate: {DEFAULT_CERT}\nPrivate key: {DEFAULT_KEY}")
