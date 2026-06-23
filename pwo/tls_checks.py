import asyncio
import datetime
import socket
import ssl

from cryptography import x509


def _check_tls_sync(domain: str, timeout: int) -> dict:
    result: dict = {
        "tls_valid": False,
        "tls_error": None,
        "tls_issuer": None,
        "tls_not_before": None,
        "tls_not_after": None,
        "tls_days_until_expiry": None,
    }
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert_der = ssock.getpeercert(binary_form=True)

        cert = x509.load_der_x509_certificate(cert_der)

        issuer_attrs = cert.issuer.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
        result["tls_issuer"] = issuer_attrs[0].value if issuer_attrs else None

        not_before = cert.not_valid_before_utc
        not_after = cert.not_valid_after_utc
        now = datetime.datetime.now(datetime.timezone.utc)

        result["tls_not_before"] = not_before.isoformat()
        result["tls_not_after"] = not_after.isoformat()
        result["tls_days_until_expiry"] = (not_after - now).days
        result["tls_valid"] = True

    except ssl.SSLCertVerificationError as e:
        result["tls_error"] = f"cert_verification: {e}"
    except ssl.SSLError as e:
        result["tls_error"] = f"ssl_error: {e}"
    except socket.timeout:
        result["tls_error"] = "timeout"
    except OSError as e:
        result["tls_error"] = f"connection_error: {e}"
    except Exception as e:
        result["tls_error"] = str(e)[:200]

    return result


async def check_tls(domain: str, timeout: int = 15) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check_tls_sync, domain, timeout)
