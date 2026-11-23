"""Exercise the Garnet image on TLS, reusing its filesystem across restarts."""

import os
from pathlib import Path
import socket
import ssl
import subprocess
from tempfile import TemporaryDirectory
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "soenneker-cloudflare-garnet:cache-verify"
SERVER = f"garnet-cache-test-{uuid.uuid4()}"
PASSWORD = 'cache fixture "with" \\ punctuation!'


def docker(*args, check=True, env=None):
    result = subprocess.run(["docker", *args], cwd=ROOT, env=env, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result


def command(stream, *args):
    parts = [part.encode() for part in args]
    stream.write(f"*{len(parts)}\r\n".encode() + b"".join(
        f"${len(part)}\r\n".encode() + part + b"\r\n" for part in parts))
    return stream.readline().decode().strip()


def main():
    docker("build", "-t", IMAGE, "container")
    with TemporaryDirectory() as tmp:
        certs = Path(tmp)

        def openssl(*args):
            subprocess.run(["openssl", *args], cwd=certs, capture_output=True, check=True)

        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Test Root",
                "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign",
                "-addext", "subjectKeyIdentifier=hash", "-keyout", "root.key", "-out", "root.pem")
        for name, issuer, constraints in (
                ("issuer", "root", "basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign"),
                ("localhost", "issuer", "basicConstraints=CA:FALSE\nsubjectAltName=DNS:localhost")):
            openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=" + name,
                    "-keyout", name + ".key", "-out", name + ".csr")
            (certs / "extensions").write_text(constraints + "\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n")
            openssl("x509", "-req", "-in", name + ".csr", "-CA", issuer + ".pem", "-CAkey", issuer + ".key",
                    "-CAcreateserial", "-days", "1", "-extfile", "extensions", "-out", name + ".pem")
        pem, key = certs / "localhost.pem", certs / "localhost.key"
        ctx = ssl.create_default_context(cafile=str(certs / "root.pem"))
        env = {**os.environ, "TLS_CERT_PEM": pem.read_text() + (certs / "issuer.pem").read_text(), "TLS_KEY_PEM": key.read_text(),
               "GARNET_PASSWORD": PASSWORD}
        try:
            docker("run", "-d", "--name", SERVER, "--tmpfs", "/run", "-p", "127.0.0.1::6379",
                   "-e", "GARNET_PASSWORD", "-e", "TLS_CERT_PEM", "-e", "TLS_KEY_PEM", IMAGE, env=env)
            for stop in (None, "stop", "kill"):
                if stop:
                    docker(stop, SERVER)
                    docker("start", SERVER)
                docker("exec", SERVER, "/bin/sh", "/usr/local/bin/wait-garnet.sh")
                port = int(docker("port", SERVER, "6379/tcp").stdout.strip().split(":")[-1])
                with socket.create_connection(("127.0.0.1", port), timeout=3) as tcp:
                    with ctx.wrap_socket(tcp, server_hostname="localhost") as tls:
                        with tls.makefile("rwb", buffering=0) as stream:
                            assert command(stream, "GET", "dotnet").startswith("-NOAUTH")
                            assert command(stream, "AUTH", "wrong").startswith("-")
                            assert command(stream, "AUTH", PASSWORD) == "+OK"
                            assert command(stream, "EXISTS", "dotnet") == ":0"
                            assert command(stream, "SET", "dotnet", "marker") == "+OK"
                            if stop is None:
                                assert command(stream, "DEL", "dotnet") == ":1"
                                reply = command(stream, "XADD", "stream-test", "*", "field", "value")
                                assert "unknown command" in reply, "Recheck documented Streams support"
                if stop is None:
                    subprocess.run(["dotnet", "run", "--file", "test/RedisClient.cs"], cwd=ROOT,
                                   env={**os.environ, "TEST_REDIS_PORT": str(port),
                                        "TEST_TLS_CERT": pem.read_text(), "TEST_REDIS_PASSWORD": PASSWORD},
                                   check=True, timeout=120)
            print("Verified TLS, authentication, application commands and empty starts after SIGTERM and SIGKILL.")
        except BaseException:
            result = docker("logs", "--tail", "20", SERVER, check=False)
            print(result.stdout, result.stderr)
            raise
        finally:
            docker("rm", "-fv", SERVER, check=False)


if __name__ == "__main__":
    main()
