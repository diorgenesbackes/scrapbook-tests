import argparse
from http import HTTPStatus
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener


class HlgVerificationError(Exception):
    pass


class DirectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_origin(value):
    parsed = urlsplit(value)
    if not re.fullmatch(r"https://[a-z0-9][a-z0-9.-]+\.[a-z][a-z0-9-]+", value) or not parsed.hostname:
        raise argparse.ArgumentTypeError("Use uma origem HTTPS sem caminho, credenciais ou porta.")
    return value


def request(url):
    opener = build_opener(DirectRedirectHandler())
    try:
        with opener.open(url, timeout=10) as response:
            return response.status, response.headers, response.read(2 * 1024 * 1024)
    except HTTPError as error:
        with error:
            return error.code, error.headers, b""


def verify_hlg(*, ui, api, sha, version, fetch=request):
    for path in ("/", "/meus-eventos"):
        status, headers, body = fetch(ui + path)
        directives = [directive.strip().split() for directive in headers.get("Content-Security-Policy", "").split(";")]
        if status != HTTPStatus.OK or not headers.get("Content-Type", "").startswith("text/html"):
            raise HlgVerificationError(f"O shell da UI não respondeu com HTML em {path}.")
        if (
            not body
            or ["connect-src", "'self'", api] not in directives
            or ["frame-ancestors", "'none'"] not in directives
            or headers.get("X-Content-Type-Options") != "nosniff"
            or headers.get("Referrer-Policy") != "no-referrer"
        ):
            raise HlgVerificationError(f"O shell e seus cabeçalhos divergiram em {path}.")
    status, _, body = fetch(ui + "/release.json")
    if status != HTTPStatus.OK or json.loads(body) != {"sha": sha, "version": version}:
        raise HlgVerificationError("O artefato ativo da UI difere do candidato.")
    for path in ("/health", "/health/ready"):
        status, _, _ = fetch(api + path)
        if status not in (HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND):
            raise HlgVerificationError(f"A rota operacional {path} está acessível ao público.")


def main():
    parser = argparse.ArgumentParser(description="Confere o shell HTTPS publicado e as rotas operacionais.")
    parser.add_argument("--ui", type=https_origin, required=True)
    parser.add_argument("--api", type=https_origin, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if args.ui == args.api:
        parser.error("Configure origens distintas para UI e API.")
    try:
        verify_hlg(**vars(args))
    except HlgVerificationError as error:
        parser.exit(1, f"{error}\n")
    except (URLError, TimeoutError, ValueError):
        parser.exit(1, "Resposta HTTPS indisponível ou incompatível com o candidato.\n")
    print("Shell HTTPS, revisão da UI e restrição das rotas operacionais verificados.")


if __name__ == "__main__":
    main()
